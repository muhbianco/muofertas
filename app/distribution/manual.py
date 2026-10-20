"""Link avulso: o operador cola o link de um anúncio, digita o que a página mostra e o post entra
em 1º na fila de cada grupo escolhido, com o link de afiliado do grupo gerado como sempre.

A API do app não lê anúncio fora do catálogo (`/items` dá 403), então não há revalidação: o preço
é o digitado, com a hora em que foi visto na mensagem. Vale 6 horas, ou até a oferta relâmpago
acabar (`manual_block_reason`). O produto entra na base inativo (o robô não o acompanha); se o
link é de um produto que já temos, usa o dele.

A foto é obrigatória quando o produto não tem uma na base: o servidor não lê a página do ML.
Link curto do afiliado do sistema (`short_url`, ver `app.distribution.short_links`): o produto é
desconhecido, então o link vai como está, o mesmo para todos os grupos (18/10/2026).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collector.marketplace import MARKETPLACE
from app.core.logging import get_logger
from app.distribution.service import (
    DistributionError,
    chosen_channels,
    repeat_plan_for,
    top_position,
)
from app.domain.distribution import (
    MANUAL_TIER,
    FlashError,
    LinkError,
    PixError,
    UnitsError,
    check_coupon,
    check_pix,
    parse_brl,
    parse_flash_left,
    parse_last_units,
    parse_manual_link,
    short_link,
    short_manual_link,
)
from app.integrations.images import IMAGE_HOSTS
from app.models import CatalogProduct, OperatorDecision, Publication

logger = get_logger(__name__)
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{8,32}$")
WEB_IMAGE_EXT = re.compile(r"\.(webp|avif)$", re.IGNORECASE)
NAME_MAX = 200
DISCOVERED_MANUAL = "avulso"


class ManualError(DistributionError):
    """Formulário do link avulso recusado (mensagem pronta para o operador)."""


@dataclass(frozen=True)
class ManualPost:
    url: str
    name: str
    price_cents: int
    original_cents: int | None
    pix_cents: int | None
    coupon_cents: int | None
    last_units: int | None
    image_url: str | None
    flash_left: timedelta | None
    channel_ids: list[int]
    nonce: str  # do formulário: reenviar o mesmo formulário não duplica
    short_url: str | None = None  # link curto do afiliado do sistema (o produto não é conhecido)


@dataclass(frozen=True)
class ManualForm:
    """O formulário como veio (texto), para validar e para devolver preenchido no erro."""

    url: str = ""
    nome: str = ""
    preco: str = ""
    de: str = ""
    pix: str = ""
    cupom: str = ""
    ultimas: str = ""
    foto: str = ""
    relampago: bool = False
    acaba_em: str = ""
    nonce: str = ""


def _money(text: str, what: str) -> int | None:
    try:
        return parse_brl(text)
    except PixError as exc:
        raise ManualError(f"{what}: valor inválido (ex.: 308,00)") from exc


def post_from_form(
    form: ManualForm,
    channel_ids: list[int],
    *,
    url: str | None = None,
    short_url: str | None = None,
) -> ManualPost:
    """Texto do formulário → post. O tempo da relâmpago preenchido já vale como relâmpago."""
    price = _money(form.preco, "preço")
    if price is None:
        raise ManualError("digite o preço que a página mostra")
    try:
        units = parse_last_units(form.ultimas)
    except UnitsError as exc:
        raise ManualError("últimas unidades: um número de 1 a 20") from exc
    flash: timedelta | None = None
    if form.relampago or form.acaba_em.strip():
        if not form.acaba_em.strip():
            raise ManualError("oferta relâmpago: digite quanto falta (o relógio da página)")
        try:
            flash = parse_flash_left(form.acaba_em)
        except FlashError as exc:
            raise ManualError(str(exc)) from exc
    return ManualPost(
        url=url if url is not None else form.url,
        name=form.nome,
        price_cents=price,
        original_cents=_money(form.de, "preço “de”"),
        pix_cents=_money(form.pix, "Pix"),
        coupon_cents=_money(form.cupom, "preço com cupom"),
        last_units=units,
        image_url=form.foto.strip() or None,
        flash_left=flash,
        channel_ids=channel_ids,
        nonce=form.nonce,
        short_url=short_url,
    )


@dataclass
class ManualResult:
    created: int = 0
    skipped: list[str] = field(default_factory=list)
    channel_ids: list[int] = field(default_factory=list)


def check_image_url(url: str | None) -> str | None:
    """Endereço da foto copiado da página do anúncio. A página entrega WebP, que o envio não
    aceita; o CDN do ML serve a mesma imagem em JPEG trocando a extensão (testado em 12/10)."""
    if not url or not url.strip():
        return None
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or parts.hostname not in IMAGE_HOSTS or len(url) > 512:
        raise ManualError(
            "foto: use o endereço da imagem do ML (começa com https://http2.mlstatic.com)"
        )
    path = WEB_IMAGE_EXT.sub(".jpg", parts.path)
    return f"https://{parts.hostname}{path}"


async def queue_manual(
    db: AsyncSession, post: ManualPost, *, actor: str, now: datetime
) -> ManualResult:
    if not NONCE_RE.match(post.nonce):
        raise ManualError("formulário vencido: abra o link avulso de novo")
    short = short_link(post.short_url) if post.short_url else None
    try:
        link = short_manual_link(short) if short else parse_manual_link(post.url)
    except LinkError as exc:
        raise ManualError(str(exc)) from exc
    if post.price_cents <= 0:
        raise ManualError("digite o preço que a página mostra")
    if post.original_cents is not None and post.original_cents <= post.price_cents:
        raise ManualError("o preço “de” (riscado) precisa ser maior que o preço")
    try:
        if post.pix_cents is not None:
            check_pix(post.pix_cents, post.price_cents)
        if post.coupon_cents is not None:
            check_coupon(post.coupon_cents, post.price_cents)
    except ValueError as exc:
        raise ManualError(str(exc)) from exc
    image = check_image_url(post.image_url)
    channels = await chosen_channels(db, post.channel_ids)
    if not channels:
        raise ManualError("marque pelo menos um grupo ativo")

    product = (
        await db.execute(
            select(CatalogProduct).where(
                CatalogProduct.marketplace == MARKETPLACE,
                CatalogProduct.external_id == link.external_id,
            )
        )
    ).scalar_one_or_none()
    name = post.name.strip()[:NAME_MAX]
    if image is None and (product is None or not product.image_url):
        raise ManualError(
            "foto: obrigatória para este produto, o sistema não tem a foto dele. Na página do "
            "anúncio, abra a foto e copie o endereço da imagem (https://http2.mlstatic.com/…)"
        )
    if product is None:
        if not name:
            raise ManualError("escreva o nome do produto (como está na página)")
        product = CatalogProduct(
            marketplace=MARKETPLACE,
            external_id=link.external_id,
            niche_id=channels[0].niche_id,
            name=name,
            permalink=link.target_url,
            image_url=image,
            discovered_via=DISCOVERED_MANUAL,
            active=False,  # o robô não acompanha: a API não lê o anúncio
            tier="cold",
            next_check_at=now,
            consecutive_failures=0,
            first_seen_at=now,
        )
        db.add(product)
        await db.flush()
    elif product.discovered_via == DISCOVERED_MANUAL:
        # Produto que só existe por link avulso: vale o que o operador digitou por último.
        product.name = name or product.name
        product.image_url = image or product.image_url
    elif not product.name and not name:
        raise ManualError("escreva o nome do produto (como está na página)")

    decision = OperatorDecision(
        product_id=product.id,
        decision="approved",
        reason="link avulso",
        actor=actor,
        decided_at=now,
    )
    db.add(decision)
    await db.flush()
    flash_ends_at = now + post.flash_left if post.flash_left is not None else None
    dedup_key = f"{MARKETPLACE}:{link.external_id}"
    result = ManualResult()
    for channel in channels:
        key = f"{channel.id}:av:{post.nonce}"
        exists = (
            await db.execute(select(Publication.id).where(Publication.idempotency_key == key))
        ).scalar_one_or_none()
        if exists is not None:
            continue  # o mesmo formulário de novo (duplo toque, voltar e reenviar)
        plan = await repeat_plan_for(
            db,
            channel,
            dedup_key=dedup_key,
            price_cents=post.price_cents,
            tier=MANUAL_TIER,
            now=now,
        )
        reason = plan.skip
        db.add(
            Publication(
                channel_id=channel.id,
                product_id=product.id,
                decision_id=decision.id,
                idempotency_key=key,
                dedup_key=dedup_key,
                # Link curto do nosso afiliado: já é o link do post (não há página para gerar).
                state="skipped" if reason else "ready" if short else "awaiting_link",
                affiliate_url=link.target_url if short and not reason else None,
                tier=MANUAL_TIER,
                item_id=link.item_id or link.external_id,
                target_url=link.target_url,
                approved_price_cents=post.price_cents,
                # Sem revalidação: o preço "conferido" é o digitado, na hora em que foi visto.
                checked_price_cents=post.price_cents,
                checked_at=now,
                original_price_cents=post.original_cents,
                pix_price_cents=post.pix_cents,
                pix_at=now if post.pix_cents is not None else None,
                coupon_price_cents=post.coupon_cents,
                coupon_at=now if post.coupon_cents is not None else None,
                last_units=post.last_units,
                last_units_at=now if post.last_units is not None else None,
                flash_ends_at=flash_ends_at,
                not_before=plan.not_before,
                queue_position=None if reason else await top_position(db, channel.id),
                note=reason,
                created_at=now,
                actor=actor,
            )
        )
        await db.flush()
        if reason:
            result.skipped.append(f"{channel.name}: {reason}")
        else:
            result.created += 1
            result.channel_ids.append(channel.id)
    logger.info(
        "link avulso",
        extra={
            "product_id": product.id,
            "groups": result.channel_ids,
            "skipped_groups": len(result.skipped),
            "flash": flash_ends_at is not None,
            "user": actor,
        },
    )
    return result
