"""Casos de uso da distribuição manual (Fase 2a).

Aprovar → publicação por grupo do nicho (`awaiting_link`) → links do gerador do ML colados
(`ready`) → revalidação do preço da oferta → o operador posta pelo celular e marca (`sent`).

Nenhuma transação fica aberta durante chamada ao ML: quem chama faz commit antes da rede.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.distribution import (
    DEDUP_WINDOW,
    MANUAL_TIER,
    ChannelPolicy,
    LinkError,
    PixError,
    PriorPost,
    Recheck,
    check_coupon,
    check_pix,
    flash_to_show,
    is_fresh,
    listing_discount,
    local_day_start,
    min_gap,
    offer_page_url,
    official_comparison,
    parse_affiliate_links,
    parse_brl,
    pix_to_show,
    recheck_offer,
    render_message,
    repeat_block_reason,
    seller_line,
    send_block_reason,
    units_to_show,
)
from app.integrations.mercadolivre import MercadoLivreClient, MercadoLivreError
from app.models import (
    CatalogProduct,
    Channel,
    MarketplaceSeller,
    Niche,
    OfferEvaluation,
    OperatorDecision,
    Publication,
)

PRIOR_LIMIT = 20
MAX_GROUPS = 20  # grupos escolhidos de uma vez (aprovar, enviar também, link avulso)


class DistributionError(Exception):
    """Ação recusada, com mensagem pronta para o operador."""


class PixRejectedError(DistributionError):
    """Pix ou preço com cupom fora da regra (menor que o preço, até 30% abaixo).

    `field`: "pix" ou "cupom", para o aviso certo na tela.
    """

    def __init__(self, message: str, *, field: str = "pix") -> None:
        super().__init__(message)
        self.field = field


# Ordem de saída das publicações prontas de um grupo: as que o operador posicionou primeiro, na
# ordem dele; depois as outras, por ordem de aprovação.
READY_ORDER = (
    Publication.queue_position.is_(None),
    Publication.queue_position,
    Publication.created_at,
    Publication.id,
)
QUEUE_STEP = 10


async def move_up(db: AsyncSession, *, publication_id: int, before_id: int | None) -> bool:
    """Leva a publicação para antes de `before_id` (ou para o topo). Só sobe, nunca desce.

    Renumera a fila pronta do grupo inteira (é curta): sem posição repetida nem buraco.
    """
    pub = await db.get(Publication, publication_id)
    if pub is None or pub.state != "ready":
        return False
    queue = list(
        (
            await db.execute(
                select(Publication)
                .where(Publication.channel_id == pub.channel_id, Publication.state == "ready")
                .order_by(*READY_ORDER)
                .limit(500)
            )
        ).scalars()
    )
    order = [p.id for p in queue]
    if pub.id not in order:
        return False
    current = order.index(pub.id)
    target = 0 if before_id is None else (order.index(before_id) if before_id in order else -1)
    if target < 0 or target >= current:
        return False  # alvo de outro grupo, ou seria descer
    order.remove(pub.id)
    order.insert(target, pub.id)
    by_id = {p.id: p for p in queue}
    for n, pid in enumerate(order, start=1):
        by_id[pid].queue_position = n * QUEUE_STEP
    return True


def policy_of(channel: Channel) -> ChannelPolicy:
    return ChannelPolicy(
        max_per_day=channel.max_per_day,
        min_interval=min_gap(
            channel.max_per_day, channel.window_start_hour, channel.window_end_hour
        ),
        window_start_hour=channel.window_start_hour,
        window_end_hour=channel.window_end_hour,
        paused=channel.paused,
    )


# --------------------------------------------------------------------------- aprovação


@dataclass
class ApprovalResult:
    created: int = 0
    auto_link: int = 0  # o worker gera o link (grupo com etiqueta)
    automatic: int = 0  # e vão sozinhas para o grupo
    skipped: list[str] | None = None
    no_channel: bool = False


async def publications_for_approval(
    db: AsyncSession,
    *,
    product: CatalogProduct,
    evaluation: OfferEvaluation,
    decision: OperatorDecision,
    now: datetime,
    pix_cents: int | None = None,
    affiliate_tool_id: str = "",
    coupon_cents: int | None = None,
    last_units: int | None = None,
    seen_at: datetime | None = None,
    key_tag: str = "",
    channels: list[Channel] | None = None,
    flash_ends_at: datetime | None = None,
) -> ApprovalResult:
    """Uma publicação por grupo do nicho. Repetida em 7 dias ou grupo pausado: `skipped`.

    `pix_cents`, `coupon_cents` e `last_units`: o que o operador viu na página ao aprovar (o Pix
    vale por 3 horas, as últimas unidades por 2). `seen_at`: quando viu (a troca de grupo
    mantém a hora da aprovação, para não esticar a validade). `key_tag`: separa a publicação de
    uma troca de grupo da aprovação original (a mesma avaliação pode voltar ao mesmo grupo).
    `channels`: grupos escolhidos pelo operador ("enviar também em"); sem isso, os do nicho.
    `flash_ends_at`: fim da oferta relâmpago que o operador viu na página.
    """
    seen = seen_at or now
    result = ApprovalResult(skipped=[])
    if evaluation.price_cents is None or product.niche_id is None:
        raise DistributionError("esta avaliação não tem preço válido para publicar")
    if pix_cents is not None:
        try:
            check_pix(pix_cents, evaluation.price_cents)
        except PixError as exc:
            raise PixRejectedError(str(exc), field="pix") from exc
    if coupon_cents is not None:
        try:
            check_coupon(coupon_cents, evaluation.price_cents)
        except PixError as exc:
            raise PixRejectedError(str(exc), field="cupom") from exc
    if channels is None:
        channels = list(
            (
                await db.execute(
                    select(Channel).where(Channel.niche_id == product.niche_id).order_by(Channel.id)
                )
            ).scalars()
        )
    if not channels:
        result.no_channel = True
        return result
    snapshot = json.loads(evaluation.snapshot)
    item_id = (
        snapshot.get("offer_item_id") if isinstance(snapshot.get("offer_item_id"), str) else None
    )
    dedup_key = f"{product.marketplace}:{product.external_id}"
    for channel in channels:
        key = f"{channel.id}:{evaluation.id}{key_tag}"
        exists = (
            await db.execute(select(Publication.id).where(Publication.idempotency_key == key))
        ).scalar_one_or_none()
        if exists is not None:
            continue  # o mesmo toque de novo (outra aba, reenvio do formulário)
        reason = await repeat_reason(
            db,
            channel,
            dedup_key=dedup_key,
            price_cents=evaluation.price_cents,
            tier=evaluation.tier,
            now=now,
        )
        target_url = offer_page_url(product.permalink, product.external_id, item_id)
        db.add(
            Publication(
                channel_id=channel.id,
                product_id=product.id,
                evaluation_id=evaluation.id,
                decision_id=decision.id,
                idempotency_key=key,
                dedup_key=dedup_key,
                state="skipped" if reason else "awaiting_link",
                tier=evaluation.tier,
                item_id=item_id,
                target_url=target_url,
                approved_price_cents=evaluation.price_cents,
                pix_price_cents=pix_cents,
                pix_at=seen if pix_cents is not None else None,
                coupon_price_cents=coupon_cents,
                coupon_at=seen if coupon_cents is not None else None,
                last_units=last_units,
                last_units_at=seen if last_units is not None else None,
                flash_ends_at=flash_ends_at,
                note=reason,
                created_at=now,
            )
        )
        if reason:
            assert result.skipped is not None
            result.skipped.append(f"{channel.name}: {reason}")
        else:
            result.created += 1
            if channel.ml_tag and (affiliate_tool_id or channel.transport == "wuzapi"):
                result.auto_link += 1
                if channel.transport == "wuzapi" and channel.wa_jid:
                    result.automatic += 1
    return result


# --------------------------------------------------------------------------- links do ML


async def attach_links(
    db: AsyncSession,
    *,
    channel_id: int,
    publication_ids: list[int],
    text: str,
    now: datetime,
) -> int:
    """Liga os links colados às publicações, na ordem em que as URLs foram entregues."""
    if not publication_ids:
        raise DistributionError("nenhuma publicação esperando link neste grupo")
    rows = {
        p.id: p
        for p in (
            await db.execute(
                select(Publication).where(
                    Publication.id.in_(publication_ids),
                    Publication.channel_id == channel_id,
                    Publication.state == "awaiting_link",
                )
            )
        ).scalars()
    }
    if len(rows) != len(publication_ids):
        raise DistributionError("a lista mudou desde que a página abriu; recarregue e cole de novo")
    try:
        links = parse_affiliate_links(text, len(publication_ids))
    except LinkError as exc:
        raise DistributionError(str(exc)) from exc
    for pub_id, link in zip(publication_ids, links, strict=True):
        pub = rows[pub_id]
        pub.affiliate_url = link
        pub.link_at = now
        pub.state = "ready"
    return len(links)


# --------------------------------------------------------------------------- revalidação


async def fetch_recheck(
    client: MercadoLivreClient, *, external_id: str, item_id: str, approved_cents: int
) -> Recheck:
    """Chamada ao ML (fora de transação)."""
    try:
        offers = await client.product_items(external_id)
    except MercadoLivreError as exc:
        raise DistributionError(
            f"o ML não respondeu ({exc.status or 'rede'}); tente de novo"
        ) from exc
    return recheck_offer(offers, item_id, approved_cents)


def apply_recheck(pub: Publication, recheck: Recheck, now: datetime) -> None:
    pub.checked_at = now
    pub.checked_price_cents = recheck.price_cents
    pub.original_price_cents = recheck.original_cents
    pub.official_price_cents = recheck.official_cents
    pub.message = (
        None  # remontada com o preço e a hora novos (o Pix vale pela hora em que foi visto)
    )
    if recheck.status == "gone":
        pub.note = "a oferta saiu do ar"
    elif recheck.status == "price_up":
        pub.note = "o preço subiu mais de 3% desde a aprovação"
    else:
        pub.note = None


# --------------------------------------------------------------------------- envio


async def channel_status(db: AsyncSession, channel: Channel, now: datetime) -> str | None:
    """Motivo para não enviar agora (ou None). A janela fecha à noite: o dia basta."""
    sent_today, last_sent = (
        await db.execute(
            select(func.count(), func.max(Publication.sent_at)).where(
                Publication.channel_id == channel.id,
                Publication.state == "sent",
                Publication.sent_at >= local_day_start(now),
            )
        )
    ).one()
    return send_block_reason(
        policy_of(channel), now=now, sent_today=sent_today, last_sent_at=last_sent
    )


async def repeat_reason(
    db: AsyncSession,
    channel: Channel,
    *,
    dedup_key: str,
    price_cents: int,
    tier: str,
    now: datetime,
) -> str | None:
    """Por que o produto não entra agora neste grupo (repetido em 7 dias, grupo pausado)."""
    prior = (
        await db.execute(
            select(
                func.coalesce(Publication.checked_price_cents, Publication.approved_price_cents),
                Publication.created_at,
            )
            .where(
                Publication.channel_id == channel.id,
                Publication.dedup_key == dedup_key,
                Publication.created_at >= now - DEDUP_WINDOW,
                Publication.state.in_(("awaiting_link", "ready", "sending", "unknown", "sent")),
            )
            .order_by(Publication.created_at.desc())
            .limit(PRIOR_LIMIT)
        )
    ).all()
    reason = repeat_block_reason(
        [PriorPost(price, created) for price, created in prior],
        price_cents=price_cents,
        tier=tier,
        now=now,
    )
    if reason is None and channel.paused:
        reason = "grupo pausado"
    return reason


async def chosen_channels(db: AsyncSession, ids: list[int]) -> list[Channel]:
    """Os grupos marcados que ainda valem (ativos, de nicho ativo); id desconhecido some."""
    wanted = sorted(set(ids))[:MAX_GROUPS]
    if not wanted:
        return []
    rows = await db.execute(
        select(Channel)
        .join(Niche, Niche.id == Channel.niche_id)
        .where(Channel.id.in_(wanted), Niche.active.is_(True), Channel.paused.is_(False))
        .order_by(Channel.id)
        .limit(MAX_GROUPS)
    )
    return list(rows.scalars())


async def top_position(db: AsyncSession, channel_id: int) -> int:
    """Posição que põe uma publicação à frente de toda a fila do grupo (a mais nova fica em 1º)."""
    first = (
        await db.execute(
            select(func.min(Publication.queue_position)).where(
                Publication.channel_id == channel_id,
                Publication.state.in_(("awaiting_link", "ready")),
            )
        )
    ).scalar_one_or_none()
    return (first if first is not None else QUEUE_STEP) - QUEUE_STEP


async def message_for(db: AsyncSession, pub: Publication, now: datetime | None = None) -> str:
    product = await db.get(CatalogProduct, pub.product_id)
    channel = await db.get(Channel, pub.channel_id)
    if product is None or channel is None or pub.affiliate_url is None:
        raise DistributionError("publicação incompleta")
    if pub.checked_at is None or pub.checked_price_cents is None:
        raise DistributionError("revalide o preço antes de montar a mensagem")
    niche = await db.get(Niche, channel.niche_id)
    evaluation = await db.get(OfferEvaluation, pub.evaluation_id) if pub.evaluation_id else None
    # Link avulso: o preço é o digitado e a hora dele não muda; Pix e últimas unidades vencem
    # pela hora do envio (`now`), não pela hora em que foram digitados junto com o preço.
    at = now if now is not None and pub.tier == MANUAL_TIER else pub.checked_at
    official = bool(evaluation and evaluation.used_official)
    discount = listing_discount(
        pub.checked_price_cents,
        pub.original_price_cents,
        evaluation.reference_cents if evaluation else None,
    )
    return render_message(
        niche_slug=niche.slug if niche else "",
        niche_name=niche.name if niche else "",
        product_name=product.name or product.external_id,
        price_cents=pub.checked_price_cents,
        checked_at=pub.checked_at,
        official=official,
        url=pub.affiliate_url,
        pix_cents=pix_to_show(pub.pix_price_cents, pub.pix_at, pub.checked_price_cents, at),
        coupon_cents=pix_to_show(
            pub.coupon_price_cents, pub.coupon_at, pub.checked_price_cents, at
        ),
        discount=discount,
        versus_official=None
        if official or discount is not None
        else official_comparison(pub.checked_price_cents, pub.official_price_cents),
        seller=None if official else await _seller_line(db, evaluation),
        last_units=units_to_show(pub.last_units, pub.last_units_at, at),
        flash_ends_at=flash_to_show(pub.flash_ends_at, at),
    )


async def _seller_line(db: AsyncSession, evaluation: OfferEvaluation | None) -> str | None:
    """Reputação e vendas de quem vende a oferta aprovada (vem da avaliação)."""
    if evaluation is None:
        return None
    seller_id = json.loads(evaluation.snapshot).get("offer_seller_id")
    if not isinstance(seller_id, int):
        return None
    seller = (
        await db.execute(
            select(MarketplaceSeller).where(
                MarketplaceSeller.marketplace == "mercadolivre",
                MarketplaceSeller.external_id == seller_id,
            )
        )
    ).scalar_one_or_none()
    return seller_line(seller.level_id, seller.transactions_total) if seller else None


async def set_pix(db: AsyncSession, pub: Publication, text: str, now: datetime) -> None:
    """Preço do Pix visto pelo operador. Só com a revalidação ainda valendo."""
    if pub.state != "ready" or pub.checked_price_cents is None or not is_fresh(pub.checked_at, now):
        raise DistributionError("revalide o preço antes de informar o Pix")
    try:
        pix = parse_brl(text)
        if pix is not None:
            check_pix(pix, pub.checked_price_cents)
    except PixError as exc:
        raise DistributionError(str(exc)) from exc
    pub.pix_price_cents = pix
    pub.pix_at = now if pix is not None else None
    pub.message = await message_for(db, pub)


def mark_sent(pub: Publication, *, actor: str, now: datetime) -> bool:
    """Registra o envio uma vez só. Devolve False se já estava registrado."""
    if pub.state == "sent":
        return False
    if pub.state != "ready":
        raise DistributionError("só publicação pronta pode ser marcada como enviada")
    pub.state = "sent"
    pub.sent_at = now
    pub.actor = actor
    return True


def discard(pub: Publication, *, actor: str, reason: str | None) -> None:
    if pub.state == "sent":
        raise DistributionError("publicação já enviada não pode ser descartada")
    pub.state = "discarded"
    pub.actor = actor
    pub.note = (reason or "descartada pelo operador")[:200]
