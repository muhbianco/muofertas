"""Operadores do painel: lista, convite por e-mail e a página pública do convite."""

from __future__ import annotations

from typing import Annotated

import httpx
import segno
from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import delete, select

from app.core.clock import utcnow
from app.core.config import settings
from app.core.logging import get_logger
from app.models import AdminInvite, AdminSession, AdminUser
from app.panel import invites
from app.panel.auth import check_csrf
from app.panel.routes import Admin, Db, templates
from app.panel.security import otpauth_uri

logger = get_logger(__name__)
router = APIRouter()


async def _render(
    request: Request,
    db: Db,
    ctx: Admin,
    *,
    notice: str | None = None,
    error: str | None = None,
    manual_link: str | None = None,
    code: int = 200,
) -> Response:
    users = list((await db.execute(select(AdminUser).order_by(AdminUser.id).limit(50))).scalars())
    pending = list(
        (
            await db.execute(
                select(AdminInvite)
                .where(AdminInvite.used_at.is_(None), AdminInvite.expires_at > utcnow())
                .order_by(AdminInvite.id.desc())
                .limit(50)
            )
        ).scalars()
    )
    return templates.TemplateResponse(
        request,
        "operadores.html",
        {
            "ctx": ctx,
            "users": users,
            "pending": pending,
            "notice": notice,
            "error": error,
            "manual_link": manual_link,
        },
        status_code=code,
    )


@router.get("/operadores", response_class=HTMLResponse)
async def operators_page(request: Request, db: Db, ctx: Admin) -> Response:
    return await _render(request, db, ctx)


@router.post("/operadores/convite", response_class=HTMLResponse)
async def invite(
    request: Request,
    db: Db,
    ctx: Admin,
    email: Annotated[str, Form(max_length=254)],
    csrf: Annotated[str | None, Form()] = None,
) -> Response:
    check_csrf(ctx, csrf)
    try:
        row, token = await invites.create_invite(db, email=email, actor=ctx.username, now=utcnow())
    except invites.InviteError as exc:
        return await _render(request, db, ctx, error=str(exc), code=422)
    invited = row.email
    await db.commit()  # o convite existe antes de chamar o n8n
    link = invites.invite_link(settings, token)
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as http:
        sent = await invites.send_invite_email(http, settings, email=invited, link=link)
    logger.info("convite criado", extra={"user": ctx.username, "emailed": sent})
    if sent:
        return await _render(request, db, ctx, notice=f"Convite enviado para {invited}.")
    # Sem e-mail: o link aparece só nesta resposta (nunca em URL ou log) para mandar à mão.
    return await _render(
        request,
        db,
        ctx,
        error="O e-mail não saiu. Mande este link à pessoa (vale 48 h, uma vez):",
        manual_link=link,
    )


@router.post("/operadores/{user_id}/desativar")
async def deactivate(
    db: Db, ctx: Admin, user_id: int, csrf: Annotated[str | None, Form()] = None
) -> Response:
    check_csrf(ctx, csrf)
    if user_id == ctx.user_id:
        raise HTTPException(status_code=409, detail="não dá para desativar a si mesmo")
    user = await db.get(AdminUser, user_id)
    if user is None:
        raise HTTPException(status_code=404)
    user.active = False
    await db.execute(delete(AdminSession).where(AdminSession.user_id == user_id))
    logger.warning("operador desativado", extra={"target": user.username, "user": ctx.username})
    return RedirectResponse("/operadores", status_code=status.HTTP_303_SEE_OTHER)


# --------------------------------------------------------------------------- página pública


def _qr(secret: str, email: str) -> str:
    return segno.make(otpauth_uri(secret, email), error="m").svg_data_uri(scale=5, border=2)


async def _invite_page(
    request: Request, invite: AdminInvite, *, error: str | None = None, code: int = 200
) -> Response:
    secret = invites.ensure_secret(invite)
    grouped = " ".join(secret[i : i + 4] for i in range(0, len(secret), 4))
    return templates.TemplateResponse(
        request,
        "convite.html",
        {
            "ctx": None,
            "valid": True,
            "email": invite.email,
            "qr": _qr(secret, invite.email),
            "key": grouped,
            "error": error,
        },
        status_code=code,
    )


@router.get("/convite/{token}", response_class=HTMLResponse)
async def invite_page(request: Request, db: Db, token: str) -> Response:
    invite = await invites.valid_invite(db, token, utcnow())
    if invite is None:
        return templates.TemplateResponse(
            request, "convite.html", {"ctx": None, "valid": False}, status_code=404
        )
    return await _invite_page(request, invite)


@router.post("/convite/{token}", response_class=HTMLResponse)
async def accept_invite(
    request: Request,
    db: Db,
    token: str,
    username: Annotated[str, Form(max_length=40)],
    password: Annotated[str, Form(max_length=128)],
    password_again: Annotated[str, Form(max_length=128)],
    code: Annotated[str, Form(max_length=10)],
) -> Response:
    now = utcnow()
    invite = await invites.valid_invite(db, token, now)
    if invite is None:
        return templates.TemplateResponse(
            request, "convite.html", {"ctx": None, "valid": False}, status_code=404
        )
    try:
        user = await invites.accept(
            db,
            invite,
            invites.Acceptance(username, password, password_again, code),
            now,
        )
    except invites.InviteError as exc:
        await db.commit()  # a tentativa errada conta
        return await _invite_page(request, invite, error=str(exc), code=422)
    logger.info("operador criado por convite", extra={"username": user.username})
    return templates.TemplateResponse(
        request, "convite.html", {"ctx": None, "valid": True, "done": user.username}
    )
