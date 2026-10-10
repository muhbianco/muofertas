"""Convite de operador: link de uso único por e-mail; o convidado define senha e autenticador.

- Token de 256 bits no link; o banco guarda só o hash. Vale 48 h e uma vez.
- O segredo TOTP nasce na primeira visita ao link e fica na linha do convite até o primeiro
  código certo; só então vira operador (senha em argon2, como os outros).
- 10 tentativas erradas invalidam o convite.
- O e-mail sai pelo fluxo `megapromooz-convite` do n8n: o texto é fixo lá e só o link vai daqui,
  então o webhook não serve para mandar mensagem arbitrária.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.models import AdminInvite, AdminUser
from app.panel.security import hash_password, new_totp_secret, token_hash, verify_totp

logger = get_logger(__name__)
INVITE_TTL = timedelta(hours=48)
MAX_ATTEMPTS = 10
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,40}$")


class InviteError(Exception):
    """Recusa com mensagem pronta para a tela."""


def invite_link(settings: Settings, token: str) -> str:
    return f"{settings.panel_url.rstrip('/')}/convite/{token}"


async def create_invite(
    db: AsyncSession, *, email: str, actor: str, now: datetime
) -> tuple[AdminInvite, str]:
    email = email.strip().lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise InviteError("e-mail inválido")
    # Um convite valendo por e-mail: o novo substitui o anterior ainda não usado.
    await db.execute(
        delete(AdminInvite).where(AdminInvite.email == email, AdminInvite.used_at.is_(None))
    )
    token = secrets.token_urlsafe(32)
    invite = AdminInvite(
        email=email,
        token_hash=token_hash(token),
        created_by=actor,
        created_at=now,
        expires_at=now + INVITE_TTL,
        attempts=0,
    )
    db.add(invite)
    await db.flush()
    return invite, token


async def send_invite_email(
    http: httpx.AsyncClient, settings: Settings, *, email: str, link: str
) -> bool:
    if not settings.invite_email_webhook_url:
        return False
    token = settings.alert_email_webhook_token.get_secret_value()
    try:
        response = await http.post(
            settings.invite_email_webhook_url,
            json={"app": "megapromooz", "kind": "invite", "to": email, "link": link},
            headers={"authorization": token} if token else {},
        )
    except httpx.HTTPError as exc:
        logger.warning("convite: e-mail não saiu", extra={"error": type(exc).__name__})
        return False
    if response.status_code >= 300:
        logger.warning("convite: e-mail recusado", extra={"status": response.status_code})
        return False
    return True


async def valid_invite(db: AsyncSession, token: str, now: datetime) -> AdminInvite | None:
    if not token or len(token) > 100:
        return None
    invite = (
        await db.execute(select(AdminInvite).where(AdminInvite.token_hash == token_hash(token)))
    ).scalar_one_or_none()
    if (
        invite is None
        or invite.used_at is not None
        or invite.expires_at <= now
        or invite.attempts >= MAX_ATTEMPTS
    ):
        return None
    return invite


def ensure_secret(invite: AdminInvite) -> str:
    if not invite.totp_secret:
        invite.totp_secret = new_totp_secret()
    return invite.totp_secret


@dataclass(frozen=True, slots=True)
class Acceptance:
    username: str
    password: str
    password_again: str
    code: str


async def accept(
    db: AsyncSession, invite: AdminInvite, form: Acceptance, now: datetime
) -> AdminUser:
    """Cria o operador. Erro de digitação conta tentativa; o código vale uma vez (replay)."""
    username = form.username.strip().lower()
    problem = None
    if not USERNAME_RE.match(username):
        problem = "usuário: de 3 a 40 caracteres, só minúsculas, números, ponto, hífen e _"
    elif len(form.password) < 12 or len(form.password) > 128:
        problem = "senha: de 12 a 128 caracteres"
    elif form.password != form.password_again:
        problem = "as duas senhas não são iguais"
    elif (
        await db.execute(select(AdminUser.id).where(AdminUser.username == username))
    ).scalar_one_or_none() is not None:
        problem = "esse usuário já existe; escolha outro"
    step = None
    if problem is None:
        secret = ensure_secret(invite)
        step = verify_totp(secret, form.code, last_step=0, now=now.timestamp())
        if step is None:
            problem = "código do autenticador não confere (o relógio do celular está certo?)"
    if problem is not None or step is None:
        invite.attempts += 1
        raise InviteError(problem or "código não confere")
    user = AdminUser(
        username=username,
        password_hash=hash_password(form.password),
        totp_secret=invite.totp_secret or "",
        totp_last_step=step,
        active=True,
        created_at=now,
    )
    db.add(user)
    invite.used_at = now
    invite.username = username
    invite.totp_secret = None  # o segredo agora mora no operador
    await db.flush()
    return user
