"""Rotas do painel (HTML server-side, formulários comuns, pensado para celular)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.collector.domains import DomainRuleError, apply_panel_rule
from app.collector.recategorize import ProductNotFound, RecategorizeError, recategorize
from app.core.clock import ensure_utc, utcnow
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
    FlashError,
    PixError,
    UnitsError,
    format_brl,
    parse_brl,
    parse_flash_left,
    parse_last_units,
)
from app.models import (
    AdminUser,
    CatalogProduct,
    Channel,
    OfferEvaluation,
    OperatorDecision,
)
from app.panel import coordination, queries, report
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
    "relampago": "Não aprovada: o tempo da relâmpago é o relógio “Encerra em” da página (ex.: "
    "07:10:38), de 10 minutos a 24 horas.",
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
    "ja-decidido": "Outro operador já decidiu este produto; a fila foi atualizada.",
}
VERB = {"approved": "aprovou", "rejected": "rejeitou", "blocked": "bloqueou"}
# Resposta da decisão pedida pela fila sem recarregar (fetch), quando não há aviso específico.
DONE = {
    "approved": "Aprovada.",
    "rejected": "Rejeitada: saiu da fila.",
    "blocked": "Bloqueada: o produto não volta para a fila.",
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
            "now_iso": utcnow().isoformat(),
        },
    )


def _seen(value: str | None, now: datetime) -> datetime | None:
    """Quando a página foi aberta (o servidor escreve no formulário). Inválido ou futuro: None."""
    if not value:
        return None
    try:
        seen = ensure_utc(datetime.fromisoformat(value))
    except ValueError:
        return None
    return seen if seen <= now else None


def _minutes(now: datetime, since: datetime) -> int:
    return max(0, int((now - since).total_seconds() // 60))


def _late_message(late: coordination.Conflict, actor: str, decision: str) -> str:
    who = "Você já" if late.actor == actor else f"{late.actor} já"
    when = _local(late.at)
    if late.decision == "approved" and decision == "approved":
        return (
            f"{who} aprovou este produto ({when}). Para mandar a outro grupo, use o \uff0b "
            "“Enviar também em”, no Postar."
        )
    if late.decision == "approved":
        return f"{who} aprovou este produto ({when}). Para tirar da fila de envio, use ✕ no Postar."
    return f"{who} {VERB.get(late.decision, 'decidiu')} este produto ({when})."


def _decided_label(late: coordination.Conflict, actor: str) -> str:
    who = "Você" if late.actor == actor else late.actor
    return f"{who} {VERB.get(late.decision, 'decidiu')} ({_local(late.at)})"


@router.post("/decisao")
async def decide(
    request: Request,
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
    acaba_em: Annotated[str, Form(max_length=12)] = "",
    grupos: Annotated[str | None, Form(max_length=1)] = None,
    channel_id: Annotated[list[int] | None, Form()] = None,
    seguinte: Annotated[int | None, Form()] = None,
    vista_em: Annotated[str | None, Form(max_length=40)] = None,
) -> Response:
    """`grupos` marca o formulário com "Grupos ▾": aí vale o que foi marcado em `channel_id`
    (nenhum marcado não aprova). Sem ele (formulário antigo), os grupos do nicho do produto.

    A fila decide sem recarregar (fetch com `Accept: application/json`) e recebe o aviso em
    JSON. Sem JavaScript, o redirect volta para o card `seguinte` (`#p-<id>`): antes cada
    aprovação jogava o operador para o topo da página (15/10/2026).

    Dois operadores: a linha do produto fica travada até o commit, e a decisão que chega tarde
    (já aprovado e valendo, ou decidido por outro depois de `vista_em`) é recusada com 409 e
    quem decidiu. O commit acontece antes da resposta: falha no banco não vira "Aprovada."."""
    check_csrf(ctx, csrf)
    wants_json = "application/json" in request.headers.get("accept", "")
    target = _safe_next(next)

    def reply(aviso: str | None, *, ok: bool = True, message: str | None = None) -> Response:
        if wants_json:
            late = aviso == "ja-decidido"
            body: dict[str, object] = {
                "ok": ok,
                "aviso": aviso,
                "mensagem": message or NOTICES.get(aviso or "") or DONE.get(decision, "Feito."),
            }
            if late:
                body["decidido"] = True
            return JSONResponse(body, status_code=200 if ok else 409 if late else 422)
        url = target
        if aviso:
            url += ("&" if "?" in url else "?") + f"aviso={aviso}"
        if ok and seguinte is not None:
            url += f"#p-{seguinte}"
        return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)

    try:
        last_units = parse_last_units(ultimas) if decision == "approved" else None
    except UnitsError:
        return reply("unidades", ok=False)
    try:
        pix_cents = parse_brl(pix) if decision == "approved" else None
    except PixError:
        return reply("pix", ok=False)
    try:
        coupon_cents = parse_brl(cupom) if decision == "approved" else None
    except PixError:
        return reply("cupom", ok=False)
    try:
        flash_left = (
            parse_flash_left(acaba_em) if decision == "approved" and acaba_em.strip() else None
        )
    except FlashError:
        return reply("relampago", ok=False)
    if decision not in DECISIONS:
        raise HTTPException(status_code=422, detail="decisão inválida")
    channels: list[Channel] | None = None
    if decision == "approved" and grupos:
        channels = await chosen_channels(db, channel_id or [])
        if not channels:
            return reply("grupos", ok=False)
    now = utcnow()
    product = (
        await db.execute(
            select(CatalogProduct).where(CatalogProduct.id == product_id).with_for_update()
        )
    ).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404)
    if decision in ("approved", "rejected"):
        late = await coordination.conflict(
            db, product, actor=ctx.username, seen_at=_seen(vista_em, now), now=now
        )
        if late is not None:
            await db.rollback()
            logger.info(
                "decisão recusada: já decidido",
                extra={
                    "product_id": product_id,
                    "decision": decision,
                    "user": ctx.username,
                    "by": late.actor,
                },
            )
            return reply(
                "ja-decidido", ok=False, message=_late_message(late, ctx.username, decision)
            )
    ev: OfferEvaluation | None = None
    if evaluation_id is not None:
        ev = await db.get(OfferEvaluation, evaluation_id)
        if ev is None or ev.product_id != product_id:
            raise HTTPException(status_code=422, detail="avaliação não confere")
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
                flash_ends_at=now + flash_left if flash_left is not None else None,
            )
        except PixRejectedError as exc:
            await db.rollback()  # nem a decisão fica: o operador corrige e aprova de novo
            return reply(exc.field, ok=False)
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
    await coordination.settle(db, product_id)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        logger.warning(
            "decisão recusada no commit", extra={"product_id": product_id, "user": ctx.username}
        )
        return reply(
            "ja-decidido",
            ok=False,
            message="Outro operador decidiu este produto agora mesmo; a fila foi atualizada.",
        )
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
    return reply(notice)


def _ids(raw: str) -> list[int]:
    out: list[int] = []
    for part in raw.split(",")[: coordination.BOARD_LIMIT]:
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


@router.post("/fila/marca")
async def queue_mark(
    db: Db,
    ctx: Admin,
    product_id: Annotated[int, Form()],
    marca: Annotated[str, Form(max_length=12)] = "",
    csrf: Annotated[str | None, Form()] = None,
) -> JSONResponse:
    """✓/✕ de um card vai para o banco: os outros operadores veem "em aprovação por X" e não
    marcam o mesmo produto. `marca` vazia desmarca."""
    check_csrf(ctx, csrf)
    if marca and marca not in coordination.MARKS:
        raise HTTPException(status_code=422, detail="marca inválida")
    now = utcnow()
    if marca:
        if await db.get(CatalogProduct, product_id) is None:
            raise HTTPException(status_code=404)
        holder = await coordination.claim(
            db, product_id=product_id, actor=ctx.username, mark=marca, now=now
        )
        if holder is not None:
            await db.rollback()
            minutes = _minutes(now, holder.since)
            return JSONResponse(
                {
                    "ok": False,
                    "por": holder.actor,
                    "ha_min": minutes,
                    "mensagem": f"Em aprovação por {holder.actor} (marcou há {minutes} min).",
                },
                status_code=409,
            )
    else:
        await coordination.release(db, product_id=product_id, actor=ctx.username)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return JSONResponse(
            {"ok": False, "mensagem": "Outro operador marcou este produto agora mesmo."},
            status_code=409,
        )
    return JSONResponse({"ok": True})


@router.post("/fila/estado")
async def queue_board(
    db: Db,
    ctx: Admin,
    ids: Annotated[str, Form(max_length=1000)] = "",
    desde: Annotated[str | None, Form(max_length=40)] = None,
    csrf: Annotated[str | None, Form()] = None,
) -> JSONResponse:
    """O que a página aberta precisa saber dos outros operadores (a fila consulta a cada 15 s):
    marcas deles, as minhas (de outro aparelho) e o que foi decidido depois que ela abriu."""
    check_csrf(ctx, csrf)
    now = utcnow()
    state = await coordination.board(
        db, product_ids=_ids(ids), actor=ctx.username, since=_seen(desde, now), now=now
    )
    await db.commit()
    return JSONResponse(
        {
            "meus": {str(k): v for k, v in state.mine.items()},
            "outros": {
                str(k): {"por": h.actor, "marca": h.mark, "ha_min": _minutes(now, h.since)}
                for k, h in state.others.items()
            },
            "decididos": {
                str(k): {"mensagem": _decided_label(c, ctx.username)}
                for k, c in state.decided.items()
            },
        }
    )


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
            "now_iso": utcnow().isoformat(),
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
