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

from app.collector.domains import DomainRuleError, apply_panel_rule
from app.collector.recategorize import ProductNotFound, RecategorizeError, recategorize
from app.core.clock import utcnow
from app.core.config import settings
from app.core.database import get_session
from app.core.logging import get_logger
from app.distribution.service import (
    DistributionError,
    PixRejectedError,
    chosen_channels,
    publications_for_approval,
)
from app.domain.distribution import (
    PixError,
    UnitsError,
    format_brl,
    parse_brl,
    parse_last_units,
)
from app.models import (
    AdminUser,
    CatalogProduct,
    Channel,
    OfferEvaluation,
    OperatorDecision,
)
from app.panel import queries, report
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
    "cupom": "Não aprovada: o preço com cupom precisa ser menor que o preço e até 30% abaixo.",
    "unidades": "Não aprovada: últimas unidades é um número de 1 a 20 (o que a página mostra).",
    "sem-grupo": "Aprovada, mas o nicho não tem grupo cadastrado (Grupos).",
    "grupos": "Não aprovada: marque pelo menos um grupo ativo em “Grupos”.",
    "varios": "Aprovada para os grupos marcados. O link de cada grupo sai em instantes e o post "
    "aparece na fila de cada um em Postar.",
    "varios-parte": "Aprovada em parte dos grupos marcados: os outros já receberam este produto "
    "nos últimos 7 dias.",
    "repetida": "Aprovada, mas não vai agora: já foi para o grupo nos últimos 7 dias.",
    "sem-preco": "Aprovada, mas esta avaliação não tem preço válido para publicar.",
    "nicho": "Nicho trocado. O robô reavalia com as regras do nicho novo em instantes.",
    "nicho-fila": "Nicho trocado. Ele saiu da fila de envio do grupo antigo: aprove de novo na "
    "página do produto para ir ao grupo certo.",
    "nicho-tipo": "Nicho trocado para este produto e para todos do mesmo tipo. Os novos desse "
    "tipo já entram nesse nicho (regra na tela Nichos).",
    "nicho-invalido": "Escolha um nicho ativo.",
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
    enviados: str | None = None,
    aviso: str | None = None,
) -> Response:
    niches = await queries.niches(db)
    sent = enviados if enviados in queries.SENT_FILTERS else None
    niche = next((n for n in niches if n.slug == nicho), None)
    only_discount = nivel == queries.DISCOUNT_FILTER
    if only_discount:
        tiers = queries.DISCOUNT_TIERS
    else:
        tiers = [nivel] if nivel in queries.TIER_ORDER else queries.QUEUE_DEFAULT_TIERS
    niche_id = niche.id if niche else None
    cards = await queries.queue(db, tiers, niche_id, only_discount=only_discount, sent=sent)
    ready = await queries.ready_to_post(db, utcnow())
    counts = await queries.tier_counts(db, niche_id)
    return templates.TemplateResponse(
        request,
        "fila.html",
        {
            "ctx": ctx,
            "cards": cards,
            "counts": counts,
            "niches": niches,
            "nicho": niche.slug if niche else None,
            "nivel": nivel if nivel in queries.TIER_ORDER or only_discount else None,
            "discount_count": await queries.discount_count(db, niche_id),
            "enviados": sent,
            "ready": ready,
            "ready_total": sum(k.ready for k in ready.values()),
            "tier_order": queries.TIER_ORDER,
            "targets": await queries.approval_targets(db),
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
    cupom: Annotated[str, Form(max_length=20)] = "",
    ultimas: Annotated[str, Form(max_length=4)] = "",
    grupos: Annotated[str | None, Form(max_length=1)] = None,
    channel_id: Annotated[list[int] | None, Form()] = None,
) -> Response:
    """`grupos` marca o formulário com "Grupos ▾": aí vale o que foi marcado em `channel_id`
    (nenhum marcado não aprova). Sem ele (formulário antigo), os grupos do nicho do produto."""
    check_csrf(ctx, csrf)
    target = _safe_next(next)
    sep = "&" if "?" in target else "?"
    try:
        last_units = parse_last_units(ultimas) if decision == "approved" else None
    except UnitsError:
        return RedirectResponse(
            target + sep + "aviso=unidades", status_code=status.HTTP_303_SEE_OTHER
        )
    try:
        pix_cents = parse_brl(pix) if decision == "approved" else None
    except PixError:
        return RedirectResponse(target + sep + "aviso=pix", status_code=status.HTTP_303_SEE_OTHER)
    try:
        coupon_cents = parse_brl(cupom) if decision == "approved" else None
    except PixError:
        return RedirectResponse(target + sep + "aviso=cupom", status_code=status.HTTP_303_SEE_OTHER)
    if decision not in DECISIONS:
        raise HTTPException(status_code=422, detail="decisão inválida")
    channels: list[Channel] | None = None
    if decision == "approved" and grupos:
        channels = await chosen_channels(db, channel_id or [])
        if not channels:
            return RedirectResponse(
                target + sep + "aviso=grupos", status_code=status.HTTP_303_SEE_OTHER
            )
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
                coupon_cents=coupon_cents,
                last_units=last_units,
                channels=channels,
            )
        except PixRejectedError as exc:
            await db.rollback()  # nem a decisão fica: o operador corrige e aprova de novo
            return RedirectResponse(
                target + sep + f"aviso={exc.field}", status_code=status.HTTP_303_SEE_OTHER
            )
        except DistributionError:
            notice = "sem-preco"
        else:
            if result.no_channel:
                notice = "sem-grupo"
            elif channels and len(channels) > 1 and result.created and result.skipped:
                notice = "varios-parte"
            elif result.created > 1:
                notice = "varios"
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
            "groups": [c.id for c in channels] if channels else None,
        },
    )
    if notice:
        target += ("&" if "?" in target else "?") + f"aviso={notice}"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


# --------------------------------------------------------------------------- páginas


@router.post("/produto/{product_id}/nicho")
async def change_niche(
    db: Db,
    ctx: Admin,
    product_id: int,
    niche_id: Annotated[int, Form()],
    csrf: Annotated[str | None, Form()] = None,
    next: Annotated[str | None, Form()] = None,
    todos: Annotated[bool, Form()] = False,
) -> Response:
    check_csrf(ctx, csrf)
    target = _safe_next(next)
    sep = "&" if "?" in target else "?"
    try:
        result = await recategorize(
            db, product_id=product_id, niche_id=niche_id, actor=ctx.username
        )
        same_type = 0
        if todos and result.domain_id:
            # "Todos deste tipo": vira regra (os novos desse tipo já entram neste nicho).
            same_type = await apply_panel_rule(
                db,
                domain_id=result.domain_id,
                niche_id=niche_id,
                actor=ctx.username,
                now=utcnow(),
            )
    except ProductNotFound:
        raise HTTPException(status_code=404) from None
    except (RecategorizeError, DomainRuleError):
        await db.rollback()
        return RedirectResponse(
            target + sep + "aviso=nicho-invalido", status_code=status.HTTP_303_SEE_OTHER
        )
    await db.commit()
    logger.info(
        "nicho trocado pelo painel",
        extra={"product_id": product_id, "user": ctx.username, "same_type": same_type},
    )
    if todos and result.domain_id:
        notice = "nicho-tipo"
    else:
        notice = "nicho-fila" if result.discarded else "nicho"
    return RedirectResponse(target + sep + f"aviso={notice}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/produto/{product_id}", response_class=HTMLResponse)
async def product_detail(
    request: Request, db: Db, ctx: Admin, product_id: int, aviso: str | None = None
) -> Response:
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
            "niches": await queries.niches(db),
            "targets": await queries.approval_targets(db),
            "notice": NOTICES.get(aviso or ""),
        },
    )


@router.get("/nichos", response_class=HTMLResponse)
async def niches_page(request: Request, db: Db, ctx: Admin) -> Response:
    return templates.TemplateResponse(
        request,
        "nichos.html",
        {
            "ctx": ctx,
            "rows": await queries.niche_overview(db),
            "tier_order": queries.TIER_ORDER,
            "report": await report.niche_report(db, utcnow()),
            "report_days": report.REPORT_DAYS,
            "sales": await report.sales_status(db),
            "domain_rules": await queries.domain_rule_rows(db),
        },
    )


@router.get("/saude", response_class=HTMLResponse)
async def health_page(request: Request, db: Db, ctx: Admin) -> Response:
    return templates.TemplateResponse(
        request, "saude.html", {"ctx": ctx, "h": await queries.health(db), "now": utcnow()}
    )
