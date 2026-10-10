"""Casos de uso do coletor do ML: descoberta pelos mais vendidos e atualização de preço.

Nenhuma transação fica aberta durante chamada de rede: lê o que precisa numa transação curta,
chama a API fora dela e grava o resultado em outra transação curta.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.catalog_offers import (
    CatalogSnapshot,
    OfferPolicy,
    SellerInfo,
    Tier,
    TierPolicy,
    needs_heartbeat,
    next_tier,
    seller_ids,
    summarize,
)
from app.integrations.mercadolivre import (
    CallBudgetExceededError,
    ForbiddenError,
    MercadoLivreClient,
    MercadoLivreError,
    NotFoundError,
    RateLimitedError,
)
from app.models import (
    CatalogProduct,
    CollectionRun,
    IntegrationError,
    MarketplaceSeller,
    NicheCategory,
    PriceObservation,
)

logger = get_logger(__name__)
MARKETPLACE = "mercadolivre"
MAX_FAILURES_BEFORE_COLD = 3
SELLER_TTL = timedelta(days=7)


def tier_policy(settings: Settings) -> TierPolicy:
    return TierPolicy(
        hot=timedelta(minutes=settings.tier_hot_minutes),
        warm=timedelta(minutes=settings.tier_warm_minutes),
        cold=timedelta(minutes=settings.tier_cold_minutes),
    )


def _jitter(interval: timedelta) -> timedelta:
    """Espalha as próximas consultas (±10%) para não formar picos."""
    return interval * random.uniform(0.9, 1.1)  # noqa: S311 - agenda, não é segurança


def _brand(product: dict[str, object]) -> str | None:
    attributes = product.get("attributes")
    if not isinstance(attributes, list):
        return None
    for attr in attributes:
        if isinstance(attr, dict) and attr.get("id") == "BRAND":
            value = attr.get("value_name")
            return str(value)[:80] if value else None
    return None


def _picture(product: dict[str, object]) -> str | None:
    pictures = product.get("pictures") or []
    if isinstance(pictures, list) and pictures and isinstance(pictures[0], dict):
        url = pictures[0].get("url")
        return str(url)[:512] if url else None
    return None


@dataclass
class RunStats:
    calls: int = 0
    seen: int = 0
    changed: int = 0
    errors: int = 0


class Collector:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        client: MercadoLivreClient,
        settings: Settings,
    ) -> None:
        self._sessions = sessions
        self._client = client
        self._settings = settings
        self._policy = tier_policy(settings)
        self._offer_policy = OfferPolicy(min_seller_sales=settings.min_seller_sales)

    # ------------------------------------------------------------------ registro

    async def _start_run(self, source: str) -> int:
        async with self._sessions() as session, session.begin():
            run = CollectionRun(source=source, started_at=utcnow(), status="running")
            session.add(run)
            await session.flush()
            return run.id

    async def _finish_run(
        self, run_id: int, stats: RunStats, status: str, note: str | None = None
    ) -> None:
        async with self._sessions() as session, session.begin():
            run = await session.get(CollectionRun, run_id)
            if run is None:
                return
            run.finished_at = utcnow()
            run.status = status
            run.calls = stats.calls
            run.items_seen = stats.seen
            run.items_changed = stats.changed
            run.errors = stats.errors
            run.note = note[:255] if note else None

    async def _record_error(self, source: str, run_id: int | None, exc: MercadoLivreError) -> None:
        async with self._sessions() as session, session.begin():
            session.add(
                IntegrationError(
                    source=source,
                    run_id=run_id,
                    occurred_at=utcnow(),
                    http_status=exc.status,
                    error_code=exc.code,
                    path=(exc.path or "")[:255] or None,
                    message=str(exc)[:500],
                )
            )

    # ------------------------------------------------------------------ descoberta

    async def discover_due(self) -> int:
        """Varre os mais vendidos das categorias vencidas e devolve quantos produtos novos."""
        cutoff = utcnow() - timedelta(minutes=self._settings.discovery_interval_minutes)
        async with self._sessions() as session:
            due = (
                await session.execute(
                    select(
                        NicheCategory.id, NicheCategory.niche_id, NicheCategory.external_category_id
                    )
                    .where(
                        NicheCategory.active.is_(True),
                        NicheCategory.marketplace == MARKETPLACE,
                        (NicheCategory.last_discovery_at.is_(None))
                        | (NicheCategory.last_discovery_at < cutoff),
                    )
                    .order_by(
                        NicheCategory.last_discovery_at.is_(None).desc(),
                        NicheCategory.last_discovery_at,
                    )
                    .limit(20)
                )
            ).all()
        created = 0
        for cat_id, niche_id, external_id in due:
            created += await self._discover_category(cat_id, niche_id, external_id)
        return created

    async def _discover_category(
        self, cat_id: int, niche_id: int, external_category_id: str
    ) -> int:
        run_id = await self._start_run("ml_discovery")
        stats = RunStats()
        calls_before = self._client.calls
        try:
            entries = await self._client.highlights(external_category_id)
            positions: dict[str, int] = {}
            for entry in entries:
                if entry.get("type") == "PRODUCT" and isinstance(entry.get("id"), str):
                    positions.setdefault(entry["id"], int(entry.get("position") or 99))
            stats.seen = len(positions)

            async with self._sessions() as session:
                known = set(
                    (
                        await session.execute(
                            select(CatalogProduct.external_id).where(
                                CatalogProduct.marketplace == MARKETPLACE,
                                CatalogProduct.external_id.in_(list(positions)),
                            )
                        )
                    ).scalars()
                )
            details: dict[str, dict[str, object]] = {}
            for external_id in positions:
                if external_id not in known:
                    try:
                        details[external_id] = await self._client.product(external_id)
                    except (NotFoundError, ForbiddenError) as exc:
                        stats.errors += 1
                        await self._record_error("ml_discovery", run_id, exc)

            now = utcnow()
            async with self._sessions() as session, session.begin():
                existing = {
                    p.external_id: p
                    for p in (
                        await session.execute(
                            select(CatalogProduct).where(
                                CatalogProduct.marketplace == MARKETPLACE,
                                CatalogProduct.external_id.in_(list(positions)),
                            )
                        )
                    ).scalars()
                }
                for external_id, position in positions.items():
                    product = existing.get(external_id)
                    if product is None:
                        info = details.get(external_id)
                        if info is None:
                            continue
                        session.add(
                            CatalogProduct(
                                marketplace=MARKETPLACE,
                                external_id=external_id,
                                niche_id=niche_id,
                                category_id=external_category_id,
                                name=str(info.get("name") or "")[:255] or None,
                                brand=_brand(info),
                                domain_id=str(info.get("domain_id") or "")[:80] or None,
                                permalink=str(info.get("permalink") or "")[:512] or None,
                                image_url=_picture(info),
                                discovered_via="highlights",
                                active=True,
                                tier=Tier.WARM.value,
                                next_check_at=now,
                                consecutive_failures=0,
                                first_seen_at=now,
                                last_seen_in_highlights_at=now,
                                best_highlight_position=position,
                            )
                        )
                        stats.changed += 1
                    else:
                        product.last_seen_in_highlights_at = now
                        product.best_highlight_position = min(
                            product.best_highlight_position or position, position
                        )
                        if not product.active:
                            product.active = True
                            product.next_check_at = now
                category = await session.get(NicheCategory, cat_id)
                if category is not None:
                    category.last_discovery_at = now
            stats.calls = self._client.calls - calls_before
            await self._finish_run(run_id, stats, "ok" if stats.errors == 0 else "partial")
            logger.info(
                "descoberta",
                extra={"category": external_category_id, "found": stats.seen, "new": stats.changed},
            )
            return stats.changed
        except (RateLimitedError, CallBudgetExceededError) as exc:
            stats.calls = self._client.calls - calls_before
            await self._finish_run(run_id, stats, "partial", str(exc))
            raise
        except MercadoLivreError as exc:
            stats.calls = self._client.calls - calls_before
            stats.errors += 1
            await self._record_error("ml_discovery", run_id, exc)
            await self._finish_run(run_id, stats, "failed", str(exc))
            return 0

    # ------------------------------------------------------------------ vendedores

    async def _sellers(self, wanted: set[int], run_id: int) -> dict[int, SellerInfo]:
        """Reputação dos vendedores: cache no banco; o que falta ou venceu vem por /users/bulk."""
        if not wanted:
            return {}
        fresh_after = utcnow() - SELLER_TTL
        async with self._sessions() as session:
            cached = {
                s.external_id: s
                for s in (
                    await session.execute(
                        select(MarketplaceSeller).where(
                            MarketplaceSeller.marketplace == MARKETPLACE,
                            MarketplaceSeller.external_id.in_(list(wanted)),
                        )
                    )
                ).scalars()
            }
        missing = sorted(s for s in wanted if s not in cached or cached[s].checked_at < fresh_after)
        fetched: list[dict[str, Any]] = []
        if missing:
            try:
                fetched = await self._client.users_bulk(missing)
            except (RateLimitedError, CallBudgetExceededError):
                raise
            except MercadoLivreError as exc:
                await self._record_error("ml_sellers", run_id, exc)
        now = utcnow()
        if fetched:
            async with self._sessions() as session, session.begin():
                rows = {
                    s.external_id: s
                    for s in (
                        await session.execute(
                            select(MarketplaceSeller).where(
                                MarketplaceSeller.marketplace == MARKETPLACE,
                                MarketplaceSeller.external_id.in_([u["id"] for u in fetched]),
                            )
                        )
                    ).scalars()
                }
                for user in fetched:
                    rep = user.get("seller_reputation") or {}
                    transactions = (rep.get("transactions") or {}) if isinstance(rep, dict) else {}
                    row = rows.get(user["id"])
                    if row is None:
                        row = MarketplaceSeller(marketplace=MARKETPLACE, external_id=user["id"])
                        session.add(row)
                        cached[user["id"]] = row
                    row.nickname = str(user.get("nickname") or "")[:120] or None
                    row.level_id = rep.get("level_id") if isinstance(rep, dict) else None
                    row.power_seller_status = (
                        rep.get("power_seller_status") if isinstance(rep, dict) else None
                    )
                    row.transactions_total = int(transactions.get("total") or 0)
                    row.status = (
                        str((user.get("status") or {}).get("site_status") or "")[:16] or None
                    )
                    row.checked_at = now
                    cached[row.external_id] = row
        return {
            sid: SellerInfo(s.level_id, s.power_seller_status, s.transactions_total)
            for sid, s in cached.items()
        }

    # ------------------------------------------------------------------ atualização de preço

    async def refresh_due(self) -> RunStats:
        """Atualiza um lote de produtos vencidos (lote limitado; o worker chama de novo)."""
        now = utcnow()
        async with self._sessions() as session:
            due = list(
                (
                    await session.execute(
                        select(CatalogProduct)
                        .where(CatalogProduct.active.is_(True), CatalogProduct.next_check_at <= now)
                        .order_by(CatalogProduct.next_check_at)
                        .limit(self._settings.refresh_batch_size)
                    )
                ).scalars()
            )
            previous = await _latest_snapshots(session, [p.id for p in due])
        stats = RunStats()
        if not due:
            return stats

        run_id = await self._start_run("ml_refresh")
        calls_before = self._client.calls
        raw: dict[int, list[dict[str, Any]]] = {}
        results: dict[int, CatalogSnapshot | MercadoLivreError | None] = {}
        stop: MercadoLivreError | None = None
        for product in due:
            try:
                raw[product.id] = await self._client.product_items(product.external_id)
            except (RateLimitedError, CallBudgetExceededError) as exc:
                stop = exc
                break
            except MercadoLivreError as exc:
                results[product.id] = exc
                stats.errors += 1
                await self._record_error("ml_refresh", run_id, exc)

        # Reputação dos vendedores do lote inteiro (cache de 7 dias + /users/bulk para o resto).
        sellers: dict[int, SellerInfo] = {}
        if raw and stop is None:
            wanted: set[int] = set()
            for offers in raw.values():
                wanted |= seller_ids(offers)
            try:
                sellers = await self._sellers(wanted, run_id)
            except (RateLimitedError, CallBudgetExceededError) as exc:
                stop = exc
        for product_id, offers in raw.items():
            results[product_id] = summarize(offers, sellers, self._offer_policy)

        now = utcnow()
        async with self._sessions() as session, session.begin():
            # Uma consulta para o lote inteiro (sem N+1); as linhas voltam prontas para atualizar.
            loaded = {
                p.id: p
                for p in (
                    await session.execute(
                        select(CatalogProduct).where(CatalogProduct.id.in_(list(results)))
                    )
                ).scalars()
            }
            for product in due:
                if product.id not in results:
                    continue
                current = loaded.get(product.id)
                if current is None:
                    continue
                outcome = results[product.id]
                stats.seen += 1
                current.last_checked_at = now
                if isinstance(outcome, MercadoLivreError):
                    current.consecutive_failures += 1
                    if isinstance(outcome, NotFoundError):
                        current.active = False
                    tier = (
                        Tier.COLD
                        if current.consecutive_failures >= MAX_FAILURES_BEFORE_COLD
                        else Tier(current.tier)
                    )
                    current.tier = tier.value
                    current.next_check_at = now + _jitter(self._policy.interval(tier))
                    continue
                current.consecutive_failures = 0
                prev = previous.get(product.id)
                if outcome is None:
                    # Sem oferta nova à venda agora: confere de novo devagar.
                    current.tier = Tier.COLD.value
                    current.next_check_at = now + _jitter(self._policy.cold)
                    continue
                changed = outcome.differs_from(prev)
                heartbeat = needs_heartbeat(
                    current.last_observation_at,
                    now,
                    timedelta(hours=self._settings.heartbeat_hours),
                )
                if changed or heartbeat:
                    session.add(
                        _observation(product.id, now, outcome, "change" if changed else "heartbeat")
                    )
                    current.last_observation_at = now
                if changed:
                    current.last_changed_at = now
                    current.needs_evaluation = True
                    stats.changed += 1
                tier = next_tier(
                    Tier(current.tier),
                    previous=prev,
                    new=outcome,
                    last_changed_at=current.last_changed_at,
                    now=now,
                    policy=self._policy,
                )
                current.tier = tier.value
                current.next_check_at = now + _jitter(self._policy.interval(tier))

        stats.calls = self._client.calls - calls_before
        status = "partial" if stop or stats.errors else "ok"
        await self._finish_run(run_id, stats, status, str(stop) if stop else None)
        if stop:
            raise stop
        return stats


async def _latest_snapshots(
    session: AsyncSession, product_ids: list[int]
) -> dict[int, CatalogSnapshot]:
    """Última observação de cada produto do lote, numa consulta só (sem N+1)."""
    if not product_ids:
        return {}
    latest = (
        select(PriceObservation.product_id, func.max(PriceObservation.id).label("mid"))
        .where(PriceObservation.product_id.in_(product_ids))
        .group_by(PriceObservation.product_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(PriceObservation).join(latest, PriceObservation.id == latest.c.mid)
        )
    ).scalars()
    return {row.product_id: _snapshot(row) for row in rows}


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
    )


def _observation(
    product_id: int, now: datetime, snap: CatalogSnapshot, kind: str
) -> PriceObservation:
    return PriceObservation(
        product_id=product_id,
        observed_at=now,
        kind=kind,
        currency="BRL",
        min_price_cents=snap.min_price_cents,
        min_official_price_cents=snap.min_official_price_cents,
        offers_count=snap.offers_count,
        official_offers_count=snap.official_offers_count,
        deal_offers_count=snap.deal_offers_count,
        free_shipping_offers_count=snap.free_shipping_offers_count,
        min_offer_item_id=snap.min_offer_item_id,
        min_offer_seller_id=snap.min_offer_seller_id,
        min_offer_original_price_cents=snap.min_offer_original_price_cents,
        min_offer_free_shipping=snap.min_offer_free_shipping,
        eligible_min_price_cents=snap.eligible_min_price_cents,
        eligible_item_id=snap.eligible_item_id,
        eligible_seller_id=snap.eligible_seller_id,
        eligible_offers_count=snap.eligible_offers_count,
        trusted_min_price_cents=snap.trusted_min_price_cents,
        trusted_item_id=snap.trusted_item_id,
        trusted_seller_id=snap.trusted_seller_id,
        eligible_official=snap.eligible_official,
        trusted_official=snap.trusted_official,
        official_item_id=snap.official_item_id,
        official_seller_id=snap.official_seller_id,
    )
