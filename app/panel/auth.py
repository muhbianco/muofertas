"""Sessão do painel: cookie com token aleatório, banco com o hash, CSRF por sessão."""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.database import get_session
from app.models import AdminSession, AdminUser
from app.panel.security import new_session_token, token_hash

SESSION_COOKIE = "mpo_session"
SESSION_TTL = timedelta(hours=12)


class LoginRequiredError(Exception):
    """Sem sessão válida: o handler manda para /login."""


@dataclass(frozen=True, slots=True)
class AuthContext:
    user_id: int
    username: str
    csrf_token: str


async def create_session(db: AsyncSession, user: AdminUser) -> tuple[str, str]:
    token, hashed = new_session_token()
    csrf = secrets.token_urlsafe(24)
    now = utcnow()
    # Uma sessão só por operador: logar de novo derruba a anterior.
    await db.execute(delete(AdminSession).where(AdminSession.user_id == user.id))
    db.add(
        AdminSession(
            token_hash=hashed,
            csrf_token=csrf,
            user_id=user.id,
            created_at=now,
            expires_at=now + SESSION_TTL,
        )
    )
    return token, csrf


async def destroy_session(db: AsyncSession, token: str | None) -> None:
    if token:
        await db.execute(delete(AdminSession).where(AdminSession.token_hash == token_hash(token)))


async def require_admin(request: Request, db: AsyncSession = Depends(get_session)) -> AuthContext:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise LoginRequiredError
    row = (
        await db.execute(
            select(AdminSession, AdminUser)
            .join(AdminUser, AdminUser.id == AdminSession.user_id)
            .where(AdminSession.token_hash == token_hash(token))
        )
    ).first()
    if row is None:
        raise LoginRequiredError
    sess, user = row
    if sess.expires_at <= utcnow() or not user.active:
        await db.execute(delete(AdminSession).where(AdminSession.id == sess.id))
        raise LoginRequiredError
    return AuthContext(user_id=user.id, username=user.username, csrf_token=sess.csrf_token)


def check_csrf(ctx: AuthContext, submitted: str | None) -> None:
    if not submitted or not hmac.compare_digest(submitted, ctx.csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf")
