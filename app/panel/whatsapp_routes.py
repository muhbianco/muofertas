"""Painel do WhatsApp (Fase 2b): conectar o número e ligar os grupos aos nichos."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.core.config import settings
from app.core.database import get_sessionmaker
from app.core.logging import get_logger
from app.integrations.wuzapi import WuzapiClient, WuzapiError, digits
from app.panel.auth import check_csrf
from app.panel.routes import Admin, Db, templates
from app.whatsapp import service as wa

logger = get_logger(__name__)
router = APIRouter()

ERRORS = {
    "wuzapi": "O WhatsApp (WuzAPI) não respondeu ou recusou. Tente de novo em instantes.",
    "telefone": "Informe o número do chip com DDI e DDD (ex.: 5511999998888).",
    "confirmar": "Para desconectar, marque a confirmação.",
    "grupo": "Não deu para ligar esse grupo. Confira se o número está no grupo e é admin.",
}


async def get_wa_client(request: Request) -> WuzapiClient:
    client = getattr(request.app.state, "wa_client", None)
    if client is None:
        client = WuzapiClient(settings)
        request.app.state.wa_client = client
    return client


Wa = Annotated[WuzapiClient, Depends(get_wa_client)]
Sessions = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)]


def _see_other(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/numero", response_class=HTMLResponse)
async def number_page(
    request: Request, db: Db, ctx: Admin, client: Wa, sm: Sessions, erro: str | None = None
) -> Response:
    row = await wa.get_session(db, settings)
    await db.commit()  # nada de transação aberta durante a chamada à WuzAPI
    state = None
    qr = ""
    error = ERRORS.get(erro or "")
    if client.configured and row is not None:
        try:
            refreshed = await wa.refresh_session(sm, client, settings, utcnow())
            state = refreshed
            if refreshed is not None and not refreshed.logged_in:
                qr = await client.qr()
        except WuzapiError:
            error = error or ERRORS["wuzapi"]
    current = state or row
    return templates.TemplateResponse(
        request,
        "numero.html",
        {
            "ctx": ctx,
            "configured": client.configured,
            "session": current,
            "number": wa.masked_number(current.jid if current else None),
            "qr": qr,
            "code": None,
            "error": error,
        },
    )


@router.post("/numero/conectar")
async def connect(
    db: Db, ctx: Admin, client: Wa, sm: Sessions, csrf: Annotated[str | None, Form()] = None
) -> Response:
    check_csrf(ctx, csrf)
    await db.commit()
    try:
        await wa.create_session(sm, client, settings, utcnow())
    except WuzapiError:
        return _see_other("/numero?erro=wuzapi")
    logger.info("whatsapp: conectar", extra={"user": ctx.username})
    return _see_other("/numero")


@router.post("/numero/codigo", response_class=HTMLResponse)
async def pair_code(
    request: Request,
    db: Db,
    ctx: Admin,
    client: Wa,
    phone: Annotated[str, Form(max_length=20)],
    csrf: Annotated[str | None, Form()] = None,
) -> Response:
    """Código de pareamento: o número digitado vai só para a WuzAPI, não fica gravado."""
    check_csrf(ctx, csrf)
    if not 12 <= len(digits(phone)) <= 13:
        return _see_other("/numero?erro=telefone")
    row = await wa.get_session(db, settings)
    await db.commit()
    if row is None:
        return _see_other("/numero")
    try:
        code = await client.pair_code(phone)
    except WuzapiError:
        return _see_other("/numero?erro=wuzapi")
    return templates.TemplateResponse(
        request,
        "numero.html",
        {
            "ctx": ctx,
            "configured": True,
            "session": row,
            "number": None,
            "qr": "",
            "code": code,
            "error": None,
        },
    )


@router.post("/numero/sair")
async def logout(
    db: Db,
    ctx: Admin,
    client: Wa,
    csrf: Annotated[str | None, Form()] = None,
    confirm: Annotated[str | None, Form()] = None,
) -> Response:
    check_csrf(ctx, csrf)
    if confirm != "sim":
        return _see_other("/numero?erro=confirmar")
    await db.commit()
    try:
        await client.logout()
    except WuzapiError:
        return _see_other("/numero?erro=wuzapi")
    logger.warning("whatsapp: número desconectado pelo painel", extra={"user": ctx.username})
    return _see_other("/numero")


@router.post("/grupos/whatsapp/vincular")
async def link_group(
    db: Db,
    ctx: Admin,
    client: Wa,
    sm: Sessions,
    jid: Annotated[str, Form(max_length=64)],
    target: Annotated[str, Form(pattern=r"^[cn]:\d{1,9}$")],
    csrf: Annotated[str | None, Form()] = None,
    name: Annotated[str, Form(max_length=120)] = "",
) -> Response:
    """`target`: `c:<id>` liga a um grupo do painel; `n:<id>` cria um no nicho e liga."""
    check_csrf(ctx, csrf)
    await db.commit()
    kind, raw_id = target.split(":", 1)
    now = utcnow()
    try:
        channel_id = (
            int(raw_id)
            if kind == "c"
            else await wa.create_channel_for_group(sm, niche_id=int(raw_id), name=name, now=now)
        )
        await wa.link_group(sm, client, channel_id=channel_id, jid=jid, now=now)
    except WuzapiError:
        return _see_other("/grupos?erro=grupo")
    logger.info("whatsapp: grupo ligado", extra={"channel_id": channel_id, "user": ctx.username})
    return _see_other("/grupos")


@router.post("/grupos/whatsapp/so-admins")
async def admins_only(
    db: Db,
    ctx: Admin,
    client: Wa,
    jid: Annotated[str, Form(max_length=64)],
    csrf: Annotated[str | None, Form()] = None,
) -> Response:
    check_csrf(ctx, csrf)
    await db.commit()
    try:
        await client.set_announce(jid, True)
    except WuzapiError:
        return _see_other("/grupos?erro=grupo")
    return _see_other("/grupos")
