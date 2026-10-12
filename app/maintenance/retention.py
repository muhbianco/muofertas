"""Limpeza diária do que só serve por um tempo.

- `collection_run` (uma linha por lote do coletor, ~7 mil por dia) e `integration_error`: somem com
  30 dias. O painel e o alerta só olham as execuções recentes.
- `offer_evaluation` (uma por produto por dia, mais as de mudança de preço): some com 90 dias,
  menos a última de cada produto e as que uma decisão do operador ou um envio citam (trilha de
  auditoria).
- `price_observation` nunca: o histórico de preço é o ativo do produto.

Roda uma vez por dia, depois das 4h de Brasília, com o lease do worker. Apaga em lotes curtos (cada
um na sua transação), com teto por execução: o que sobrar fica para o dia seguinte.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Select, delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.core.config import Settings
from app.core.logging import get_logger
from app.models import (
    CollectionRun,
    IntegrationError,
    OfferEvaluation,
    OperatorDecision,
    Publication,
)

logger = get_logger(__name__)
SP = ZoneInfo("America/Sao_Paulo")
BATCH = 2000
MAX_BATCHES = 25  # por tabela e por execução: até 50 mil linhas


@dataclass(frozen=True, slots=True)
class Cleaned:
    runs: int
    errors: int
    evaluations: int


class Retention:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self._sessions = sessions
        self._settings = settings
        self._last_day: date | None = None

    async def run(self, now: datetime) -> Cleaned | None:
        """Limpa se ainda não limpou hoje e já passou da hora. None quando não era a vez."""
        local = now.astimezone(SP)
        if local.hour < self._settings.retention_hour or self._last_day == local.date():
            return None
        runs_cutoff = now - timedelta(days=self._settings.retention_runs_days)
        eval_cutoff = now - timedelta(days=self._settings.retention_evaluations_days)
        result = Cleaned(
            runs=await self._prune(self._old_runs(runs_cutoff), CollectionRun),
            errors=await self._prune(self._old_errors(runs_cutoff), IntegrationError),
            evaluations=await self._prune(self._old_evaluations(eval_cutoff), OfferEvaluation),
        )
        self._last_day = local.date()
        logger.info(
            "limpeza diária",
            extra={"runs": result.runs, "errors": result.errors, "evaluations": result.evaluations},
        )
        return result

    async def _prune(
        self,
        ids_query: Select[tuple[int]],
        model: type[CollectionRun] | type[IntegrationError] | type[OfferEvaluation],
    ) -> int:
        total = 0
        for _ in range(MAX_BATCHES):
            async with self._sessions() as session, session.begin():
                ids = list((await session.execute(ids_query.limit(BATCH))).scalars())
                if not ids:
                    break
                await session.execute(delete(model).where(model.id.in_(ids)))
            total += len(ids)
            if len(ids) < BATCH:
                break
        return total

    @staticmethod
    def _old_runs(cutoff: datetime) -> Select[tuple[int]]:
        # O id cresce com o tempo: em ordem de id, as velhas vêm primeiro e a busca para cedo.
        return (
            select(CollectionRun.id)
            .where(CollectionRun.started_at < cutoff)
            .order_by(CollectionRun.id)
        )

    @staticmethod
    def _old_errors(cutoff: datetime) -> Select[tuple[int]]:
        return (
            select(IntegrationError.id)
            .where(IntegrationError.occurred_at < cutoff)
            .order_by(IntegrationError.id)
        )

    @staticmethod
    def _old_evaluations(cutoff: datetime) -> Select[tuple[int]]:
        newer = aliased(OfferEvaluation)
        return (
            select(OfferEvaluation.id)
            .where(
                OfferEvaluation.evaluated_at < cutoff,
                # a última do produto fica (o painel mostra a nota atual a partir dela)
                exists().where(
                    newer.product_id == OfferEvaluation.product_id, newer.id > OfferEvaluation.id
                ),
                ~exists().where(OperatorDecision.evaluation_id == OfferEvaluation.id),
                ~exists().where(Publication.evaluation_id == OfferEvaluation.id),
            )
            .order_by(OfferEvaluation.id)
        )
