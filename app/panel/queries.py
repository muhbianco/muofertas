"""Leituras do painel. Toda lista tem LIMIT; filtros só de listas fechadas (allowlist)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, case, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collector.domains import SOURCE_PANEL, label
from app.collector.marketplace import MARKETPLACE
from app.core.clock import utcnow
from app.domain.distribution import (
    REPUTATION,
    local_day_start,
    offer_page_url,
    product_page_url,
)
from app.models import (
    Alert,
    CatalogProduct,
    Channel,
    CollectionRun,
    IntegrationError,
    MarketplaceDomain,
    MarketplaceSeller,
    Niche,
    NicheCategory,
    OfferEvaluation,
    OperatorDecision,
    PriceObservation,
    Publication,
    WorkerLease,
)

TIER_ORDER = ["excepcional", "excelente", "boa", "suspeita", "regular", "bloqueada"]
QUEUE_DEFAULT_TIERS = ["excepcional", "excelente", "boa", "suspeita"]
QUEUE_LIMIT = 50
TOP_POSITIONS = 20  # a API de mais vendidos devolve 20 por categoria
# Filtro "com % OFF no anúncio": todos os níveis menos bloqueada.
DISCOUNT_FILTER = "off"
DISCOUNT_TIERS = [t for t in TIER_ORDER if t != "bloqueada"]
# Selo e filtro "já foi para o grupo": envio nos últimos 7 dias (a mesma janela da repetição).
SENT_WINDOW = timedelta(days=7)
SENT_HIDE = "ocultar"
SENT_ONLY = "so"
SENT_FILTERS = (SENT_HIDE, SENT_ONLY)


def product_url(product: CatalogProduct) -> str:
    return product_page_url(product.permalink, product.external_id)


LEVELS = REPUTATION
POWER = {"platinum": "MercadoLíder Platinum", "gold": "MercadoLíder Gold", "silver": "MercadoLíder"}
KIND = {"oficial": "loja oficial", "confiavel": "oferta confiável", "elegivel": "vendedor elegível"}


def offer_url(product: CatalogProduct, item_id: str | None) -> str:
    return offer_page_url(product.permalink, product.external_id, item_id)


@dataclass
class SellerView:
    nickname: str | None
    reputation: str
    power: str | None
    sales: int

    @classmethod
    def of(cls, seller: MarketplaceSeller) -> SellerView:
        return cls(
            nickname=seller.nickname,
            reputation=LEVELS.get(seller.level_id or "", "sem reputação"),
            power=POWER.get(seller.power_seller_status or ""),
            sales=seller.transactions_total,
        )


async def _sellers(db: AsyncSession, ids: set[int]) -> dict[int, SellerView]:
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(MarketplaceSeller).where(
                MarketplaceSeller.marketplace == "mercadolivre",
                MarketplaceSeller.external_id.in_(list(ids)),
            )
        )
    ).scalars()
    return {s.external_id: SellerView.of(s) for s in rows}


def _seller_id(snapshot: dict[str, Any]) -> int | None:
    value = snapshot.get("offer_seller_id")
    return value if isinstance(value, int) else None


@dataclass
class Card:
    product_id: int
    evaluation_id: int | None
    name: str
    niche: str
    image_url: str | None
    permalink: str | None
    tier: str
    score: float | None
    price_cents: int | None
    reference_cents: int | None
    real_discount_bp: int | None
    used_official: bool
    reasons: list[str] = field(default_factory=list)
    offer_kind: str | None = None
    offer_url: str | None = None
    seller: SellerView | None = None
    read_at: datetime | None = None
    # % OFF do próprio anúncio (preço riscado da página), com as travas do post.
    listing_pct: int | None = None
    listing_original_cents: int | None = None
    niche_id: int | None = None
    # Último envio a um grupo nos últimos 7 dias (aprovar de novo sai como repetida).
    sent_at: datetime | None = None
    # Melhor posição nos mais vendidos da categoria (1 a 20); vazio no produto vigiado.
    highlight_position: int | None = None
    # Tipo de produto do ML ("Compressores de ar"): "Trocar nicho" pode valer para todos dele.
    domain_id: str | None = None
    domain_label: str | None = None


def reasons(snapshot: dict[str, Any]) -> list[str]:
    labels = {
        "G1_preco_velho": "preço desatualizado",
        "G2_sem_oferta": "sem oferta à venda",
        "G7_sem_loja_oficial": "sem loja oficial (o nicho exige)",
        "G7_sem_oferta_confiavel": "sem oferta confiável (loja oficial ou MercadoLíder "
        "Gold/Platinum)",
        "G9_sem_vendedor_elegivel": "nenhum vendedor elegível (reputação amarela/verde, "
        "envio pelo ML e vendas mínimas)",
        "G8_titulo_de_copia": "título de cópia/réplica",
        "S1_subiu_para_descer": "subiu antes de cair",
        "S2_desconto_alto_demais": "desconto alto demais",
        "S4_muito_abaixo_da_loja_oficial": "muito abaixo da loja oficial",
    }
    out = [labels.get(g, g) for g in snapshot.get("gates_failed", [])]
    out += [labels.get(s, s) for s in snapshot.get("signals", [])]
    out += list(snapshot.get("notes", []))
    return out


async def niches(db: AsyncSession) -> list[Niche]:
    return list((await db.execute(select(Niche).order_by(Niche.id).limit(50))).scalars())


async def tier_counts(db: AsyncSession, niche_id: int | None) -> dict[str, int]:
    q = select(CatalogProduct.last_tier, func.count()).where(
        CatalogProduct.active.is_(True), CatalogProduct.operator_blocked.is_(False)
    )
    if niche_id is not None:
        q = q.where(CatalogProduct.niche_id == niche_id)
    rows = (await db.execute(q.group_by(CatalogProduct.last_tier))).tuples().all()
    return {tier or "sem nota": count for tier, count in rows}


async def discount_count(db: AsyncSession, niche_id: int | None) -> int:
    """Produtos com % OFF no anúncio fora dos bloqueados (o chip do filtro)."""
    q = select(func.count()).where(
        CatalogProduct.active.is_(True),
        CatalogProduct.operator_blocked.is_(False),
        CatalogProduct.listing_discount_pct.is_not(None),
        CatalogProduct.last_tier.in_(DISCOUNT_TIERS),
    )
    if niche_id is not None:
        q = q.where(CatalogProduct.niche_id == niche_id)
    return int((await db.execute(q)).scalar_one())


def _queue_filters(
    tiers: list[str], *, only_discount: bool, sent: str | None, now: datetime | None
) -> list[ColumnElement[bool]]:
    """O que está na fila de aprovação (a lista e o KPI por nicho usam o mesmo critério)."""
    sent_since = (now or utcnow()) - SENT_WINDOW
    since_change = func.coalesce(CatalogProduct.last_changed_at, CatalogProduct.first_seen_at)
    filters: list[ColumnElement[bool]] = [
        CatalogProduct.active.is_(True),
        CatalogProduct.operator_blocked.is_(False),
        CatalogProduct.last_tier.in_(tiers),
        # Resolvido desde a última mudança de preço sai da fila até o preço mudar:
        # rejeitado, ou aprovado que virou publicação. Aprovado sem grupo no nicho continua
        # na fila (a aprovação não levou a lugar nenhum).
        ~exists().where(
            OperatorDecision.product_id == CatalogProduct.id,
            OperatorDecision.decision == "rejected",
            OperatorDecision.decided_at >= since_change,
        ),
        ~exists().where(
            Publication.product_id == CatalogProduct.id,
            Publication.created_at >= since_change,
        ),
    ]
    if only_discount:
        filters.append(CatalogProduct.listing_discount_pct.is_not(None))
    recently_sent = exists().where(
        Publication.product_id == CatalogProduct.id,
        Publication.state == "sent",
        Publication.sent_at >= sent_since,
    )
    if sent == SENT_HIDE:
        filters.append(~recently_sent)
    elif sent == SENT_ONLY:
        filters.append(recently_sent)
    return filters


@dataclass(frozen=True, slots=True)
class ReadyKpi:
    ready: int  # aprovado, esperando o horário do grupo (ou o link, que sai em segundos)
    sent_today: int
    per_day: int  # soma do limite diário dos grupos do nicho


READY_STATES = ("awaiting_link", "ready")


async def ready_to_post(db: AsyncSession, now: datetime) -> dict[int, ReadyKpi]:
    """O que já está pronto para postar em cada nicho, com os envios de hoje e o limite."""
    today = local_day_start(now)
    pending = Publication.state.in_(READY_STATES)
    sent_today = (Publication.state == "sent") & (Publication.sent_at >= today)
    rows = await db.execute(
        select(
            Channel.niche_id,
            func.sum(case((pending, 1), else_=0)),
            func.sum(case((sent_today, 1), else_=0)),
        )
        .select_from(Publication)
        .join(Channel, Channel.id == Publication.channel_id)
        .where(pending | sent_today)
        .group_by(Channel.niche_id)
    )
    counts = {
        niche_id: (int(ready or 0), int(sent or 0)) for niche_id, ready, sent in rows.tuples()
    }
    caps = await db.execute(
        select(Channel.niche_id, func.sum(Channel.max_per_day))
        .where(Channel.paused.is_(False))
        .group_by(Channel.niche_id)
    )
    out = {}
    for niche_id, per_day in caps.tuples():
        ready, sent = counts.pop(niche_id, (0, 0))
        out[niche_id] = ReadyKpi(ready, sent, int(per_day or 0))
    for niche_id, (ready, sent) in counts.items():  # grupo pausado com fila antiga
        out[niche_id] = ReadyKpi(ready, sent, 0)
    return out


async def queue(
    db: AsyncSession,
    tiers: list[str],
    niche_id: int | None,
    *,
    only_discount: bool = False,
    sent: str | None = None,
    now: datetime | None = None,
) -> list[Card]:
    sent_since = (now or utcnow()) - SENT_WINDOW
    rank = case({t: i for i, t in enumerate(TIER_ORDER)}, value=CatalogProduct.last_tier, else_=99)
    q = (
        select(CatalogProduct, Niche.name)
        .join(Niche, Niche.id == CatalogProduct.niche_id)
        .where(*_queue_filters(tiers, only_discount=only_discount, sent=sent, now=now))
    )
    if niche_id is not None:
        q = q.where(CatalogProduct.niche_id == niche_id)
    # % OFF no anúncio primeiro: enquanto não há histórico para comprovar desconto, é o melhor
    # sinal de promoção que a fila tem. Pesa junto a posição nos mais vendidos da categoria (o 1º
    # dobra o peso, o 20º quase não muda; sem posição, como o vigiado, fica o % puro): 44% OFF no
    # mais vendido vem antes de 70% OFF de um produto que quase ninguém compra. Depois o nível e a
    # nota.
    position = func.coalesce(CatalogProduct.best_highlight_position, TOP_POSITIONS + 1)
    capped = case((position > TOP_POSITIONS, TOP_POSITIONS + 1), else_=position)
    popularity = 1.0 + (TOP_POSITIONS + 1 - capped) / float(TOP_POSITIONS)
    order = (
        CatalogProduct.listing_discount_pct.is_(None),
        (CatalogProduct.listing_discount_pct * popularity).desc(),
        rank,
        CatalogProduct.last_score.desc(),
    )
    rows = (await db.execute(q.order_by(*order).limit(QUEUE_LIMIT))).all()
    ids = [p.id for p, _ in rows]
    latest = await _latest_evaluations(db, ids)
    snaps = {pid: json.loads(ev.snapshot) for pid, ev in latest.items()}
    sellers = await _sellers(db, {s for s in map(_seller_id, snaps.values()) if s is not None})
    sent_at = await _recently_sent(db, ids, sent_since)
    domains = await domain_labels(db, {p.domain_id for p, _ in rows if p.domain_id})
    cards = []
    for product, niche_name in rows:
        ev = latest.get(product.id)
        snap = snaps.get(product.id, {})
        seller_id = _seller_id(snap)
        cards.append(
            Card(
                product_id=product.id,
                evaluation_id=ev.id if ev else None,
                name=product.name or product.external_id,
                niche=niche_name,
                image_url=product.image_url,
                permalink=product_url(product),
                tier=product.last_tier or "",
                score=product.last_score,
                price_cents=ev.price_cents if ev else None,
                reference_cents=ev.reference_cents if ev else None,
                real_discount_bp=ev.real_discount_bp if ev else None,
                used_official=bool(ev and ev.used_official),
                reasons=reasons(snap),
                offer_kind=KIND.get(snap.get("offer_kind") or ""),
                offer_url=offer_url(product, snap.get("offer_item_id")),
                seller=sellers.get(seller_id) if seller_id is not None else None,
                read_at=product.last_checked_at,
                listing_pct=product.listing_discount_pct,
                listing_original_cents=snap.get("offer_original_cents")
                if product.listing_discount_pct is not None
                else None,
                niche_id=product.niche_id,
                sent_at=sent_at.get(product.id),
                domain_id=product.domain_id,
                domain_label=domains.get(product.domain_id or ""),
                highlight_position=product.best_highlight_position,
            )
        )
    return cards


async def domain_labels(db: AsyncSession, domain_ids: set[str]) -> dict[str, str]:
    """Nome de cada tipo de produto para o painel (o id legível enquanto o worker não buscou)."""
    if not domain_ids:
        return {}
    names = dict(
        (
            await db.execute(
                select(MarketplaceDomain.domain_id, MarketplaceDomain.name).where(
                    MarketplaceDomain.marketplace == MARKETPLACE,
                    MarketplaceDomain.domain_id.in_(list(domain_ids)),
                )
            )
        )
        .tuples()
        .all()
    )
    return {d: label(d, names.get(d)) for d in domain_ids}


@dataclass
class DomainRuleRow:
    label: str
    niche: str
    from_panel: bool
    actor: str | None
    at: datetime | None


async def domain_rule_rows(db: AsyncSession) -> list[DomainRuleRow]:
    """Tipos de produto com nicho fixo (tela Nichos)."""
    rows = await db.execute(
        select(MarketplaceDomain, Niche.name)
        .join(Niche, Niche.id == MarketplaceDomain.niche_id)
        .where(MarketplaceDomain.marketplace == MARKETPLACE)
        .order_by(Niche.id, MarketplaceDomain.domain_id)
        .limit(500)
    )
    return [
        DomainRuleRow(
            label(d.domain_id, d.name),
            niche_name,
            d.rule_source == SOURCE_PANEL,
            d.rule_actor,
            d.rule_at,
        )
        for d, niche_name in rows.tuples()
    ]


async def _recently_sent(db: AsyncSession, ids: list[int], since: datetime) -> dict[int, datetime]:
    if not ids:
        return {}
    rows = await db.execute(
        select(Publication.product_id, func.max(Publication.sent_at))
        .where(
            Publication.product_id.in_(ids),
            Publication.state == "sent",
            Publication.sent_at >= since,
        )
        .group_by(Publication.product_id)
    )
    return {pid: at for pid, at in rows.tuples() if at is not None}


async def _latest_evaluations(
    db: AsyncSession, product_ids: list[int]
) -> dict[int, OfferEvaluation]:
    if not product_ids:
        return {}
    latest = (
        select(func.max(OfferEvaluation.id))
        .where(OfferEvaluation.product_id.in_(product_ids))
        .group_by(OfferEvaluation.product_id)
    )
    rows = (
        await db.execute(select(OfferEvaluation).where(OfferEvaluation.id.in_(latest)))
    ).scalars()
    return {r.product_id: r for r in rows}


@dataclass
class ProductPage:
    product: CatalogProduct
    niche: str | None
    observations: list[PriceObservation]
    evaluations: list[OfferEvaluation]
    decisions: list[OperatorDecision]
    latest_snapshot: dict[str, Any]
    seller: SellerView | None = None
    offer_url: str | None = None
    offer_kind: str | None = None
    domain_label: str | None = None


async def product_page(db: AsyncSession, product_id: int) -> ProductPage | None:
    product = await db.get(CatalogProduct, product_id)
    if product is None:
        return None
    niche = await db.get(Niche, product.niche_id) if product.niche_id else None
    since = utcnow() - timedelta(days=90)
    observations = list(
        (
            await db.execute(
                select(PriceObservation)
                .where(
                    PriceObservation.product_id == product_id, PriceObservation.observed_at >= since
                )
                .order_by(PriceObservation.observed_at)
                .limit(1000)
            )
        ).scalars()
    )
    evaluations = list(
        (
            await db.execute(
                select(OfferEvaluation)
                .where(OfferEvaluation.product_id == product_id)
                .order_by(OfferEvaluation.id.desc())
                .limit(10)
            )
        ).scalars()
    )
    decisions = list(
        (
            await db.execute(
                select(OperatorDecision)
                .where(OperatorDecision.product_id == product_id)
                .order_by(OperatorDecision.id.desc())
                .limit(10)
            )
        ).scalars()
    )
    snap = json.loads(evaluations[0].snapshot) if evaluations else {}
    seller_id = _seller_id(snap)
    sellers = await _sellers(db, {seller_id} if seller_id is not None else set())
    return ProductPage(
        product,
        niche.name if niche else None,
        observations,
        evaluations,
        decisions,
        snap,
        seller=sellers.get(seller_id) if seller_id is not None else None,
        offer_url=offer_url(product, snap.get("offer_item_id")),
        offer_kind=KIND.get(snap.get("offer_kind") or ""),
        domain_label=(await domain_labels(db, {product.domain_id})).get(product.domain_id)
        if product.domain_id
        else None,
    )


def sparkline(
    observations: list[PriceObservation], width: int = 320, height: int = 90
) -> dict[str, Any]:
    """Degraus do menor preço de vendedor elegível e da loja oficial, em coordenadas SVG.

    O menor preço bruto fica fora: pode ser de vendedor sem reputação (a nota o ignora).
    """
    prices = [o.eligible_min_price_cents for o in observations if o.eligible_min_price_cents] + [
        o.min_official_price_cents for o in observations if o.min_official_price_cents
    ]
    if not prices:
        return {"min": "", "official": "", "lo": None, "hi": None}
    lo, hi = min(prices), max(prices)
    t0 = observations[0].observed_at.timestamp()
    t1 = max(utcnow().timestamp(), t0 + 1)
    span = hi - lo or 1

    def xy(at: datetime, price: int) -> str:
        x = 4 + (width - 8) * (at.timestamp() - t0) / (t1 - t0)
        y = height - 4 - (height - 8) * (price - lo) / span
        return f"{x:.1f},{y:.1f}"

    def path(values: list[tuple[datetime, int | None]]) -> str:
        pts: list[str] = []
        prev: int | None = None
        for at, price in values:
            if price is None:
                prev = None
                continue
            if prev is not None:
                pts.append(xy(at, prev))  # degrau: o preço anterior vale até aqui
            pts.append(xy(at, price))
            prev = price
        if prev is not None:
            pts.append(xy(utcnow(), prev))
        return " ".join(pts)

    return {
        "min": path([(o.observed_at, o.eligible_min_price_cents) for o in observations]),
        "official": path([(o.observed_at, o.min_official_price_cents) for o in observations]),
        "lo": lo,
        "hi": hi,
    }


@dataclass
class NicheRow:
    niche: Niche
    categories: list[NicheCategory]
    counts: dict[str, int]


async def niche_overview(db: AsyncSession) -> list[NicheRow]:
    all_niches = await niches(db)
    cats = list(
        (await db.execute(select(NicheCategory).order_by(NicheCategory.id).limit(500))).scalars()
    )
    counts_rows = (
        (
            await db.execute(
                select(CatalogProduct.niche_id, CatalogProduct.last_tier, func.count())
                .where(CatalogProduct.active.is_(True))
                .group_by(CatalogProduct.niche_id, CatalogProduct.last_tier)
            )
        )
        .tuples()
        .all()
    )
    counts: dict[int, dict[str, int]] = {}
    for niche_id, tier, count in counts_rows:
        if niche_id is not None:
            counts.setdefault(niche_id, {})[tier or "sem nota"] = count
    return [
        NicheRow(n, [c for c in cats if c.niche_id == n.id], counts.get(n.id, {}))
        for n in all_niches
    ]


@dataclass
class Health:
    runs: list[CollectionRun]
    errors_24h: int
    recent_errors: list[IntegrationError]
    calls_last_hour: int
    observations_24h: int
    products_active: int
    lease: WorkerLease | None
    alerts: list[Alert] = field(default_factory=list)


async def open_alerts(db: AsyncSession) -> int:
    return (
        await db.execute(select(func.count()).select_from(Alert).where(Alert.state == "open"))
    ).scalar_one()


async def health(db: AsyncSession) -> Health:
    now = utcnow()
    runs = list(
        (
            await db.execute(select(CollectionRun).order_by(CollectionRun.id.desc()).limit(20))
        ).scalars()
    )
    errors_24h = (
        await db.execute(
            select(func.count())
            .select_from(IntegrationError)
            .where(IntegrationError.occurred_at >= now - timedelta(hours=24))
        )
    ).scalar_one()
    recent_errors = list(
        (
            await db.execute(
                select(IntegrationError).order_by(IntegrationError.id.desc()).limit(10)
            )
        ).scalars()
    )
    calls = (
        await db.execute(
            select(func.coalesce(func.sum(CollectionRun.calls), 0)).where(
                CollectionRun.started_at >= now - timedelta(hours=1)
            )
        )
    ).scalar_one()
    observations = (
        await db.execute(
            select(func.count())
            .select_from(PriceObservation)
            .where(PriceObservation.observed_at >= now - timedelta(hours=24))
        )
    ).scalar_one()
    products = (
        await db.execute(
            select(func.count()).select_from(CatalogProduct).where(CatalogProduct.active.is_(True))
        )
    ).scalar_one()
    lease = await db.get(WorkerLease, "collector")
    alerts = list(
        (
            await db.execute(select(Alert).order_by(Alert.state.desc(), Alert.id.desc()).limit(15))
        ).scalars()
    )
    return Health(
        runs, errors_24h, recent_errors, int(calls), observations, products, lease, alerts
    )
