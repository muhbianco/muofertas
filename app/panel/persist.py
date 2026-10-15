"""Gravar antes de responder.

O FastAPI (0.141) roda o fim da dependência com `yield` (o commit da sessão) depois de mandar a
resposta: um commit que falha chegava ao operador como "feito" (medido em 15/10/2026). Toda rota do
painel que grava chama `save` antes de montar a resposta; a falha vira `SaveFailed`, que o app
mostra como "nada foi salvo" (`tests/test_grava_antes.py` cobra isso de cada rota nova).
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession


class SaveFailed(Exception):
    """O banco recusou a gravação; nada do pedido ficou salvo."""

    def __init__(self, *, conflict: bool) -> None:
        super().__init__("conflito" if conflict else "falha no banco")
        self.conflict = conflict


async def save(db: AsyncSession) -> None:
    try:
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise SaveFailed(conflict=isinstance(exc, IntegrityError)) from exc
