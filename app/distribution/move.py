"""Mudar de grupo um post que ainda não saiu (tela Postar).

O operador aprovou no grupo errado. A troca vale só para este produto (sem "todos os do tipo"):
ele passa para o nicho escolhido, como no "Trocar nicho", e o post sai da fila do grupo antigo e
entra no fim da fila do grupo do nicho novo. A aprovação é a mesma; Pix, cupom e últimas unidades
vão junto com a hora em que foram vistos (a validade não se renova). O link é refeito, porque a
etiqueta de afiliado é de cada grupo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collector.recategorize import recategorize
from app.core.logging import get_logger
from app.distribution.service import (
    DistributionError,
    discard,
    publications_for_approval,
)
from app.domain.distribution import pix_to_show, units_to_show
from app.models import CatalogProduct, Channel, OfferEvaluation, OperatorDecision, Publication

logger = get_logger(__name__)
MOVABLE = ("awaiting_link", "ready")


class PublicationNotFound(Exception):
    """Publicação inexistente (a rota responde 404); não herda de LookupError (ver recategorize)."""


@dataclass
class Moved:
    channel_id: int | None  # grupo novo (o primeiro do nicho), para voltar à tela nele
    created: int = 0
    skipped: list[str] = field(default_factory=list)


async def move_publication(
    db: AsyncSession,
    *,
    publication_id: int,
    niche_id: int,
    actor: str,
    now: datetime,
    affiliate_tool_id: str = "",
) -> Moved:
    """Não faz commit: a rota fecha a unidade de trabalho."""
    pub = await db.get(Publication, publication_id, with_for_update=True)
    if pub is None:
        raise PublicationNotFound(publication_id)
    if pub.state not in MOVABLE:
        raise DistributionError("só dá para mudar de grupo o que ainda não saiu")
    current = await db.get(Channel, pub.channel_id)
    if current is not None and current.niche_id == niche_id:
        raise DistributionError("o post já está no grupo desse nicho")
    evaluation = await db.get(OfferEvaluation, pub.evaluation_id) if pub.evaluation_id else None
    decision = await db.get(OperatorDecision, pub.decision_id) if pub.decision_id else None
    if evaluation is None or decision is None:
        raise DistributionError("esta publicação não tem a aprovação registrada; aprove de novo")

    # 1) o produto vai para o nicho escolhido (só ele) e sai das filas dos outros nichos
    moved_to = await recategorize(db, product_id=pub.product_id, niche_id=niche_id, actor=actor)
    if pub.state in MOVABLE:  # produto já estava no nicho certo e o post no grupo errado
        discard(pub, actor=actor, reason=f"movida para o nicho {moved_to.niche}")

    # 2) entra no fim da fila do grupo novo, com a mesma aprovação
    product = await db.get(CatalogProduct, pub.product_id)
    assert product is not None
    seen = pub.pix_at or pub.coupon_at or pub.last_units_at or pub.created_at
    result = await publications_for_approval(
        db,
        product=product,
        evaluation=evaluation,
        decision=decision,
        now=now,
        pix_cents=pix_to_show(pub.pix_price_cents, pub.pix_at, evaluation.price_cents or 0, now)
        if evaluation.price_cents
        else None,
        coupon_cents=pix_to_show(
            pub.coupon_price_cents, pub.coupon_at, evaluation.price_cents or 0, now
        )
        if evaluation.price_cents
        else None,
        last_units=units_to_show(pub.last_units, pub.last_units_at, now),
        seen_at=seen,
        affiliate_tool_id=affiliate_tool_id,
        key_tag=f":mv{pub.id}",
    )
    target = (
        await db.execute(
            select(Channel.id).where(Channel.niche_id == niche_id).order_by(Channel.id).limit(1)
        )
    ).scalar_one_or_none()
    logger.info(
        "post mudou de grupo",
        extra={
            "publication_id": pub.id,
            "from_channel": pub.channel_id,
            "to_niche": niche_id,
            "publications": result.created,
            "user": actor,
        },
    )
    return Moved(target, result.created, list(result.skipped or []))
