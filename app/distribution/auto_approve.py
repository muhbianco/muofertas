"""Aprovação automática (plano, semana 3: "Excelente+"; ampliada em 26/10/2026).

Faz o mesmo que o botão Aprovar, como ator "aprovação automática". Só entra produto com nota nos
níveis configurados (`AUTO_APPROVE_TIERS`), e fica de fora quem o operador já decidiu desde a
última mudança de preço (aprovou, rejeitou ou bloqueou) e quem já virou publicação.

Desde 26/10/2026 (pedido do dono: aprovar à mão não dava conta e a fila secou):
- **Grupos:** os mesmos que o "Aprovar" marca sozinho: os ativos do nicho do produto e os
  marcados "sempre" (o geral). Antes ia só para os do nicho: com os grupos de nicho pausados,
  36 das 44 aprovações de 22 a 25/10 ficaram em grupo pausado e só 8 saíram.
- **Excepcional e excelente** saem sempre.
- **Boa e regular só enchem a fila:** enquanto o grupo tem menos de `queue_target` posts
  esperando (dois lotes), na ordem da fila de aprovação (% OFF no anúncio pesado pela posição nos
  mais vendidos, depois o nível e a nota). Regular só com % OFF no anúncio: sem ele, não há
  sinal de promoção nenhum. Sem o operador, não há Pix nem cupom (o dono aceitou a troca).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from app.core.logging import get_logger
from app.distribution.service import DistributionError, publications_for_approval
from app.domain.distribution import SEND_BATCH
from app.models import (
    CatalogProduct,
    Channel,
    Niche,
    OfferEvaluation,
    OperatorDecision,
    Publication,
)

logger = get_logger(__name__)
ROBOT_APPROVER = "aprovação automática"
BATCH = 5
# Produto que não deu para aprovar (sem preço fresco, sem grupo): tenta de novo depois disso.
RETRY_AFTER = timedelta(hours=1)
# A busca de boa/regular custa ~0,6 s em produção (26/10/2026, 3,6 mil regulares): rodada que
# não aprovou nada espera isto antes de procurar de novo.
FILL_PAUSE = timedelta(minutes=1)
PRIORITY_TIERS = frozenset({"excepcional", "excelente"})
NEEDS_LISTING_DISCOUNT = frozenset({"regular"})
WAITING = ("awaiting_link", "ready")
FILL_RANK = {"excepcional": 0, "excelente": 1, "boa": 2, "suspeita": 3, "regular": 4}
TOP_POSITIONS = 20  # como na fila de aprovação (app/panel/queries.py)


def queue_target(channel: Channel) -> int:
    """Quantos posts esperando o grupo aguenta antes de parar de encher: dois lotes; no ritmo,
    meio dia de envios."""
    if channel.send_mode == SEND_BATCH and channel.batch_size:
        return 2 * channel.batch_size
    return max(1, channel.max_per_day // 2)


def _fill_order() -> tuple[ColumnElement[Any], ...]:
    """A ordem da fila de aprovação (`queue_page`): o robô aprova o que o operador via primeiro."""
    position = func.coalesce(CatalogProduct.best_highlight_position, TOP_POSITIONS + 1)
    capped = case((position > TOP_POSITIONS, TOP_POSITIONS + 1), else_=position)
    popularity = 1.0 + (TOP_POSITIONS + 1 - capped) / float(TOP_POSITIONS)
    rank = case(FILL_RANK, value=CatalogProduct.last_tier, else_=len(FILL_RANK))
    return (
        CatalogProduct.listing_discount_pct.is_(None),
        (CatalogProduct.listing_discount_pct * popularity).desc(),
        rank,
        CatalogProduct.last_score.desc(),
    )


async def active_targets(db: AsyncSession) -> list[Channel]:
    """Grupos que o "Aprovar" oferece: ativos, de nicho ativo."""
    rows = await db.execute(
        select(Channel)
        .join(Niche, Niche.id == Channel.niche_id)
        .where(Niche.active.is_(True), Channel.paused.is_(False))
        .order_by(Channel.id)
        .limit(50)
    )
    return list(rows.scalars())


def targets_for(niche_id: int | None, groups: list[Channel]) -> list[Channel]:
    """Os grupos que o "Aprovar" marca sozinho para um produto do nicho."""
    return [g for g in groups if g.niche_id == niche_id or g.always_checked]


class AutoApprover:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        tiers: tuple[str, ...],
        affiliate_tool_id: str,
    ) -> None:
        self._sessions = sessions
        self._priority = tuple(t for t in tiers if t in PRIORITY_TIERS)
        self._fill = tuple(t for t in tiers if t not in PRIORITY_TIERS)
        self._tool_id = affiliate_tool_id
        self._failed: dict[int, datetime] = {}
        self._next_fill_at: datetime | None = None

    def _undecided(self) -> list[ColumnElement[bool]]:
        since_change = func.coalesce(CatalogProduct.last_changed_at, CatalogProduct.first_seen_at)
        return [
            CatalogProduct.active.is_(True),
            CatalogProduct.operator_blocked.is_(False),
            ~exists().where(
                OperatorDecision.product_id == CatalogProduct.id,
                OperatorDecision.decided_at >= since_change,
            ),
            ~exists().where(
                Publication.product_id == CatalogProduct.id,
                Publication.created_at >= since_change,
            ),
        ]

    async def _priority_candidates(self, db: AsyncSession) -> list[int]:
        if not self._priority:
            return []
        rows = await db.execute(
            select(CatalogProduct.id)
            .where(CatalogProduct.last_tier.in_(self._priority), *self._undecided())
            .order_by(CatalogProduct.last_score.desc())
            .limit(BATCH * 4)
        )
        return list(rows.scalars())

    async def _fill_candidates(self, db: AsyncSession) -> list[int]:
        if not self._fill:
            return []
        discount_ok = or_(
            CatalogProduct.last_tier.not_in(NEEDS_LISTING_DISCOUNT),
            CatalogProduct.listing_discount_pct.is_not(None),
        )
        rows = await db.execute(
            select(CatalogProduct.id)
            .where(CatalogProduct.last_tier.in_(self._fill), discount_ok, *self._undecided())
            .order_by(*_fill_order())
            .limit(BATCH * 4)
        )
        return list(rows.scalars())

    async def _room(self, db: AsyncSession, groups: list[Channel]) -> dict[int, int]:
        waiting = dict(
            (
                await db.execute(
                    select(Publication.channel_id, func.count())
                    .where(
                        Publication.channel_id.in_([g.id for g in groups]),
                        Publication.state.in_(WAITING),
                    )
                    .group_by(Publication.channel_id)
                )
            )
            .tuples()
            .all()
        )
        return {g.id: queue_target(g) - waiting.get(g.id, 0) for g in groups}

    def _may_retry(self, product_id: int, now: datetime) -> bool:
        failed_at = self._failed.get(product_id)
        return failed_at is None or now - failed_at > RETRY_AFTER

    async def run(self, now: datetime) -> int:
        if not self._priority and not self._fill:
            return 0
        async with self._sessions() as db:
            groups = await active_targets(db)
            if not groups:
                return 0
            room = await self._room(db, groups)
            priority = [p for p in await self._priority_candidates(db) if self._may_retry(p, now)]
            look_fill = any(r > 0 for r in room.values()) and (
                self._next_fill_at is None or now >= self._next_fill_at
            )
            fill = (
                [p for p in await self._fill_candidates(db) if self._may_retry(p, now)]
                if look_fill
                else []
            )
        approved = filled = 0
        for product_id, limited in [(p, False) for p in priority] + [(p, True) for p in fill]:
            if approved >= BATCH or (limited and not any(r > 0 for r in room.values())):
                break
            if await self._approve(product_id, groups, room, limited=limited, now=now):
                approved += 1
                filled += limited
        if look_fill:
            self._next_fill_at = None if filled else now + FILL_PAUSE
        return approved

    async def _approve(
        self,
        product_id: int,
        groups: list[Channel],
        room: dict[int, int],
        *,
        limited: bool,
        now: datetime,
    ) -> bool:
        """`limited`: nível que só enche a fila (vai só para grupo com vaga)."""
        tiers = self._fill if limited else self._priority
        try:
            async with self._sessions() as db, db.begin():
                product = await db.get(CatalogProduct, product_id)
                evaluation = (
                    await db.execute(
                        select(OfferEvaluation)
                        .where(OfferEvaluation.product_id == product_id)
                        .order_by(OfferEvaluation.id.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if product is None or evaluation is None or evaluation.tier not in tiers:
                    return False
                channels = targets_for(product.niche_id, groups)
                if not channels:
                    # Nenhum grupo ativo para ele: nada gravado, nem decisão; tenta depois.
                    self._failed[product_id] = now
                    return False
                if limited:
                    channels = [c for c in channels if room.get(c.id, 0) > 0]
                    if not channels:
                        return False  # os grupos dele estão cheios: entra quando abrir vaga
                decision = OperatorDecision(
                    product_id=product_id,
                    evaluation_id=evaluation.id,
                    decision="approved",
                    actor=ROBOT_APPROVER,
                    decided_at=now,
                )
                db.add(decision)
                await db.flush()
                result = await publications_for_approval(
                    db,
                    product=product,
                    evaluation=evaluation,
                    decision=decision,
                    now=now,
                    affiliate_tool_id=self._tool_id,
                    channels=list(
                        (
                            await db.execute(
                                select(Channel)
                                .where(Channel.id.in_([c.id for c in channels]))
                                .order_by(Channel.id)
                            )
                        ).scalars()
                    ),
                )
        except DistributionError:
            # Sem preço que dê para publicar: nada gravado; tenta depois.
            self._failed[product_id] = now
            return False
        for channel in channels:
            room[channel.id] = room.get(channel.id, 0) - 1
        logger.info(
            "aprovação automática",
            extra={
                "product_id": product_id,
                "tier": evaluation.tier,
                "publications": result.created,
                "groups": [c.id for c in channels],
                "filling": limited,
            },
        )
        return True
