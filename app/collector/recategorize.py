"""Trocar o nicho de um produto pelo painel.

O nicho vem da categoria do ML pela qual o produto foi descoberto, e as categorias do ML misturam
coisas (Ciclismo traz bomba de ar e compressor de pneu de carro). O operador corrige: o produto
muda de nicho, fica travado lá (o `seed-niches` não o move de volta) e volta para a avaliação com
as regras do nicho novo (comissão, loja oficial, marcas de risco).

O que ainda esperava link ou horário num grupo do nicho antigo sai dessa fila: era o grupo
errado. O que está saindo agora (`sending`) ou já saiu fica como está.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.distribution.service import discard
from app.models import CatalogProduct, Channel, Niche, Publication

logger = get_logger(__name__)

PENDING = ("awaiting_link", "ready")
PUBLICATION_LIMIT = 50


class RecategorizeError(ValueError):
    """Mensagem pronta para o operador."""


class ProductNotFound(Exception):
    """Produto inexistente (a rota responde 404). Não herda de LookupError: um KeyError de
    verdade no meio do caminho não pode virar 404."""


@dataclass(frozen=True, slots=True)
class Recategorized:
    niche: str
    changed: bool
    discarded: int = 0
    domain_id: str | None = None


async def recategorize(
    db: AsyncSession, *, product_id: int, niche_id: int, actor: str
) -> Recategorized:
    """Move o produto para o nicho escolhido. Não faz commit: a rota fecha a unidade de trabalho."""
    product = await db.get(CatalogProduct, product_id, with_for_update=True)
    if product is None:
        raise ProductNotFound(product_id)
    niche = await db.get(Niche, niche_id)
    if niche is None or not niche.active:
        raise RecategorizeError("Escolha um nicho ativo.")
    if product.niche_id == niche.id:
        product.niche_locked = True  # o operador confirmou o nicho atual
        return Recategorized(niche.name, changed=False, domain_id=product.domain_id)
    previous = product.niche_id
    product.niche_id = niche.id
    product.niche_locked = True
    product.needs_evaluation = True
    discarded = await discard_pending(db, [product.id], niche, actor=actor)
    logger.info(
        "nicho trocado",
        extra={
            "product_id": product.id,
            "from": previous,
            "to": niche.id,
            "user": actor,
            "discarded": discarded,
        },
    )
    return Recategorized(niche.name, changed=True, discarded=discarded, domain_id=product.domain_id)


async def discard_pending(
    db: AsyncSession, product_ids: list[int], niche: Niche, *, actor: str
) -> int:
    """Tira da fila de envio dos grupos de outro nicho o que ainda esperava link ou horário."""
    if not product_ids:
        return 0
    pending = (
        await db.execute(
            select(Publication)
            .join(Channel, Channel.id == Publication.channel_id)
            .where(
                Publication.product_id.in_(product_ids),
                Publication.state.in_(PENDING),
                Channel.niche_id != niche.id,
            )
            .limit(PUBLICATION_LIMIT)
            .with_for_update()
        )
    ).scalars()
    discarded = 0
    for pub in pending:
        discard(pub, actor=actor, reason=f"produto movido para o nicho {niche.name}")
        discarded += 1
    return discarded
