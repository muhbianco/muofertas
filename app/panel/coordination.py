"""Dois operadores na mesma fila de aprovação (pedido do dono em 15/10/2026).

- `conflict`: o /decisao recusa aprovar (ou rejeitar) o que já foi aprovado e ainda vale, e o que
  outro operador decidiu depois que a página abriu. Antes, a segunda aprovação respondia
  "Aprovada." sem fazer nada e jogava fora o Pix e o cupom de quem chegou depois.
- `claim`/`release`/`board`: a marca ✓/✕ de cada operador fica no banco, para os outros verem "em
  aprovação por X" e não marcarem o mesmo produto. A marca de um operador só trava os outros
  enquanto alguma aba dele dá sinal (`seen_at`); some sozinha depois de algumas horas.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.distribution import DEDUP_WINDOW
from app.models import ApprovalClaim, CatalogProduct, OperatorDecision, Publication

MARKS = ("approved", "rejected")
# A marca de outro operador trava enquanto a aba dele está aberta: ela dá sinal a cada 15 s, e a
# cada minuto quando fica escondida (o operador foi conferir a oferta noutra aba).
CLAIM_ACTIVE = timedelta(minutes=5)
# Depois disso a marca some (aba esquecida, operador foi embora).
CLAIM_KEEP = timedelta(hours=6)
# Publicação que ainda vale: aprovar de novo seria duplicar a intenção.
LIVE_STATES = ("awaiting_link", "ready", "sending", "unknown", "sent")
BOARD_LIMIT = 60  # cards por página da fila (50) com folga
STALE_BATCH = 200


@dataclass(frozen=True, slots=True)
class Conflict:
    actor: str
    decision: str  # approved | rejected | blocked
    at: datetime


@dataclass(frozen=True, slots=True)
class Holder:
    actor: str
    mark: str
    since: datetime


@dataclass(frozen=True, slots=True)
class Board:
    mine: dict[int, str]
    others: dict[int, Holder]
    decided: dict[int, Conflict]


async def conflict(
    db: AsyncSession,
    product: CatalogProduct,
    *,
    actor: str,
    seen_at: datetime | None,
    now: datetime,
) -> Conflict | None:
    """Por que esta decisão chega tarde, ou None.

    Leituras com trava compartilhada: com a linha do produto travada pelo chamador, elas veem o que
    a outra transação acabou de gravar (a leitura comum do InnoDB veria a foto do início)."""
    since = max(product.last_changed_at or product.first_seen_at, now - DEDUP_WINDOW)
    live = (
        await db.execute(
            select(Publication.created_at, OperatorDecision.actor)
            .outerjoin(OperatorDecision, OperatorDecision.id == Publication.decision_id)
            .where(
                Publication.product_id == product.id,
                Publication.created_at >= since,
                Publication.state.in_(LIVE_STATES),
            )
            .order_by(Publication.created_at.desc())
            .limit(1)
            .with_for_update(read=True)
        )
    ).first()
    if live is not None:
        return Conflict(actor=live.actor or "envio automático", decision="approved", at=live[0])
    if seen_at is None:
        return None
    other = (
        await db.execute(
            select(OperatorDecision.actor, OperatorDecision.decision, OperatorDecision.decided_at)
            .where(
                OperatorDecision.product_id == product.id,
                OperatorDecision.decided_at > seen_at,
                OperatorDecision.actor != actor,
            )
            .order_by(OperatorDecision.decided_at.desc())
            .limit(1)
            .with_for_update(read=True)
        )
    ).first()
    if other is None:
        return None
    return Conflict(actor=other.actor, decision=other.decision, at=other.decided_at)


async def claim(
    db: AsyncSession, *, product_id: int, actor: str, mark: str, now: datetime
) -> Holder | None:
    """Marca o produto para `actor`. Devolve quem está com ele quando é outro operador ativo."""
    row = (
        await db.execute(
            select(ApprovalClaim).where(ApprovalClaim.product_id == product_id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        db.add(
            ApprovalClaim(
                product_id=product_id, actor=actor, mark=mark, claimed_at=now, seen_at=now
            )
        )
        return None
    if row.actor != actor and now - row.seen_at < CLAIM_ACTIVE:
        return Holder(actor=row.actor, mark=row.mark, since=row.claimed_at)
    if row.actor != actor:
        row.actor, row.claimed_at = actor, now  # a aba do outro fechou: assume
    row.mark, row.seen_at = mark, now
    return None


async def release(db: AsyncSession, *, product_id: int, actor: str) -> None:
    await db.execute(
        delete(ApprovalClaim).where(
            ApprovalClaim.product_id == product_id, ApprovalClaim.actor == actor
        )
    )


async def settle(db: AsyncSession, product_id: int) -> None:
    """Produto decidido: ninguém mais está com ele."""
    await db.execute(delete(ApprovalClaim).where(ApprovalClaim.product_id == product_id))


async def board(
    db: AsyncSession,
    *,
    product_ids: list[int],
    actor: str,
    since: datetime | None,
    now: datetime,
) -> Board:
    """Estado da página de um operador: as marcas dele, as dos outros e o que foi decidido depois
    que a página abriu. Renova o sinal das marcas dele (a aba está aberta)."""
    ids = product_ids[:BOARD_LIMIT]
    stale = list(
        (
            await db.execute(
                select(ApprovalClaim.product_id)
                .where(ApprovalClaim.seen_at < now - CLAIM_KEEP)
                .limit(STALE_BATCH)
            )
        ).scalars()
    )
    if stale:
        await db.execute(delete(ApprovalClaim).where(ApprovalClaim.product_id.in_(stale)))
    if not ids:
        return Board(mine={}, others={}, decided={})
    await db.execute(
        update(ApprovalClaim)
        .where(ApprovalClaim.product_id.in_(ids), ApprovalClaim.actor == actor)
        .values(seen_at=now)
    )
    mine: dict[int, str] = {}
    others: dict[int, Holder] = {}
    claims = (
        await db.execute(select(ApprovalClaim).where(ApprovalClaim.product_id.in_(ids)))
    ).scalars()
    for row in claims:
        if row.actor == actor:
            mine[row.product_id] = row.mark
        elif now - row.seen_at < CLAIM_ACTIVE:
            others[row.product_id] = Holder(actor=row.actor, mark=row.mark, since=row.claimed_at)
    decided: dict[int, Conflict] = {}
    if since is not None:
        rows = (
            await db.execute(
                select(
                    OperatorDecision.product_id,
                    OperatorDecision.actor,
                    OperatorDecision.decision,
                    OperatorDecision.decided_at,
                )
                .where(OperatorDecision.product_id.in_(ids), OperatorDecision.decided_at >= since)
                .order_by(OperatorDecision.decided_at.desc())
                .limit(BOARD_LIMIT * 4)
            )
        ).all()
        for product_id, who, what, when in rows:
            decided.setdefault(product_id, Conflict(actor=who, decision=what, at=when))
    return Board(mine=mine, others=others, decided=decided)
