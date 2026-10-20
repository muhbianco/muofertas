"""Painel da distribuição manual (Fase 2a): grupos, links do ML e postagem pelo celular."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collector import watchlist
from app.collector.recategorize import RecategorizeError
from app.core.clock import utcnow
from app.core.config import settings
from app.core.database import get_sessionmaker
from app.core.logging import get_logger
from app.distribution import invites, service
from app.distribution.manual import (
    DISCOVERED_MANUAL,
    ManualError,
    ManualForm,
    check_image_url,
    post_from_form,
    queue_manual,
)
from app.distribution.move import (
    PublicationNotFound,
    copy_publication,
    move_publication,
)
from app.distribution.sender import resend_now, send_now
from app.distribution.service import DistributionError
from app.distribution.short_links import expand
from app.domain.distribution import (
    BATCH_GAP,
    INVITE_PREFIX,
    MANUAL_TIER,
    MAX_BATCH_SIZE,
    MAX_PER_DAY,
    MIN_BATCH_EVERY_MINUTES,
    MIN_SPACING_MINUTES,
    ML_TAG_RE,
    SEND_BATCH,
    SEND_MODES,
    SEND_STEADY,
    LinkError,
    batch_starts,
    format_brl,
    hour_label,
    is_fresh,
    is_urgent,
    local_day_start,
    manual_block_reason,
    max_sends_in_window,
    share_url,
    spacing_minutes,
)
from app.domain.invites import InviteConfigError
from app.integrations import ml_shortlink
from app.integrations.mercadolivre import MercadoLivreClient
from app.integrations.wuzapi import WuzapiClient, WuzapiError
from app.models import CatalogProduct, Channel, Niche, Publication
from app.panel import queries
from app.panel.auth import AuthContext, check_csrf
from app.panel.persist import save
from app.panel.routes import Admin, Db, templates
from app.panel.whatsapp_routes import get_wa_client
from app.whatsapp import service as wa

logger = get_logger(__name__)
router = APIRouter()
LIST_LIMIT = 100
PER_GROUP = 60  # itens por grupo na tela Postar (o resto aparece quando estes saírem)

POST_NOTICES = {
    "avulso": "Link avulso na fila: é o próximo a sair nos grupos marcados. O link de cada grupo "
    "sai em segundos.",
    "avulso-parte": "Link avulso na fila em parte dos grupos: nos outros o produto já está na "
    "fila.",
    "avulso-repetido": "O link avulso não entrou: o produto já está na fila dos grupos marcados.",
    "movido": "Post mudou de grupo: entrou no fim da fila do grupo novo; o link novo sai em "
    "segundos. O produto ficou no nicho escolhido.",
    "movido-repetido": "O produto mudou de nicho, mas não entrou na fila do grupo novo: ele já "
    "está na fila desse grupo.",
    "movido-sem-grupo": "O produto mudou de nicho, mas esse nicho não tem grupo ativo.",
    "mover-erro": "Não deu para mudar de grupo: o post já saiu ou está no grupo desse nicho.",
    "copiado": "Pronto: o post entrou também no fim da fila dos grupos escolhidos; o link de "
    "cada grupo sai em segundos.",
    "copiado-agendado": "Pronto: o post entrou também nos grupos escolhidos. Onde o produto saiu "
    "há menos de 24 h, a cópia espera completar as 24 h (⏳ na fila).",
    "copiado-parte": "Entrou em parte dos grupos: nos outros o produto já está na fila.",
    "copiado-repetido": "Não entrou em nenhum: o produto já está na fila dos grupos escolhidos.",
    "copiado-ja": "O post já estava na fila desses grupos; nada foi duplicado.",
    "copiar-erro": "Não deu para enviar também: escolha pelo menos um outro grupo ativo (só "
    "vale para post que ainda não saiu).",
    "removido": "Post tirado da fila de envio. O produto volta para a aprovação quando o preço "
    "mudar.",
    "enviado": "Post enviado agora para o grupo.",
}


def send_now_notice(pub: Publication | None) -> str:
    """Por que o "Enviar agora" não mandou. Antes o painel voltava para a fila calado e o post
    sumia: a panela de 14/10/2026 tinha subido de R$ 159,00 para R$ 169,90 e pareceu defeito."""
    if pub is None:
        return "Não enviado: o post não existe mais."
    if pub.state == "sent":
        return POST_NOTICES["enviado"]
    if pub.state == "skipped":
        approved, now_price = pub.approved_price_cents, pub.checked_price_cents
        if approved and now_price and now_price > approved:
            return (
                f"Não enviado: o preço subiu de {format_brl(approved)} para "
                f"{format_brl(now_price)} desde a aprovação, e o post saiu da fila. Para postar "
                "pelo preço novo, aprove o produto de novo."
            )
        return f"Não enviado: {pub.note or 'a revalidação recusou'}. O post saiu da fila."
    if pub.state == "ready" and pub.retry_after is not None:
        return (
            "Não enviado: o Mercado Livre não achou a oferta agora (pode ser instabilidade). "
            "O post continua na fila e tenta de novo em 30 minutos."
        )
    if pub.state == "ready":
        return (
            "Não enviado: o Mercado Livre não respondeu. O post continua na fila; tente de novo "
            "em alguns minutos."
        )
    return f"Não enviado: {pub.note or 'veja o post na lista de enviados'}."


ERRORS = {
    "ml": "O Mercado Livre não respondeu. Tente revalidar de novo.",
    "pix": "Preço do Pix recusado: precisa ser menor que o anúncio e até 30% abaixo (ex.: 49,90).",
    "pix-velho": "A revalidação venceu: revalide o preço e informe o Pix de novo.",
    "sem-item": "Esta publicação não tem a oferta registrada; descarte e aprove de novo.",
    "foto": "Foto recusada: use o endereço da imagem do anúncio (https://http2.mlstatic.com/…).",
}


async def get_ml_client(request: Request) -> MercadoLivreClient:
    """Um cliente por processo web (ritmo e teto próprios), criado no primeiro uso."""
    client = getattr(request.app.state, "ml_client", None)
    if client is None:
        client = MercadoLivreClient(settings)
        request.app.state.ml_client = client
    return client


Ml = Annotated[MercadoLivreClient, Depends(get_ml_client)]
WaClient = Annotated[WuzapiClient, Depends(get_wa_client)]
Sessions = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)]


def _see_other(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


# --------------------------------------------------------------------------- grupos


@dataclass
class ChannelForm:
    name: str
    niche_id: int
    ml_tag: str | None
    invite_url: str | None
    max_per_day: int
    window_start_hour: int
    window_end_hour: int
    paused: bool
    transport: str = "manual"
    always_checked: bool = False
    send_mode: str = SEND_STEADY
    batch_size: int | None = None
    batch_every_minutes: int | None = None


def _batch_slots(form: ChannelForm) -> list[datetime]:
    every = timedelta(minutes=form.batch_every_minutes or 0)
    return batch_starts(utcnow(), form.window_start_hour, form.window_end_hour, every)


def _every(minutes: int) -> str:
    return f"{minutes // 60} h" if minutes % 60 == 0 else f"{minutes} min"


def send_plan(channel: Channel) -> str:
    """Resumo do ritmo do grupo na tela Grupos (o que o envio vai fazer de verdade)."""
    form = ChannelForm(
        name=channel.name,
        niche_id=channel.niche_id,
        ml_tag=None,
        invite_url=None,
        max_per_day=channel.max_per_day,
        window_start_hour=channel.window_start_hour,
        window_end_hour=channel.window_end_hour,
        paused=channel.paused,
        send_mode=channel.send_mode,
        batch_size=channel.batch_size,
        batch_every_minutes=channel.batch_every_minutes,
    )
    if form.send_mode == SEND_BATCH and form.batch_size and form.batch_every_minutes:
        slots = _batch_slots(form)
        hours = ", ".join(hour_label(s) for s in slots)
        return (
            f"Em lotes: {form.batch_size} a cada {_every(form.batch_every_minutes)}, às {hours} "
            f"· até {form.batch_size * len(slots)} por dia, fora as prioridades "
            f"(teto de {form.max_per_day})"
        )
    spacing = spacing_minutes(form.max_per_day, form.window_start_hour, form.window_end_hour)
    return (
        f"Espalhado: {form.max_per_day} por dia, um a cada ~{spacing} min, das "
        f"{form.window_start_hour}h às {form.window_end_hour}h"
    )


NEEDS_WHATSAPP_LINK = (
    "Envio automático só para grupo ligado ao WhatsApp. Crie o grupo no WhatsApp pelo chip, "
    "ligue em 'No WhatsApp do chip' (opção Novo · nicho) e depois mude o Envio aqui."
)


def _validate(form: ChannelForm, niche_ids: set[int]) -> str | None:
    if not 1 <= len(form.name) <= 80:
        return "nome do grupo: de 1 a 80 caracteres"
    if form.niche_id not in niche_ids:
        return "escolha um nicho"
    if form.ml_tag and not ML_TAG_RE.match(form.ml_tag):
        return "etiqueta do ML: só minúsculas e números, até 30"
    if form.invite_url and not (
        form.invite_url.startswith(INVITE_PREFIX) and len(form.invite_url) <= 255
    ):
        return f"link de convite precisa começar com {INVITE_PREFIX}"
    if not 1 <= form.max_per_day <= MAX_PER_DAY:
        return f"envios por dia: de 1 a {MAX_PER_DAY}"
    if not (0 <= form.window_start_hour < form.window_end_hour <= 24):
        return "janela: início antes do fim, entre 0h e 24h"
    if form.send_mode not in SEND_MODES:
        return "ritmo de envio: espalhado ou em lotes"
    if form.send_mode == SEND_BATCH:
        window = (form.window_end_hour - form.window_start_hour) * 60
        if form.batch_size is None or not 1 <= form.batch_size <= MAX_BATCH_SIZE:
            return f"anúncios por lote: de 1 a {MAX_BATCH_SIZE}"
        every = form.batch_every_minutes
        if every is None or not MIN_BATCH_EVERY_MINUTES <= every <= window:
            return f"um lote a cada: de {MIN_BATCH_EVERY_MINUTES} min até a janela ({window} min)"
        gap = int(BATCH_GAP.total_seconds() // 60)
        if form.batch_size * gap > every:
            return (
                f"o lote não cabe: com {gap} min entre um anúncio e outro, {form.batch_size} "
                f"anúncios levam {form.batch_size * gap} min"
            )
        wanted = form.batch_size * len(_batch_slots(form))
        if wanted > form.max_per_day:
            return (
                f"envios por dia ({form.max_per_day}) corta os lotes: eles pedem {wanted}. "
                "Suba o limite do dia ou diminua o lote"
            )
    else:
        room = max_sends_in_window(form.window_start_hour, form.window_end_hour)
        if form.max_per_day > room:
            return (
                f"nessa janela cabem no máximo {room} envios por dia "
                f"(um a cada {MIN_SPACING_MINUTES} min)"
            )
    if form.transport not in ("manual", "wuzapi"):
        return "envio: manual ou automático"
    return None


async def _render_groups(
    request: Request,
    db: Db,
    ctx: Admin,
    error: str | None = None,
    code: int = 200,
    wa_client: WuzapiClient | None = None,
) -> Response:
    channels = list((await db.execute(select(Channel).order_by(Channel.id).limit(50))).scalars())
    niches = list((await db.execute(select(Niche).order_by(Niche.id).limit(50))).scalars())
    growth = await wa.growth(db, utcnow())
    session = await wa.get_session(db, settings)
    await db.commit()  # a lista de grupos vem da WuzAPI: sem transação aberta
    wa_rows: list[wa.GroupRow] | None = None
    wa_error = None
    if wa_client is not None and wa_client.configured and session is not None:
        try:
            # Estado ao vivo: o gravado só muda com o vigia (5 min) ou a tela Número.
            live = await wa_client.status()
            groups = await wa_client.groups() if live.logged_in else None
        except WuzapiError:
            groups = None
            wa_error = "O WhatsApp não respondeu agora; os grupos de lá não aparecem."
        if groups is not None:
            wa_rows = await wa.group_rows(db, groups)
            for row in wa_rows:
                if row.channel is None:
                    row.suggested_niche_id = wa.guess_niche(row.group.name, niches)
        elif wa_error is None:
            wa_error = "O chip não está pareado agora: conecte em Número para ver os grupos."
    return templates.TemplateResponse(
        request,
        "grupos.html",
        {
            "ctx": ctx,
            "channels": channels,
            "niches": niches,
            "error": error,
            "growth": growth,
            "wa_rows": wa_rows,
            "wa_error": wa_error,
            "wa_session": session,
            "spacing": spacing_minutes,
            "plans": {c.id: send_plan(c) for c in channels},
        },
        status_code=code,
    )


@router.get("/grupos", response_class=HTMLResponse)
async def groups_page(
    request: Request, db: Db, ctx: Admin, wa_client: WaClient, erro: str | None = None
) -> Response:
    error = (
        "Não deu para ligar esse grupo. Confira se o número está no grupo e é admin."
        if erro == "grupo"
        else None
    )
    return await _render_groups(request, db, ctx, error, wa_client=wa_client)


@router.post("/grupos", response_class=HTMLResponse)
@router.post("/grupos/{channel_id}", response_class=HTMLResponse)
async def save_group(
    request: Request,
    db: Db,
    ctx: Admin,
    name: Annotated[str, Form(max_length=80)],
    niche_id: Annotated[int, Form()],
    max_per_day: Annotated[int, Form()],
    window_start_hour: Annotated[int, Form()],
    window_end_hour: Annotated[int, Form()],
    csrf: Annotated[str | None, Form()] = None,
    ml_tag: Annotated[str | None, Form(max_length=30)] = None,
    invite_url: Annotated[str | None, Form(max_length=255)] = None,
    paused: Annotated[str | None, Form()] = None,
    transport: Annotated[str, Form(max_length=12)] = "manual",
    sempre: Annotated[str | None, Form(max_length=1)] = None,
    modo: Annotated[str, Form(max_length=8)] = SEND_STEADY,
    lote_tamanho: Annotated[str, Form(max_length=4)] = "",
    lote_minutos: Annotated[str, Form(max_length=5)] = "",
    channel_id: int | None = None,
) -> Response:
    check_csrf(ctx, csrf)
    form = ChannelForm(
        name=name.strip(),
        niche_id=niche_id,
        ml_tag=(ml_tag or "").strip() or None,
        invite_url=(invite_url or "").strip() or None,
        max_per_day=max_per_day,
        window_start_hour=window_start_hour,
        window_end_hour=window_end_hour,
        paused=paused == "1",
        transport=transport,
        always_checked=sempre == "1",
        send_mode=modo,
        batch_size=int(lote_tamanho) if lote_tamanho.strip().isdigit() else None,
        batch_every_minutes=int(lote_minutos) if lote_minutos.strip().isdigit() else None,
    )
    niche_ids = set((await db.execute(select(Niche.id).limit(50))).scalars())
    error = _validate(form, niche_ids)
    if error is None:
        clash = (
            await db.execute(
                select(Channel.id).where(Channel.name == form.name, Channel.id != (channel_id or 0))
            )
        ).scalar_one_or_none()
        if clash is not None:
            error = "já existe um grupo com esse nome"
    if error:
        return await _render_groups(request, db, ctx, error, 422)
    channel = await db.get(Channel, channel_id) if channel_id is not None else None
    if channel_id is not None and channel is None:
        raise HTTPException(status_code=404)
    if form.transport == "wuzapi" and (channel is None or not channel.wa_jid):
        return await _render_groups(request, db, ctx, NEEDS_WHATSAPP_LINK, 422)
    if channel is None:
        # Só depois de validar: a tela de erro faz commit e gravaria um grupo vazio.
        channel = Channel(created_at=utcnow())
        db.add(channel)
    for key, value in vars(form).items():
        setattr(channel, key, value)
    channel.min_interval_minutes = spacing_minutes(
        form.max_per_day, form.window_start_hour, form.window_end_hour
    )
    await save(db)
    logger.info("grupo salvo", extra={"channel": form.name, "user": ctx.username})
    return _see_other("/grupos")


# --------------------------------------------------------------------------- links do ML


@dataclass
class LinkItem:
    id: int
    name: str
    price_cents: int
    target_url: str


@dataclass
class LinkGroup:
    channel: Channel
    items: list[LinkItem]

    @property
    def urls(self) -> str:
        return "\n".join(i.target_url for i in self.items)


async def _link_groups(db: Db) -> list[LinkGroup]:
    rows = (
        await db.execute(
            select(Publication, CatalogProduct.name, CatalogProduct.external_id)
            .join(CatalogProduct, CatalogProduct.id == Publication.product_id)
            .where(Publication.state == "awaiting_link")
            .order_by(Publication.channel_id, Publication.id)
            .limit(LIST_LIMIT)
        )
    ).all()
    channels = {
        c.id: c
        for c in (
            await db.execute(
                select(Channel).where(Channel.id.in_({p.channel_id for p, _, _ in rows}))
            )
        ).scalars()
    }
    groups: dict[int, LinkGroup] = {}
    for pub, name, external_id in rows:
        group = groups.setdefault(pub.channel_id, LinkGroup(channels[pub.channel_id], []))
        group.items.append(
            LinkItem(pub.id, name or external_id, pub.approved_price_cents, pub.target_url)
        )
    return list(groups.values())


@router.get("/links", response_class=HTMLResponse)
async def links_page(request: Request, db: Db, ctx: Admin) -> Response:
    return templates.TemplateResponse(
        request,
        "links.html",
        {"ctx": ctx, "groups": await _link_groups(db), "error": None},
    )


@router.post("/links/{channel_id}", response_class=HTMLResponse)
async def save_links(
    request: Request,
    db: Db,
    ctx: Admin,
    channel_id: int,
    links: Annotated[str, Form(max_length=20_000)],
    publication_id: Annotated[list[int], Form()],
    csrf: Annotated[str | None, Form()] = None,
) -> Response:
    check_csrf(ctx, csrf)
    try:
        saved = await service.attach_links(
            db, channel_id=channel_id, publication_ids=publication_id, text=links, now=utcnow()
        )
    except DistributionError as exc:
        await db.rollback()
        return templates.TemplateResponse(
            request,
            "links.html",
            {"ctx": ctx, "groups": await _link_groups(db), "error": str(exc)},
            status_code=422,
        )
    await save(db)
    logger.info(
        "links do ML", extra={"channel_id": channel_id, "saved": saved, "user": ctx.username}
    )
    return _see_other("/postar")


# --------------------------------------------------------------------------- postar


@dataclass
class ReadyRow:
    pub: Publication
    name: str
    image_url: str | None
    urgent: bool = False  # relâmpago ou últimas unidades ainda valendo: sai na frente
    waiting: bool = False  # repetição agendada que ainda não chegou na hora (24 h)


@dataclass
class ReadyGroup:
    channel: Channel
    block: str | None
    rows: list[ReadyRow]
    total: int = 0  # prontos no grupo (a lista mostra até PER_GROUP)
    sent_today: int = 0
    batch: BatchView | None = None  # só no grupo em lotes


@dataclass(frozen=True, slots=True)
class BatchView:
    """O lote do grupo no aviso do Postar: "lote das 07h: 15/20 · próximo lote 10h"."""

    size: int
    sent: int
    current: str | None
    next: str | None
    rule: str


def batch_view(progress: service.BatchProgress | None) -> BatchView | None:
    if progress is None:
        return None
    hours = [hour_label(s) for s in progress.starts]
    listed = ", ".join(hours[:-1]) + f" e {hours[-1]}" if len(hours) > 1 else "".join(hours)
    is_open = ""
    if progress.current is not None and progress.open_until is not None:
        minutes = int((progress.open_until - progress.current).total_seconds() // 60)
        is_open = f"; cada lote fica aberto {minutes} min"
    return BatchView(
        size=progress.size,
        sent=progress.sent,
        current=hour_label(progress.current) if progress.current else None,
        next=hour_label(progress.next_start) if progress.next_start else None,
        rule=(
            f"Em lotes de {progress.size} a cada {_every(progress.every_minutes)} ({listed})"
            f"{is_open}. Avulso, relâmpago e últimas unidades saem a qualquer hora, fora do lote."
        ),
    )


@router.post("/postar/{publication_id}/subir")
async def post_move_up(
    db: Db,
    ctx: Admin,
    publication_id: int,
    csrf: Annotated[str | None, Form()] = None,
    antes_de: Annotated[int | None, Form()] = None,
) -> Response:
    """Reordena a fila de envio: a publicação sobe para antes de `antes_de` (ou para o topo)."""
    check_csrf(ctx, csrf)
    moved = await service.move_up(db, publication_id=publication_id, before_id=antes_de)
    await save(db)
    if moved:
        logger.info(
            "fila reordenada",
            extra={"publication_id": publication_id, "before": antes_de, "user": ctx.username},
        )
    pub = await db.get(Publication, publication_id)
    return _see_other(f"/postar#grupo-{pub.channel_id}" if pub else "/postar")


@router.get("/postar", response_class=HTMLResponse)
async def post_list(
    request: Request, db: Db, ctx: Admin, aviso: str | None = None, envio: int | None = None
) -> Response:
    now = utcnow()
    notice = POST_NOTICES.get(aviso or "")
    if aviso == "nao-enviado" and envio is not None:
        notice = send_now_notice(await db.get(Publication, envio))
    # Teto por grupo, não global: com um teto só, os primeiros grupos enchiam a lista e os
    # últimos (Tecnologia, Pais) sumiam da tela sem aviso.
    position = (
        func.row_number()
        .over(partition_by=Publication.channel_id, order_by=service.ready_order(now))
        .label("pos")
    )
    ranked = select(Publication.id.label("pid"), position).where(Publication.state == "ready")
    ranked_sq = ranked.subquery()
    rows = (
        await db.execute(
            select(Publication, CatalogProduct.name, CatalogProduct.image_url)
            .join(ranked_sq, ranked_sq.c.pid == Publication.id)
            .join(CatalogProduct, CatalogProduct.id == Publication.product_id)
            .where(ranked_sq.c.pos <= PER_GROUP)
            .order_by(Publication.channel_id, ranked_sq.c.pos)
        )
    ).all()
    totals = dict(
        (
            await db.execute(
                select(Publication.channel_id, func.count())
                .where(Publication.state == "ready")
                .group_by(Publication.channel_id)
            )
        )
        .tuples()
        .all()
    )
    sent_today = dict(
        (
            await db.execute(
                select(Publication.channel_id, func.count())
                .where(Publication.state == "sent", Publication.sent_at >= local_day_start(now))
                .group_by(Publication.channel_id)
            )
        )
        .tuples()
        .all()
    )
    channels = list(
        (
            await db.execute(
                select(Channel)
                .where(or_(Channel.paused.is_(False), Channel.id.in_(list(totals))))
                .order_by(Channel.id)
                .limit(LIST_LIMIT)
            )
        ).scalars()
    )
    groups: dict[int, ReadyGroup] = {}
    for channel in channels:
        groups[channel.id] = ReadyGroup(
            channel,
            await service.channel_status(db, channel, now),
            [],
            totals.get(channel.id, 0),
            sent_today.get(channel.id, 0),
            batch_view(await service.batch_progress(db, channel, now)),
        )
    for pub, name, image in rows:
        if pub.channel_id in groups:
            urgent = is_urgent(pub.flash_ends_at, pub.last_units, pub.last_units_at, now)
            waiting = pub.not_before is not None and pub.not_before > now
            groups[pub.channel_id].rows.append(
                ReadyRow(pub, name or "", image, urgent and not waiting, waiting)
            )
    attention = list(
        (
            await db.execute(
                select(Publication, CatalogProduct.name)
                .join(CatalogProduct, CatalogProduct.id == Publication.product_id)
                .where(Publication.state.in_(("unknown", "failed", "sending")))
                .order_by(Publication.id.desc())
                .limit(LIST_LIMIT)
            )
        ).all()
    )
    recent = list(
        (
            await db.execute(
                select(Publication, CatalogProduct.name, Channel.name)
                .join(CatalogProduct, CatalogProduct.id == Publication.product_id)
                .join(Channel, Channel.id == Publication.channel_id)
                .where(
                    Publication.state.in_(("sent", "skipped")),
                    Publication.created_at >= now - timedelta(days=2),
                )
                .order_by(Publication.id.desc())
                .limit(30)
            )
        ).all()
    )
    return templates.TemplateResponse(
        request,
        "postar.html",
        {
            "ctx": ctx,
            "groups": list(groups.values()),
            "move_targets": await _move_targets(db),
            "notice": notice,
            "attention": attention,
            "recent": recent,
        },
    )


async def _move_targets(db: AsyncSession) -> list[Channel]:
    """Destinos do "Mudar de grupo": um por nicho ativo (o primeiro grupo ativo do nicho)."""
    rows = await db.execute(
        select(Channel)
        .join(Niche, Niche.id == Channel.niche_id)
        .where(Niche.active.is_(True), Channel.paused.is_(False))
        .order_by(Channel.id)
        .limit(LIST_LIMIT)
    )
    targets: dict[int, Channel] = {}
    for channel in rows.scalars():
        targets.setdefault(channel.niche_id, channel)
    return list(targets.values())


async def _publication(db: Db, pub_id: int) -> Publication:
    pub = await db.get(Publication, pub_id)
    if pub is None:
        raise HTTPException(status_code=404)
    return pub


async def _render_manual(
    request: Request,
    db: AsyncSession,
    ctx: AuthContext,
    *,
    form: ManualForm | None = None,
    marked: set[int] | None = None,
    error: str | None = None,
    code: int = 200,
) -> Response:
    targets = await queries.approval_targets(db)
    if form is None:  # formulário novo: o grupo "sempre marcado" já vem marcado
        form = ManualForm(nonce=secrets.token_urlsafe(12))
        marked = {t.id for t in targets if t.always_checked}
    return templates.TemplateResponse(
        request,
        "avulso.html",
        {
            "ctx": ctx,
            "form": form,
            "marked": marked or set(),
            "targets": targets,
            "error": error,
        },
        status_code=code,
    )


@router.get("/postar/avulso", response_class=HTMLResponse)
async def manual_page(request: Request, db: Db, ctx: Admin) -> Response:
    return await _render_manual(request, db, ctx)


async def get_short_client() -> AsyncIterator[httpx.AsyncClient]:
    """Cliente do encurtador do ML (os testes trocam por um falso)."""
    async with ml_shortlink.new_client() as client:
        yield client


ShortClient = Annotated[httpx.AsyncClient, Depends(get_short_client)]


@router.post("/postar/avulso", response_class=HTMLResponse)
async def manual_add(
    request: Request,
    db: Db,
    ctx: Admin,
    short_client: ShortClient,
    url: Annotated[str, Form(max_length=2000)] = "",
    nome: Annotated[str, Form(max_length=300)] = "",
    preco: Annotated[str, Form(max_length=20)] = "",
    de: Annotated[str, Form(max_length=20)] = "",
    pix: Annotated[str, Form(max_length=20)] = "",
    cupom: Annotated[str, Form(max_length=20)] = "",
    ultimas: Annotated[str, Form(max_length=4)] = "",
    foto: Annotated[str, Form(max_length=600)] = "",
    relampago: Annotated[str | None, Form(max_length=4)] = None,
    acaba_em: Annotated[str, Form(max_length=12)] = "",
    nonce: Annotated[str, Form(max_length=40)] = "",
    channel_id: Annotated[list[int] | None, Form()] = None,
    csrf: Annotated[str | None, Form()] = None,
) -> Response:
    """Link avulso: entra em 1º na fila dos grupos marcados (ver `app.distribution.manual`)."""
    check_csrf(ctx, csrf)
    form = ManualForm(
        url=url,
        nome=nome,
        preco=preco,
        de=de,
        pix=pix,
        cupom=cupom,
        ultimas=ultimas,
        foto=foto,
        relampago=bool(relampago),
        acaba_em=acaba_em,
        nonce=nonce,
    )
    marked = set(channel_id or [])
    try:
        # Antes de tocar no banco: o link curto custa uma ida ao encurtador do ML.
        expanded = await expand(url, client=short_client, our_tool_id=settings.ml_affiliate_tool_id)
    except (LinkError, ml_shortlink.ShortLinkError) as exc:
        return await _render_manual(
            request, db, ctx, form=form, marked=marked, error=str(exc), code=422
        )
    try:
        post = post_from_form(
            form,
            sorted(marked),
            url=expanded.url,
            short_url=expanded.own_short.url if expanded.own_short else None,
        )
        result = await queue_manual(db, post, actor=ctx.username, now=utcnow())
        await db.commit()
    except ManualError as exc:
        await db.rollback()
        return await _render_manual(
            request, db, ctx, form=form, marked=marked, error=str(exc), code=422
        )
    except IntegrityError:
        await db.rollback()  # o mesmo produto entrando por dois formulários ao mesmo tempo
        return await _render_manual(
            request, db, ctx, form=form, marked=marked, error="tente de novo", code=409
        )
    if result.created and result.skipped:
        notice = "avulso-parte"
    elif result.created:
        notice = "avulso"
    else:
        notice = "avulso-repetido"
    anchor = f"#grupo-{result.channel_ids[0]}" if result.channel_ids else ""
    return _see_other(f"/postar?aviso={notice}{anchor}")


@router.get("/postar/{pub_id}", response_class=HTMLResponse)
async def post_page(
    request: Request, db: Db, ctx: Admin, pub_id: int, erro: str | None = None
) -> Response:
    pub = await _publication(db, pub_id)
    product = await db.get(CatalogProduct, pub.product_id)
    channel = await db.get(Channel, pub.channel_id)
    assert product is not None and channel is not None
    now = utcnow()
    block = await service.channel_status(db, channel, now)
    fresh = pub.state == "ready" and is_fresh(pub.checked_at, now) and pub.note is None
    message = pub.message if fresh else None
    return templates.TemplateResponse(
        request,
        "publicacao.html",
        {
            "ctx": ctx,
            "pub": pub,
            "product": product,
            "channel": channel,
            "block": block,
            "fresh": fresh,
            "message": message,
            "share": share_url(message) if message and block is None else None,
            "error": ERRORS.get(erro or ""),
            "move_targets": await _move_targets(db),
        },
    )


@router.post("/postar/{pub_id}/foto")
async def set_manual_photo(
    db: Db,
    ctx: Admin,
    pub_id: int,
    foto: Annotated[str, Form(max_length=600)] = "",
    csrf: Annotated[str | None, Form()] = None,
) -> Response:
    """Foto do produto de um link avulso (vale para os posts dele que ainda não saíram)."""
    check_csrf(ctx, csrf)
    pub = await _publication(db, pub_id)
    product = await db.get(CatalogProduct, pub.product_id)
    if pub.tier != MANUAL_TIER or product is None or product.discovered_via != DISCOVERED_MANUAL:
        raise HTTPException(status_code=404)
    try:
        url = check_image_url(foto)
    except ManualError:
        url = None
    if url is None:
        return _see_other(f"/postar/{pub_id}?erro=foto#foto")
    product.image_url = url
    await save(db)
    logger.info(
        "foto do link avulso",
        extra={"publication_id": pub_id, "product_id": product.id, "user": ctx.username},
    )
    return _see_other(f"/postar/{pub_id}#foto")


@router.post("/postar/{pub_id}/revalidar")
async def recheck(
    db: Db, ctx: Admin, client: Ml, pub_id: int, csrf: Annotated[str | None, Form()] = None
) -> Response:
    check_csrf(ctx, csrf)
    pub = await _publication(db, pub_id)
    if pub.state != "ready":
        return _see_other(f"/postar/{pub_id}")
    if pub.tier == MANUAL_TIER:
        # Link avulso: a API não lê o anúncio; vale o preço digitado até vencer.
        now = utcnow()
        reason = manual_block_reason(pub.created_at, pub.flash_ends_at, now)
        if reason:
            pub.state, pub.note = "skipped", reason
        else:
            pub.message = await service.message_for(db, pub, now=now)
        return _see_other(f"/postar/{pub_id}")
    if not pub.item_id:
        return _see_other(f"/postar/{pub_id}?erro=sem-item")
    product = await db.get(CatalogProduct, pub.product_id)
    assert product is not None
    external_id, item_id, approved = product.external_id, pub.item_id, pub.approved_price_cents
    await db.commit()  # nada de transação aberta durante a chamada ao ML
    try:
        result = await service.fetch_recheck(
            client, external_id=external_id, item_id=item_id, approved_cents=approved
        )
    except DistributionError:
        return _see_other(f"/postar/{pub_id}?erro=ml")
    pub = await _publication(db, pub_id)
    if pub.state != "ready":  # outra aba mexeu enquanto o ML respondia
        return _see_other(f"/postar/{pub_id}")
    service.apply_recheck(pub, result, utcnow())
    if result.status == "ok":
        pub.message = await service.message_for(db, pub)
    logger.info(
        "revalidação",
        extra={"publication_id": pub_id, "status": result.status, "price": result.price_cents},
    )
    return _see_other(f"/postar/{pub_id}")


@router.post("/postar/{pub_id}/pix")
async def set_pix(
    db: Db,
    ctx: Admin,
    pub_id: int,
    pix: Annotated[str, Form(max_length=20)] = "",
    csrf: Annotated[str | None, Form()] = None,
) -> Response:
    check_csrf(ctx, csrf)
    pub = await _publication(db, pub_id)
    now = utcnow()
    if not is_fresh(pub.checked_at, now):
        return _see_other(f"/postar/{pub_id}?erro=pix-velho")
    try:
        await service.set_pix(db, pub, pix, now)
    except DistributionError:
        await db.rollback()
        return _see_other(f"/postar/{pub_id}?erro=pix")
    await save(db)
    return _see_other(f"/postar/{pub_id}")


@router.post("/postar/{pub_id}/postei")
async def posted(
    db: Db, ctx: Admin, pub_id: int, csrf: Annotated[str | None, Form()] = None
) -> Response:
    check_csrf(ctx, csrf)
    pub = await _publication(db, pub_id)
    try:
        changed = service.mark_sent(pub, actor=ctx.username, now=utcnow())
    except DistributionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await save(db)
    if changed:
        logger.info("publicação enviada", extra={"publication_id": pub_id, "user": ctx.username})
    return _see_other("/postar")


@router.post("/postar/{pub_id}/conferido")
async def checked_in_group(
    db: Db,
    ctx: Admin,
    pub_id: int,
    outcome: Annotated[str, Form(pattern="^(saiu|nao-saiu|de-novo)$")],
    csrf: Annotated[str | None, Form()] = None,
) -> Response:
    """Envio incerto ou recusado: o operador olha o grupo e diz o que aconteceu."""
    check_csrf(ctx, csrf)
    pub = await _publication(db, pub_id)
    if pub.state not in ("unknown", "failed"):
        raise HTTPException(status_code=409, detail="nada a conferir nesta publicação")
    if outcome == "saiu":
        pub.state, pub.sent_at, pub.actor = "sent", pub.sending_at or utcnow(), ctx.username
    else:
        pub.state, pub.sending_at = "ready", None  # volta para a fila do envio
    pub.note = None
    await save(db)
    logger.info(
        "envio conferido",
        extra={"publication_id": pub_id, "outcome": outcome, "user": ctx.username},
    )
    return _see_other("/postar")


@router.post("/postar/{pub_id}/reenviar")
async def resend(
    db: Db,
    ctx: Admin,
    client: Ml,
    wa_client: WaClient,
    sm: Sessions,
    pub_id: int,
    csrf: Annotated[str | None, Form()] = None,
) -> Response:
    """Cópia da publicação enviada, mandada já (fora do intervalo): pedido explícito do dono."""
    check_csrf(ctx, csrf)
    await db.commit()  # a cópia nasce em transação própria, antes da rede
    try:
        copy_id = await resend_now(
            sm, client, wa_client, publication_id=pub_id, actor=ctx.username, now=utcnow()
        )
    except DistributionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.info("reenvio", extra={"publication_id": pub_id, "copy": copy_id, "user": ctx.username})
    return _see_other("/postar")


@router.post("/postar/{pub_id}/enviar-agora")
async def post_send_now(
    db: Db,
    ctx: Admin,
    client: Ml,
    wa_client: WaClient,
    sm: Sessions,
    pub_id: int,
    csrf: Annotated[str | None, Form()] = None,
) -> Response:
    """Manda já o post da fila, sem esperar o intervalo do grupo (pedido explícito do dono)."""
    check_csrf(ctx, csrf)
    await db.commit()  # o envio abre as próprias transações, fora da chamada de rede
    try:
        sent = await send_now(
            sm, client, wa_client, publication_id=pub_id, actor=ctx.username, now=utcnow()
        )
    except DistributionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.info("envio agora", extra={"publication_id": pub_id, "sent": sent, "user": ctx.username})
    if sent:
        return _see_other("/postar?aviso=enviado")
    return _see_other(f"/postar?aviso=nao-enviado&envio={pub_id}")


@router.post("/postar/{pub_id}/grupo")
async def post_move(
    db: Db,
    ctx: Admin,
    pub_id: int,
    niche_id: Annotated[int, Form()],
    csrf: Annotated[str | None, Form()] = None,
) -> Response:
    """Manda o post (só este produto) para a fila do grupo de outro nicho."""
    check_csrf(ctx, csrf)
    try:
        moved = await move_publication(
            db,
            publication_id=pub_id,
            niche_id=niche_id,
            actor=ctx.username,
            now=utcnow(),
            affiliate_tool_id=settings.ml_affiliate_tool_id,
        )
    except PublicationNotFound:
        raise HTTPException(status_code=404) from None
    except (DistributionError, RecategorizeError) as exc:
        await db.rollback()
        logger.info("mudar de grupo recusado", extra={"publication_id": pub_id, "why": str(exc)})
        return _see_other("/postar?aviso=mover-erro")
    await db.commit()
    notice = (
        "movido" if moved.created else "movido-repetido" if moved.skipped else "movido-sem-grupo"
    )
    anchor = f"#grupo-{moved.channel_id}" if moved.channel_id else ""
    return _see_other(f"/postar?aviso={notice}{anchor}")


@router.post("/postar/{pub_id}/copiar")
async def post_copy(
    db: Db,
    ctx: Admin,
    pub_id: int,
    channel_id: Annotated[list[int] | None, Form()] = None,
    csrf: Annotated[str | None, Form()] = None,
) -> Response:
    """ "Enviar também em": o post fica neste grupo e ganha cópia nos grupos escolhidos."""
    check_csrf(ctx, csrf)
    pub = await db.get(Publication, pub_id)
    anchor = f"#grupo-{pub.channel_id}" if pub else ""
    try:
        copied = await copy_publication(
            db,
            publication_id=pub_id,
            channel_ids=channel_id or [],
            actor=ctx.username,
            now=utcnow(),
            affiliate_tool_id=settings.ml_affiliate_tool_id,
        )
    except PublicationNotFound:
        raise HTTPException(status_code=404) from None
    except DistributionError as exc:
        await db.rollback()
        logger.info("enviar também recusado", extra={"publication_id": pub_id, "why": str(exc)})
        return _see_other(f"/postar?aviso=copiar-erro{anchor}")
    await db.commit()
    if copied.created and copied.skipped:
        notice = "copiado-parte"
    elif copied.created:
        notice = "copiado-agendado" if copied.scheduled else "copiado"
    elif copied.skipped:
        notice = "copiado-repetido"
    else:
        notice = "copiado-ja"  # já estava na fila desses grupos: nada duplicado
    return _see_other(f"/postar?aviso={notice}{anchor}")


@router.post("/postar/{pub_id}/descartar")
async def discard(
    db: Db,
    ctx: Admin,
    pub_id: int,
    csrf: Annotated[str | None, Form()] = None,
    reason: Annotated[str | None, Form(max_length=200)] = None,
) -> Response:
    check_csrf(ctx, csrf)
    pub = await db.get(Publication, pub_id, with_for_update=True)
    if pub is None:
        raise HTTPException(status_code=404)
    if pub.state not in ("awaiting_link", "ready"):
        # Saindo agora, já saiu ou incerto: não some da tela (o "Conferir no grupo" cuida).
        raise HTTPException(status_code=409, detail="este post não está mais na fila de envio")
    try:
        service.discard(pub, actor=ctx.username, reason=reason)
    except DistributionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await save(db)
    logger.info("post tirado da fila", extra={"publication_id": pub.id, "user": ctx.username})
    return _see_other(f"/postar?aviso=removido#grupo-{pub.channel_id}")


# --------------------------------------------------------------------------- convites

INVITE_WEEKDAYS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
INVITE_STATES = {
    "agendado": "agendado",
    "sending": "saindo",
    "sent": "enviado",
    "unknown": "sem confirmação",
    "failed": "falhou",
    "skipped": "pulado",
    "sem_envio": "não sai",
}


@router.get("/convites", response_class=HTMLResponse)
async def invites_page(request: Request, db: Db, ctx: Admin) -> Response:
    error = None
    groups: list[invites.AgendaGroup] = []
    try:
        config = await invites.invite_config(settings.invites_file)
        groups = await invites.agenda(
            db,
            config,
            utcnow(),
            hour=settings.invite_hour,
            morning=settings.invite_morning_enabled,
        )
    except (OSError, ValueError, InviteConfigError) as exc:
        logger.warning("config de convites inválido", extra={"error": str(exc)[:200]})
        error = "O arquivo de convites está com erro: nenhum convite sai até corrigir."
    return templates.TemplateResponse(
        request,
        "convites.html",
        {
            "ctx": ctx,
            "groups": groups,
            "hour": settings.invite_hour,
            "enabled": settings.invites_enabled,
            "error": error,
            "weekdays": INVITE_WEEKDAYS,
            "states": INVITE_STATES,
        },
    )


def _day(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(status_code=422) from None


@router.post("/convites/pular")
async def skip_invite(
    db: Db,
    ctx: Admin,
    channel_id: Annotated[int, Form()],
    day: Annotated[str, Form(max_length=10)],
    slot: Annotated[str, Form(max_length=10)] = "noite",
    csrf: Annotated[str | None, Form()] = None,
) -> Response:
    check_csrf(ctx, csrf)
    skipped = await invites.skip_day(
        db, channel_id=channel_id, day=_day(day), slot=slot, actor=ctx.username, now=utcnow()
    )
    await save(db)
    if skipped:
        logger.info("convite pulado", extra={"channel_id": channel_id, "day": day, "slot": slot})
    return _see_other("/convites")


@router.post("/convites/desfazer")
async def undo_invite_skip(
    db: Db,
    ctx: Admin,
    channel_id: Annotated[int, Form()],
    day: Annotated[str, Form(max_length=10)],
    slot: Annotated[str, Form(max_length=10)] = "noite",
    csrf: Annotated[str | None, Form()] = None,
) -> Response:
    check_csrf(ctx, csrf)
    await invites.undo_skip(db, channel_id=channel_id, day=_day(day), slot=slot, now=utcnow())
    await save(db)
    return _see_other("/convites")


# --------------------------------------------------------------------------- lista vigiada

WATCH_NOTICES = {
    "novo": "Produto vigiado: ele já está na fila, no filtro “Só os vigiados”; o preço chega nos "
    "próximos minutos.",
    "atualizado": "Esse produto já era acompanhado: agora está vigiado, no nicho escolhido.",
    "parado": "O robô parou de acompanhar esse produto.",
}


async def _render_watch(
    request: Request,
    sessions: async_sessionmaker[AsyncSession],
    ctx: Admin,
    *,
    code: int = 200,
    **extra: object,
) -> Response:
    async with sessions() as db:
        rows = await watchlist.watched(db)
        statuses = await queries.watch_statuses(db, [p.id for p, _ in rows])
        niches = list(
            (
                await db.execute(select(Niche).where(Niche.active.is_(True)).order_by(Niche.id))
            ).scalars()
        )
    return templates.TemplateResponse(
        request,
        "vigiar.html",
        {"ctx": ctx, "rows": rows, "statuses": statuses, "niches": niches, **extra},
        status_code=code,
    )


@router.get("/vigiar", response_class=HTMLResponse)
async def watch_page(
    request: Request, sessions: Sessions, ctx: Admin, aviso: str | None = None
) -> Response:
    return await _render_watch(request, sessions, ctx, notice=WATCH_NOTICES.get(aviso or ""))


@router.post("/vigiar", response_class=HTMLResponse)
async def watch_add(
    request: Request,
    sessions: Sessions,
    ctx: Admin,
    ml: Ml,
    short_client: ShortClient,
    url: Annotated[str, Form(max_length=2000)],
    niche_id: Annotated[int, Form()],
    csrf: Annotated[str | None, Form()] = None,
) -> Response:
    check_csrf(ctx, csrf)
    try:
        expanded = await expand(url, client=short_client, our_tool_id=settings.ml_affiliate_tool_id)
        if expanded.own_short is not None:
            raise watchlist.WatchError(
                "o link curto de afiliado não diz qual é o produto, e o robô precisa dele para "
                "acompanhar o preço. Abra o produto no navegador e copie o endereço da página "
                "(com /p/MLB…)"
            )
        result = await watchlist.watch(
            sessions, ml, url=expanded.url, niche_id=niche_id, now=utcnow()
        )
    except (LinkError, ml_shortlink.ShortLinkError) as exc:
        return await _render_watch(request, sessions, ctx, error=str(exc), url=url, code=422)
    except watchlist.WatchError as exc:
        return await _render_watch(request, sessions, ctx, error=str(exc), url=url, code=422)
    logger.info(
        "produto vigiado",
        extra={"product_id": result.product_id, "new": result.created, "user": ctx.username},
    )
    return _see_other("/vigiar?aviso=" + ("novo" if result.created else "atualizado"))


@router.post("/vigiar/{product_id}/parar")
async def watch_stop(
    db: Db, ctx: Admin, product_id: int, csrf: Annotated[str | None, Form()] = None
) -> Response:
    check_csrf(ctx, csrf)
    if not await watchlist.unwatch(db, product_id):
        raise HTTPException(status_code=404)
    await save(db)
    return _see_other("/vigiar?aviso=parado")
