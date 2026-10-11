"""Relatório por nicho (tela Nichos): o "modo sombra" do plano, para decidir onde investir.

Tudo agregado por nicho, e o que é histórico olha só os últimos 7 dias (consultas limitadas,
leves mesmo com meses de série). Medianas em Python sobre listas curtas (no máximo um valor por
produto ativo).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CatalogProduct,
    Channel,
    Niche,
    OfferEvaluation,
    OperatorDecision,
    PriceObservation,
    Publication,
    SalesCheck,
)
from app.whatsapp.service import growth

REPORT_DAYS = 7
GOOD_TIERS = frozenset({"excepcional", "excelente", "boa"})
# Ticket: o que o nicho de fato oferece (fora bloqueada e suspeita, que não vão ao grupo).
TICKET_TIERS = GOOD_TIERS | {"regular"}
PRODUCT_LIMIT = 10_000


@dataclass
class NicheReport:
    niche: Niche
    products: int = 0
    good: int = 0
    with_off: int = 0
    blocked: int = 0
    price_changes_per_day: float = 0.0
    median_real_discount_pct: float | None = None
    median_ticket_cents: int | None = None
    commission_per_order_cents: int | None = None
    approved: int = 0
    rejected: int = 0
    posts_sent: int = 0
    members: int | None = None
    members_delta: int | None = None

    @property
    def blocked_pct(self) -> int | None:
        return round(100 * self.blocked / self.products) if self.products else None


@dataclass
class SalesStatus:
    total: int | None  # vendas nos últimos 30 dias, da última checagem boa
    checked_at: datetime | None
    problem: str | None  # a última checagem falhou (sessão vencida, bloqueio...)


async def niche_report(db: AsyncSession, now: datetime) -> list[NicheReport]:
    since = now - timedelta(days=REPORT_DAYS)
    niches = list(
        (await db.execute(select(Niche).where(Niche.active.is_(True)).order_by(Niche.id))).scalars()
    )
    out = {n.id: NicheReport(n) for n in niches}

    products = await db.execute(
        select(
            CatalogProduct.niche_id, CatalogProduct.last_tier, CatalogProduct.listing_discount_pct
        )
        .where(CatalogProduct.active.is_(True), CatalogProduct.operator_blocked.is_(False))
        .limit(PRODUCT_LIMIT)
    )
    for niche_id, tier, off in products.tuples():
        row = out.get(niche_id or -1)
        if row is None:
            continue
        row.products += 1
        row.good += tier in GOOD_TIERS
        row.blocked += tier == "bloqueada"
        row.with_off += off is not None and tier != "bloqueada"

    # Última avaliação de cada produto (os ativos são reavaliados todo dia: 7 dias cobrem todos).
    latest = (
        select(func.max(OfferEvaluation.id))
        .where(OfferEvaluation.evaluated_at >= since)
        .group_by(OfferEvaluation.product_id)
    )
    evaluations = await db.execute(
        select(
            CatalogProduct.niche_id,
            OfferEvaluation.tier,
            OfferEvaluation.price_cents,
            OfferEvaluation.real_discount_bp,
        )
        .select_from(OfferEvaluation)
        .join(CatalogProduct, CatalogProduct.id == OfferEvaluation.product_id)
        .where(OfferEvaluation.id.in_(latest), CatalogProduct.active.is_(True))
        .limit(PRODUCT_LIMIT)
    )
    tickets: dict[int, list[int]] = {}
    discounts: dict[int, list[int]] = {}
    for niche_id, tier, price, discount_bp in evaluations.tuples():
        if niche_id is None:
            continue
        if price and tier in TICKET_TIERS:
            tickets.setdefault(niche_id, []).append(price)
        if discount_bp is not None:
            discounts.setdefault(niche_id, []).append(discount_bp)

    changes = await db.execute(
        select(CatalogProduct.niche_id, func.count())
        .select_from(PriceObservation)
        .join(CatalogProduct, CatalogProduct.id == PriceObservation.product_id)
        .where(PriceObservation.kind == "change", PriceObservation.observed_at >= since)
        .group_by(CatalogProduct.niche_id)
    )
    for niche_id, count in changes.tuples():
        if (row := out.get(niche_id or -1)) is not None:
            row.price_changes_per_day = count / REPORT_DAYS

    decisions = await db.execute(
        select(CatalogProduct.niche_id, OperatorDecision.decision, func.count())
        .select_from(OperatorDecision)
        .join(CatalogProduct, CatalogProduct.id == OperatorDecision.product_id)
        .where(OperatorDecision.decided_at >= since)
        .group_by(CatalogProduct.niche_id, OperatorDecision.decision)
    )
    for niche_id, decision, count in decisions.tuples():
        if (row := out.get(niche_id or -1)) is None:
            continue
        if decision == "approved":
            row.approved += count
        elif decision == "rejected":
            row.rejected += count

    channels = {
        c.id: c.niche_id
        for c in (await db.execute(select(Channel).order_by(Channel.id).limit(100))).scalars()
    }
    sent = await db.execute(
        select(Publication.channel_id, func.count())
        .where(Publication.state == "sent", Publication.sent_at >= since)
        .group_by(Publication.channel_id)
    )
    for channel_id, count in sent.tuples():
        if (row := out.get(channels.get(channel_id, -1))) is not None:
            row.posts_sent += count
    for channel_id, g in (await growth(db, now)).items():
        if (row := out.get(channels.get(channel_id, -1))) is None or g.today is None:
            continue
        row.members = (row.members or 0) + g.today
        if g.delta is not None:
            row.members_delta = (row.members_delta or 0) + g.delta

    for niche_id, row in out.items():
        if prices := tickets.get(niche_id):
            row.median_ticket_cents = int(statistics.median(prices))
            row.commission_per_order_cents = round(
                row.median_ticket_cents * row.niche.commission_rate_bp / 10_000
            )
        if values := discounts.get(niche_id):
            row.median_real_discount_pct = round(statistics.median(values) / 100, 1)
    return list(out.values())


async def sales_status(db: AsyncSession) -> SalesStatus:
    last = (
        await db.execute(select(SalesCheck).order_by(SalesCheck.id.desc()).limit(1))
    ).scalar_one_or_none()
    good = (
        await db.execute(
            select(SalesCheck)
            .where(SalesCheck.status == "ok")
            .order_by(SalesCheck.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    problem = None
    if last is not None and last.status != "ok":
        problem = {
            "session": "a sessão do portal de afiliados venceu",
            "blocked": "o portal pediu verificação",
            "sem_sessao": "sem sessão do portal no servidor",
        }.get(last.status, "o portal não respondeu na última checagem")
    return SalesStatus(
        good.total_results if good else None, good.checked_at if good else None, problem
    )
