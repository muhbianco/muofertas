"""Aprovação automática das melhores ofertas (plano, semana 3: "Excelente+").

Faz o mesmo que o botão Aprovar, como ator "aprovação automática". Só entra produto com nota nos
níveis configurados (`AUTO_APPROVE_TIERS`, padrão excepcional e excelente), e fica de fora quem o
operador já decidiu desde a última mudança de preço (aprovou, rejeitou ou bloqueou) e quem já
virou publicação. Sem histórico (antes de ~10 dias), nenhum produto chega a esses níveis: o passo
fica parado até a nota comprovar desconto.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.distribution.service import DistributionError, publications_for_approval
from app.models import CatalogProduct, OfferEvaluation, OperatorDecision, Publication

logger = get_logger(__name__)
ROBOT_APPROVER = "aprovação automática"
BATCH = 5
# Produto que não deu para aprovar (sem preço fresco): tenta de novo depois disso.
RETRY_AFTER = timedelta(hours=1)


class AutoApprover:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        tiers: tuple[str, ...],
        affiliate_tool_id: str,
    ) -> None:
        self._sessions = sessions
        self._tiers = tiers
        self._tool_id = affiliate_tool_id
        self._failed: dict[int, datetime] = {}

    async def _candidates(self, now: datetime) -> list[int]:
        since_change = func.coalesce(CatalogProduct.last_changed_at, CatalogProduct.first_seen_at)
        async with self._sessions() as db:
            rows = (
                await db.execute(
                    select(CatalogProduct.id)
                    .where(
                        CatalogProduct.active.is_(True),
                        CatalogProduct.operator_blocked.is_(False),
                        CatalogProduct.last_tier.in_(self._tiers),
                        ~exists().where(
                            OperatorDecision.product_id == CatalogProduct.id,
                            OperatorDecision.decided_at >= since_change,
                        ),
                        ~exists().where(
                            Publication.product_id == CatalogProduct.id,
                            Publication.created_at >= since_change,
                        ),
                    )
                    .order_by(CatalogProduct.last_score.desc())
                    .limit(BATCH * 4)
                )
            ).scalars()
        return [pid for pid in rows if self._may_retry(pid, now)][:BATCH]

    def _may_retry(self, product_id: int, now: datetime) -> bool:
        failed_at = self._failed.get(product_id)
        return failed_at is None or now - failed_at > RETRY_AFTER

    async def run(self, now: datetime) -> int:
        if not self._tiers:
            return 0
        approved = 0
        for product_id in await self._candidates(now):
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
                    if product is None or evaluation is None or evaluation.tier not in self._tiers:
                        continue
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
                    )
            except DistributionError:
                # Sem preço que dê para publicar: nada gravado; tenta depois.
                self._failed[product_id] = now
                continue
            approved += 1
            logger.info(
                "aprovação automática",
                extra={
                    "product_id": product_id,
                    "tier": evaluation.tier,
                    "publications": result.created,
                    "no_channel": result.no_channel,
                },
            )
        return approved
