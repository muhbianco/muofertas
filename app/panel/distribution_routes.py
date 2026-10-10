"""Painel da distribuição manual (Fase 2a): grupos, links do ML e postagem pelo celular."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.core.config import settings
from app.core.database import get_sessionmaker
from app.core.logging import get_logger
from app.distribution import service
from app.distribution.sender import resend_now
from app.distribution.service import DistributionError
from app.domain.distribution import INVITE_PREFIX, ML_TAG_RE, is_fresh, share_url
from app.integrations.mercadolivre import MercadoLivreClient
from app.integrations.wuzapi import WuzapiClient, WuzapiError
from app.models import CatalogProduct, Channel, Niche, Publication
from app.panel.auth import check_csrf
from app.panel.routes import Admin, Db, templates
from app.panel.whatsapp_routes import get_wa_client
from app.whatsapp import service as wa

logger = get_logger(__name__)
router = APIRouter()
LIST_LIMIT = 100

ERRORS = {
    "ml": "O Mercado Livre não respondeu. Tente revalidar de novo.",
    "pix": "Preço do Pix recusado: precisa ser menor que o anúncio e até 30% abaixo (ex.: 49,90).",
    "pix-velho": "A revalidação venceu: revalide o preço e informe o Pix de novo.",
    "sem-item": "Esta publicação não tem a oferta registrada; descarte e aprove de novo.",
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
    min_interval_minutes: int
    window_start_hour: int
    window_end_hour: int
    paused: bool
    transport: str = "manual"


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
    if not 1 <= form.max_per_day <= 50:
        return "envios por dia: de 1 a 50"
    if not 5 <= form.min_interval_minutes <= 600:
        return "intervalo: de 5 a 600 minutos"
    if not (0 <= form.window_start_hour < form.window_end_hour <= 24):
        return "janela: início antes do fim, entre 0h e 24h"
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
    min_interval_minutes: Annotated[int, Form()],
    window_start_hour: Annotated[int, Form()],
    window_end_hour: Annotated[int, Form()],
    csrf: Annotated[str | None, Form()] = None,
    ml_tag: Annotated[str | None, Form(max_length=30)] = None,
    invite_url: Annotated[str | None, Form(max_length=255)] = None,
    paused: Annotated[str | None, Form()] = None,
    transport: Annotated[str, Form(max_length=12)] = "manual",
    channel_id: int | None = None,
) -> Response:
    check_csrf(ctx, csrf)
    form = ChannelForm(
        name=name.strip(),
        niche_id=niche_id,
        ml_tag=(ml_tag or "").strip() or None,
        invite_url=(invite_url or "").strip() or None,
        max_per_day=max_per_day,
        min_interval_minutes=min_interval_minutes,
        window_start_hour=window_start_hour,
        window_end_hour=window_end_hour,
        paused=paused == "1",
        transport=transport,
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
    if channel_id is None:
        channel = Channel(created_at=utcnow(), transport="manual")
        db.add(channel)
    else:
        found = await db.get(Channel, channel_id)
        if found is None:
            raise HTTPException(status_code=404)
        channel = found
    if form.transport == "wuzapi" and not channel.wa_jid:
        return await _render_groups(
            request,
            db,
            ctx,
            "envio automático só com o grupo ligado ao WhatsApp (seção 'No WhatsApp do chip')",
            422,
        )
    for key, value in vars(form).items():
        setattr(channel, key, value)
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


@dataclass
class ReadyGroup:
    channel: Channel
    block: str | None
    rows: list[ReadyRow]


@router.get("/postar", response_class=HTMLResponse)
async def post_list(request: Request, db: Db, ctx: Admin) -> Response:
    now = utcnow()
    rows = (
        await db.execute(
            select(Publication, CatalogProduct.name, CatalogProduct.image_url)
            .join(CatalogProduct, CatalogProduct.id == Publication.product_id)
            .where(Publication.state == "ready")
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
    groups: dict[int, ReadyGroup] = {}
    for pub, name, image in rows:
        if pub.channel_id not in groups:
            channel = channels[pub.channel_id]
            groups[pub.channel_id] = ReadyGroup(
                channel, await service.channel_status(db, channel, now), []
            )
        groups[pub.channel_id].rows.append(ReadyRow(pub, name or "", image))
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
            "attention": attention,
            "recent": recent,
        },
    )


async def _publication(db: Db, pub_id: int) -> Publication:
    pub = await db.get(Publication, pub_id)
    if pub is None:
        raise HTTPException(status_code=404)
    return pub


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
        },
    )


@router.post("/postar/{pub_id}/revalidar")
async def recheck(
    db: Db, ctx: Admin, client: Ml, pub_id: int, csrf: Annotated[str | None, Form()] = None
) -> Response:
    check_csrf(ctx, csrf)
    pub = await _publication(db, pub_id)
    if pub.state != "ready":
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
        return _see_other(f"/postar/{pub_id}?erro=pix")
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


@router.post("/postar/{pub_id}/descartar")
async def discard(
    db: Db,
    ctx: Admin,
    pub_id: int,
    csrf: Annotated[str | None, Form()] = None,
    reason: Annotated[str | None, Form(max_length=200)] = None,
) -> Response:
    check_csrf(ctx, csrf)
    pub = await _publication(db, pub_id)
    try:
        service.discard(pub, actor=ctx.username, reason=reason)
    except DistributionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _see_other("/postar")
