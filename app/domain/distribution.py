"""Regras da distribuição (Fase 2a: envio manual pelo celular do operador). Puro, sem I/O.

- Política do grupo: janela em hora de Brasília, teto por dia, intervalo mínimo, pausa.
- Repetição: o mesmo produto não volta ao mesmo grupo em 7 dias, a menos que esteja 10% mais
  barato e seja Excelente ou Excepcional.
- Link de afiliado do ML: só o curto oficial do gerador (`meli.la` ou `/sec/`), nunca alterado
  (termos do afiliado 1.8).
- Revalidação: o preço da oferta escolhida é relido antes de postar; subiu mais de 3%, não sai.
- Mensagem: `#publi` na primeira linha (CONAR), preço com a hora da verificação e nada calculado
  por nós sobre o histórico do ML (P6).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from app.domain.catalog_offers import Offer

SP = ZoneInfo("America/Sao_Paulo")
DEDUP_WINDOW = timedelta(days=7)
REPOST_DROP = 0.10
REPOST_TIERS = frozenset({"excepcional", "excelente"})
PRICE_UP_TOLERANCE = 0.03
FRESH_FOR_SEND = timedelta(minutes=5)
ML_TAG_RE = re.compile(r"^[a-z0-9]{1,30}$")
INVITE_PREFIX = "https://chat.whatsapp.com/"
# Saída do "Gerador de produtos recomendados" do ML: curto oficial em meli.la ou /sec/.
AFFILIATE_HOSTS = frozenset({"meli.la", "mercadolivre.com", "www.mercadolivre.com"})
NICHE_EMOJI = {"ferramentas": "🔧", "beleza": "💄", "tenis-esporte": "👟"}
MAX_NAME = 120


def format_brl(cents: int | None) -> str:
    if cents is None:
        return "—"
    reais = f"{cents / 100:,.2f}"
    return "R$ " + reais.replace(",", "_").replace(".", ",").replace("_", ".")


def product_page_url(permalink: str | None, external_id: str) -> str:
    """Página do produto de catálogo no ML (/p/MLB…), mesmo quando a API não trouxe o link."""
    return permalink or f"https://www.mercadolivre.com.br/p/{external_id}"


def offer_page_url(permalink: str | None, external_id: str, item_id: str | None) -> str:
    """Página do produto com a oferta escolhida já selecionada (sem item, a página do produto)."""
    base = product_page_url(permalink, external_id)
    if not item_id:
        return base
    return f"{base}{'&' if '?' in base else '?'}pdp_filters=item_id:{item_id}"


# --------------------------------------------------------------------------- política do grupo


@dataclass(frozen=True, slots=True)
class ChannelPolicy:
    max_per_day: int
    min_interval: timedelta
    window_start_hour: int
    window_end_hour: int
    paused: bool = False


def local_day_start(now: datetime) -> datetime:
    """Meia-noite de hoje em Brasília (aware), para contar os envios do dia."""
    local = now.astimezone(SP)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def send_block_reason(
    policy: ChannelPolicy, *, now: datetime, sent_today: int, last_sent_at: datetime | None
) -> str | None:
    """Por que este grupo não pode receber agora (ou None se pode)."""
    if policy.paused:
        return "grupo pausado"
    local = now.astimezone(SP)
    if not policy.window_start_hour <= local.hour < policy.window_end_hour:
        return f"fora da janela ({policy.window_start_hour:02d}h às {policy.window_end_hour:02d}h)"
    if sent_today >= policy.max_per_day:
        return f"limite do dia atingido ({policy.max_per_day})"
    if last_sent_at is not None and now - last_sent_at < policy.min_interval:
        allowed = (last_sent_at + policy.min_interval).astimezone(SP)
        return f"próximo envio a partir de {allowed:%H:%M}"
    return None


# --------------------------------------------------------------------------- repetição


@dataclass(frozen=True, slots=True)
class PriorPost:
    price_cents: int
    created_at: datetime


def repeat_block_reason(
    prior: list[PriorPost], *, price_cents: int, tier: str, now: datetime
) -> str | None:
    """O mesmo produto no mesmo grupo em 7 dias só volta 10% mais barato e Excelente+."""
    recent = [p for p in prior if now - p.created_at < DEDUP_WINDOW]
    if not recent:
        return None
    last = max(recent, key=lambda p: p.created_at)
    if tier in REPOST_TIERS and price_cents <= last.price_cents * (1 - REPOST_DROP):
        return None
    return f"já foi para este grupo em {last.created_at.astimezone(SP):%d/%m}"


# --------------------------------------------------------------------------- links do ML


class LinkError(ValueError):
    """Bloco de links colado que não confere (mensagem pronta para o operador)."""


def parse_affiliate_links(text: str, expected: int) -> list[str]:
    """Um link por linha, na mesma ordem das URLs entregues ao gerador do ML."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != expected:
        raise LinkError(f"vieram {len(lines)} links para {expected} produtos")
    for number, url in enumerate(lines, start=1):
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or host not in AFFILIATE_HOSTS or len(url) > 512:
            raise LinkError(f"linha {number}: não é um link curto do gerador do ML")
        if host != "meli.la" and not parts.path.startswith("/sec/"):
            raise LinkError(f"linha {number}: link do ML sem /sec/ (use o link curto gerado)")
    return lines


# --------------------------------------------------------------------------- revalidação


@dataclass(frozen=True, slots=True)
class Recheck:
    status: str  # ok | gone | price_up
    price_cents: int | None


def recheck_offer(raw_offers: list[dict[str, Any]], item_id: str, approved_cents: int) -> Recheck:
    """Preço atual da oferta que o link aponta (não do produto): é o que o membro vai ver."""
    for raw in raw_offers:
        offer = Offer.from_api(raw)
        if offer is not None and offer.item_id == item_id:
            if offer.price_cents > approved_cents * (1 + PRICE_UP_TOLERANCE):
                return Recheck("price_up", offer.price_cents)
            return Recheck("ok", offer.price_cents)
    return Recheck("gone", None)


def is_fresh(checked_at: datetime | None, now: datetime) -> bool:
    return checked_at is not None and now - checked_at <= FRESH_FOR_SEND


# --------------------------------------------------------------------------- mensagem


def render_message(
    *,
    niche_slug: str,
    niche_name: str,
    product_name: str,
    price_cents: int,
    checked_at: datetime,
    official: bool,
    url: str,
) -> str:
    local = checked_at.astimezone(SP)
    name = product_name.strip()
    if len(name) > MAX_NAME:
        name = name[: MAX_NAME - 1].rstrip() + "…"
    return "\n".join(
        [
            "#publi · link de afiliado (posso receber comissão)",
            f"{NICHE_EMOJI.get(niche_slug, '🛍️')} {niche_name.upper()} · preço verificado em "
            f"{local:%d/%m} às {local:%H:%M}",
            f"{name}{' · loja oficial' if official else ''}",
            f"💰 {format_brl(price_cents)} no Mercado Livre",
            "Preço e estoque podem mudar a qualquer momento.",
            f"🛒 {url}",
        ]
    )


def share_url(message: str) -> str:
    """Abre o WhatsApp do celular com o texto pronto; o operador escolhe o grupo."""
    return "https://wa.me/?text=" + quote(message, safe="")
