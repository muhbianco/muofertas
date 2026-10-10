"""Rotas do painel (HTML server-side, formulários comuns, pensado para celular)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import settings
from app.core.database import get_session
from app.core.logging import get_logger
from app.distribution.service import (
    DistributionError,
    PixRejectedError,
    publications_for_approval,
)
from app.domain.distribution import PixError, format_brl, parse_brl
from app.models import AdminUser, CatalogProduct, OfferEvaluation, OperatorDecision
from app.panel import queries
from app.panel.auth import (
    SESSION_COOKIE,
    SESSION_TTL,
    AuthContext,
    check_csrf,
    create_session,
    destroy_session,
    require_admin,
)
from app.panel.security import LoginThrottle, verify_password, verify_totp

logger = get_logger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
throttle = LoginThrottle()
SP = ZoneInfo("America/Sao_Paulo")
DECISIONS = {"approved": "aprovada", "rejected": "rejeitada", "blocked": "bloqueada"}
NOTICES = {
    "links": "Aprovada. Ela está em Links: gere o link do ML para postar.",
    "automatica": "Aprovada. Vai sozinha para o grupo no próximo horário livre.",
    "pronta": "Aprovada. O link sai em instantes e ela aparece em Postar.",
    "pix": "Não aprovada: o Pix precisa ser menor que o preço e até 30% abaixo (ex.: 49,90).",
    "sem-grupo": "Aprovada, mas o nicho não tem grupo cadastrado (Grupos).",
    "repetida": "Aprovada, mas não vai agora: já foi para o grupo nos últimos 7 dias.",
    "sem-preco": "Aprovada, mas esta avaliação não tem preço válido para publicar.",
}

Admin = Annotated[AuthContext, Depends(require_admin)]
Db = Annotated[AsyncSession, Depends(get_session)]


def _pct(bp: int | None) -> str:
    return "—" if bp is None else f"{bp / 100:.0f}%"


def _local(value: datetime | None) -> str:
    return value.astimezone(SP).strftime("%d/%m %H:%M") if value else "—"


templates.env.filters["brl"] = format_brl
templates.env.filters["pct"] = _pct
templates.env.filters["local"] = _local


def _safe_next(target: str | None) -> str:
    """Só caminho relativo do próprio painel (evita redirect aberto)."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return "/"


# --------------------------------------------------------------------------- login


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request) -> Response:
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    db: Db,
    username: Annotated[str, Form(max_length=40)],
    password: Annotated[str, Form(max_length=200)],
    code: Annotated[str, Form(max_length=10)],
) -> Response:
    ip = request.client.host if request.client else "?"
    if not throttle.allowed(ip):
        logger.warning("login bloqueado pelo freio", extra={"ip": ip})
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Muitas tentativas. Espere alguns minutos."},
            status_code=429,
        )
    user = (
        await db.execute(select(AdminUser).where(AdminUser.username == username.strip()))
    ).scalar_one_or_none()
    password_ok = verify_password(password, user.password_hash if user else None)
    step = (
        verify_totp(user.totp_secret, code, user.totp_last_step)
        if user and user.active and password_ok
        else None
    )
    if user is None or not password_ok or step is None:
        throttle.failure(ip)
        logger.warning("login recusado", extra={"ip": ip})
        return templates.TemplateResponse(
            request, "login.html", {"error": "Usuário, senha ou código inválidos."}, status_code=401
        )
    throttle.success(ip)
    user.totp_last_step = step
    user.last_login_at = utcnow()
    token, _ = await create_session(db, user)
    logger.info("login", extra={"user": user.username})
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=settings.environment == "production",
        samesite="strict",
        path="/",
    )
    return response


@router.post("/logout")
async def logout(
    request: Request, db: Db, ctx: Admin, csrf: Annotated[str | None, Form()] = None
) -> Response:
    check_csrf(ctx, csrf)
    await destroy_session(db, request.cookies.get(SESSION_COOKIE))
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


# --------------------------------------------------------------------------- fila


@router.get("/", response_class=HTMLResponse)
async def queue_page(
    request: Request,
    db: Db,
    ctx: Admin,
    nicho: str | None = None,
    nivel: str | None = None,
    aviso: str | None = None,
) -> Response:
    niches = await queries.niches(db)
    niche = next((n for n in niches if n.slug == nicho), None)
    tiers = [nivel] if nivel in queries.TIER_ORDER else queries.QUEUE_DEFAULT_TIERS
    cards = await queries.queue(db, tiers, niche.id if niche else None)
    counts = await queries.tier_counts(db, niche.id if niche else None)
    return templates.TemplateResponse(
        request,
        "fila.html",
        {
            "ctx": ctx,
            "cards": cards,
            "counts": counts,
            "niches": niches,
            "nicho": niche.slug if niche else None,
            "nivel": nivel if nivel in queries.TIER_ORDER else None,
            "tier_order": queries.TIER_ORDER,
            "notice": NOTICES.get(aviso or ""),
            "open_alerts": await queries.open_alerts(db),
            "next": str(request.url.path) + (f"?{request.url.query}" if request.url.query else ""),
        },
    )


@router.post("/decisao")
async def decide(
    db: Db,
    ctx: Admin,
    product_id: Annotated[int, Form()],
    decision: Annotated[str, Form()],
    csrf: Annotated[str | None, Form()] = None,
    evaluation_id: Annotated[int | None, Form()] = None,
    reason: Annotated[str | None, Form(max_length=200)] = None,
    next: Annotated[str | None, Form()] = None,
    pix: Annotated[str, Form(max_length=20)] = "",
) -> Response:
    check_csrf(ctx, csrf)
    target = _safe_next(next)
    try:
        pix_cents = parse_brl(pix) if decision == "approved" else None
    except PixError:
        return RedirectResponse(
            target + ("&" if "?" in target else "?") + "aviso=pix",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if decision not in DECISIONS:
        raise HTTPException(status_code=422, detail="decisão inválida")
    product = await db.get(CatalogProduct, product_id)
    if product is None:
        raise HTTPException(status_code=404)
    ev: OfferEvaluation | None = None
    if evaluation_id is not None:
        ev = await db.get(OfferEvaluation, evaluation_id)
        if ev is None or ev.product_id != product_id:
            raise HTTPException(status_code=422, detail="avaliação não confere")
    now = utcnow()
    record = OperatorDecision(
        product_id=product_id,
        evaluation_id=evaluation_id,
        decision=decision,
        reason=(reason or "").strip()[:200] or None,
        actor=ctx.username,
        decided_at=now,
    )
    db.add(record)
    if decision == "blocked":
        product.operator_blocked = True
    notice = None
    if decision == "approved" and ev is not None:
        await db.flush()
        try:
            result = await publications_for_approval(
                db,
                product=product,
                evaluation=ev,
                decision=record,
                now=now,
                pix_cents=pix_cents,
                affiliate_tool_id=settings.ml_affiliate_tool_id,
            )
        except PixRejectedError:
            await db.rollback()  # nem a decisão fica: o operador corrige o Pix e aprova de novo
            return RedirectResponse(
                target + ("&" if "?" in target else "?") + "aviso=pix",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        except DistributionError:
            notice = "sem-preco"
        else:
            if result.no_channel:
                notice = "sem-grupo"
            elif result.automatic:
                notice = "automatica"
            elif result.auto_link:
                notice = "pronta"
            elif result.created:
                notice = "links"
            elif result.skipped:
                notice = "repetida"
    logger.info(
        "decisão",
        extra={
            "product_id": product_id,
            "decision": decision,
            "user": ctx.username,
            "notice": notice,
        },
    )
    if notice:
        target += ("&" if "?" in target else "?") + f"aviso={notice}"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


# --------------------------------------------------------------------------- páginas


@router.get("/produto/{product_id}", response_class=HTMLResponse)
async def product_detail(request: Request, db: Db, ctx: Admin, product_id: int) -> Response:
    page = await queries.product_page(db, product_id)
    if page is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "produto.html",
        {
            "ctx": ctx,
            "p": page,
            "url": queries.product_url(page.product),
            "reasons": queries.reasons(page.latest_snapshot),
            "spark": queries.sparkline(page.observations),
            "decisions_label": DECISIONS,
            "next": f"/produto/{product_id}",
        },
    )


@router.get("/nichos", response_class=HTMLResponse)
async def niches_page(request: Request, db: Db, ctx: Admin) -> Response:
    return templates.TemplateResponse(
        request,
        "nichos.html",
        {"ctx": ctx, "rows": await queries.niche_overview(db), "tier_order": queries.TIER_ORDER},
    )


@router.get("/saude", response_class=HTMLResponse)
async def health_page(request: Request, db: Db, ctx: Admin) -> Response:
    return templates.TemplateResponse(
        request, "saude.html", {"ctx": ctx, "h": await queries.health(db), "now": utcnow()}
    )
