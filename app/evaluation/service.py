"""Avaliador: aplica a nota (domain/scoring) aos produtos vigiados e grava a avaliação.

Quem entra: produto marcado pelo coletor (o preço mudou) ou com avaliação de mais de 24 h.
Lotes limitados; uma consulta por tipo de dado para o lote inteiro (sem N+1); nenhuma chamada
de rede. A avaliação é imutável (só insert); o produto guarda só o último nível e nota.
"""

from __future__ import annotations

import json
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.core.logging import get_logger
from app.domain.catalog_offers import CatalogSnapshot
from app.domain.distribution import SP, listing_discount
from app.domain.invites import InviteConfig, reference_frozen_until
from app.domain.price_history import PricePoint, compute_stats
from app.domain.scoring import Evaluation, ScoringInput, evaluate, kind_price, required_kind
from app.models import CatalogProduct, Niche, OfferEvaluation, PriceObservation

logger = get_logger(__name__)
HISTORY_DAYS = 90
DAILY_REEVALUATION = timedelta(hours=24)
P90_CACHE_SECONDS = 3600


@dataclass(frozen=True, slots=True)
class NicheRules:
    commission_rate: float
    require_official: bool
    high_risk_brands: frozenset[str]
    slug: str = ""

    @classmethod
    def from_model(cls, niche: Niche) -> NicheRules:
        brands: list[str] = json.loads(niche.high_risk_brands) if niche.high_risk_brands else []
        return cls(
            niche.commission_rate_bp / 10_000,
            niche.require_official,
            frozenset(b.lower() for b in brands),
            niche.slug,
        )


def listing_pct(ev: Evaluation) -> int | None:
    """% OFF do próprio anúncio da oferta avaliada, com as travas do post (selo da fila)."""
    if ev.price_cents is None:
        return None
    found = listing_discount(ev.price_cents, ev.offer_original_cents, ev.reference_cents)
    return found.percent if found else None


def _snapshot(row: PriceObservation) -> CatalogSnapshot:
    return CatalogSnapshot(
        min_price_cents=row.min_price_cents,
        min_official_price_cents=row.min_official_price_cents,
        offers_count=row.offers_count,
        official_offers_count=row.official_offers_count,
        deal_offers_count=row.deal_offers_count,
        free_shipping_offers_count=row.free_shipping_offers_count,
        min_offer_item_id=row.min_offer_item_id,
        min_offer_seller_id=row.min_offer_seller_id,
        min_offer_original_price_cents=row.min_offer_original_price_cents,
        min_offer_free_shipping=row.min_offer_free_shipping,
        eligible_min_price_cents=row.eligible_min_price_cents,
        eligible_item_id=row.eligible_item_id,
        eligible_seller_id=row.eligible_seller_id,
        eligible_offers_count=row.eligible_offers_count,
        trusted_min_price_cents=row.trusted_min_price_cents,
        trusted_item_id=row.trusted_item_id,
        trusted_seller_id=row.trusted_seller_id,
        eligible_official=row.eligible_official,
        trusted_official=row.trusted_official,
        official_item_id=row.official_item_id,
        official_seller_id=row.official_seller_id,
        eligible_original_cents=row.eligible_original_cents,
        trusted_original_cents=row.trusted_original_cents,
        official_original_cents=row.official_original_cents,
        originals_known=row.originals_known,
    )


class Evaluator:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        batch_size: int = 100,
        calendar: InviteConfig | None = None,
    ) -> None:
        self._sessions = sessions
        self._batch = batch_size
        # Calendário de eventos comerciais: a Black Friday congela o preço de referência.
        self._calendar = calendar
        self._p90: dict[int, int | None] = {}
        self._p90_at = 0.0

    async def _niche_p90(
        self, session: AsyncSession, rules: dict[int, NicheRules]
    ) -> dict[int, int | None]:
        """p90 da comissão esperada por nicho (normaliza o componente R); recalcula a cada hora."""
        if self._p90 and time.monotonic() - self._p90_at < P90_CACHE_SECONDS:
            return self._p90
        latest = (
            select(PriceObservation.product_id, func.max(PriceObservation.id).label("mid"))
            .group_by(PriceObservation.product_id)
            .subquery()
        )
        rows = (
            await session.execute(
                select(CatalogProduct.niche_id, PriceObservation.min_price_cents)
                .join(latest, latest.c.product_id == CatalogProduct.id)
                .join(PriceObservation, PriceObservation.id == latest.c.mid)
                .where(CatalogProduct.active.is_(True), CatalogProduct.niche_id.is_not(None))
            )
        ).all()
        by_niche: dict[int, list[float]] = defaultdict(list)
        for niche_id, price in rows:
            rule = rules.get(niche_id)
            if rule is not None:
                by_niche[niche_id].append(price * rule.commission_rate)
        self._p90 = {
            n: int(statistics.quantiles(v, n=10)[-1]) if len(v) >= 10 else None
            for n, v in by_niche.items()
        }
        self._p90_at = time.monotonic()
        return self._p90

    async def evaluate_due(self) -> int:
        now = utcnow()
        async with self._sessions() as session:
            products = list(
                (
                    await session.execute(
                        select(CatalogProduct)
                        .where(
                            CatalogProduct.active.is_(True),
                            CatalogProduct.last_observation_at.is_not(None),
                            or_(
                                CatalogProduct.needs_evaluation.is_(True),
                                CatalogProduct.last_evaluated_at.is_(None),
                                CatalogProduct.last_evaluated_at < now - DAILY_REEVALUATION,
                            ),
                        )
                        .order_by(
                            CatalogProduct.needs_evaluation.desc(), CatalogProduct.last_evaluated_at
                        )
                        .limit(self._batch)
                    )
                ).scalars()
            )
            if not products:
                return 0
            ids = [p.id for p in products]
            niches = (
                await session.execute(
                    select(Niche).where(Niche.id.in_({p.niche_id for p in products if p.niche_id}))
                )
            ).scalars()
            rules = {n.id: NicheRules.from_model(n) for n in niches}
            observations = (
                await session.execute(
                    select(PriceObservation)
                    .where(
                        PriceObservation.product_id.in_(ids),
                        PriceObservation.observed_at >= now - timedelta(days=HISTORY_DAYS),
                    )
                    .order_by(
                        PriceObservation.product_id,
                        PriceObservation.observed_at,
                        PriceObservation.id,
                    )
                )
            ).scalars()
            history: dict[int, list[PriceObservation]] = defaultdict(list)
            for obs in observations:
                history[obs.product_id].append(obs)
            p90 = await self._niche_p90(session, rules)

        results: dict[int, tuple[Evaluation, str, datetime | None]] = {}
        for product in products:
            rows = history.get(product.id)
            rule = rules.get(product.niche_id or -1)
            if not rows or rule is None:
                continue
            # A série da nota é a da mesma régua que a nota usa. Linhas de antes da 0004 só têm o
            # menor preço oficial: entram na régua oficial e ficam fora das outras.
            high_risk = bool(product.brand and product.brand.lower() in rule.high_risk_brands)
            kind = required_kind(require_official=rule.require_official, high_risk_brand=high_risk)
            points = [
                PricePoint(r.observed_at, price)
                for r in rows
                if (price := kind_price(_snapshot(r), kind)[0])
            ]
            ev = evaluate(
                ScoringInput(
                    title=product.name or "",
                    brand=product.brand,
                    commission_rate=rule.commission_rate,
                    require_official=rule.require_official,
                    high_risk_brand=high_risk,
                    snapshot=_snapshot(rows[-1]),
                    stats=compute_stats(
                        points,
                        now,
                        reference_until=reference_frozen_until(
                            now.astimezone(SP).date(), rule.slug, self._calendar
                        )
                        if self._calendar is not None
                        else None,
                    ),
                    observed_at=product.last_checked_at,
                    last_changed_at=product.last_changed_at,
                    highlight_position=product.best_highlight_position,
                    niche_commission_p90_cents=p90.get(product.niche_id or -1),
                    now=now,
                )
            )
            trigger = "price_change" if product.needs_evaluation else "daily"
            results[product.id] = (ev, trigger, product.last_observation_at)

        async with self._sessions() as session, session.begin():
            current = {
                p.id: p
                for p in (
                    await session.execute(
                        select(CatalogProduct).where(CatalogProduct.id.in_(list(results)))
                    )
                ).scalars()
            }
            for product_id, (ev, trigger, seen_observation) in results.items():
                row = current.get(product_id)
                if row is None:
                    continue
                session.add(
                    OfferEvaluation(
                        product_id=product_id,
                        evaluated_at=now,
                        trigger=trigger,
                        ruleset=ev.ruleset,
                        tier=ev.tier.value,
                        score=ev.score,
                        price_cents=ev.price_cents,
                        used_official=ev.used_official,
                        real_discount_bp=round(ev.real_discount * 10_000)
                        if ev.real_discount is not None
                        else None,
                        reference_cents=ev.reference_cents,
                        snapshot=json.dumps(ev.as_snapshot(), ensure_ascii=False),
                    )
                )
                row.last_evaluated_at = now
                row.last_tier = ev.tier.value
                row.last_score = ev.score
                row.listing_discount_pct = listing_pct(ev)
                # Se o coletor gravou preço novo no meio do caminho, a marcação fica para a próxima.
                if row.last_observation_at == seen_observation:
                    row.needs_evaluation = False
        tiers: dict[str, int] = defaultdict(int)
        for ev, _, _ in results.values():
            tiers[ev.tier.value] += 1
        logger.info("avaliação", extra={"evaluated": len(results), "tiers": dict(tiers)})
        return len(results)
