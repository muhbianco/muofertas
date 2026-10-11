"""Regras da distribuição (Fase 2a: envio manual pelo celular do operador). Puro, sem I/O.

- Política do grupo: janela em hora de Brasília, teto por dia, intervalo mínimo, pausa.
- Repetição: o mesmo produto não volta ao mesmo grupo em 7 dias, a menos que esteja 10% mais
  barato e seja Excelente ou Excepcional.
- Link de afiliado do ML: só o curto oficial do gerador (`meli.la` ou `/sec/`), nunca alterado
  (termos do afiliado 1.8).
- Revalidação: o preço da oferta escolhida é relido antes de postar; subiu mais de 3%, não sai.
- Mensagem: começa por `#publi` (CDC art. 36 e guia CONAR 2026: publicidade identificável em
  primeiro plano), preço com a hora da verificação e nada calculado por nós sobre o histórico do
  ML (P6). O aviso completo de afiliado fica na descrição do grupo.
- Pix: a API não traz o desconto do Pix (sondagem de 10/10/2026). O operador pode digitar o que a
  página mostra; entra se for menor que o preço e no máximo 30% abaixo (proteção contra digitação).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
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
NICHE_EMOJI = {
    "ferramentas": "🔧",
    "beleza": "💄",
    "tenis-esporte": "👟",
    "tecnologia": "💻",
    "casa-mimos": "🏠",
    "pais-maes-filhos": "👶",
}
MAX_NAME = 90
PIX_MAX_DISCOUNT = 0.30
# O Pix é visto pelo operador na aprovação; depois disso a mensagem sai sem ele.
PIX_VALID_FOR = timedelta(hours=3)
# Variação no intervalo do envio automático: grupos não recebem no mesmo minuto exato.
MAX_JITTER_SECONDS = 600
# Envio espalhado na janela: intervalo médio = janela ÷ envios do dia, nunca abaixo deste piso.
MIN_SPACING_MINUTES = 10
MIN_DISCOUNT_PCT = 5
MAX_DISCOUNT_PCT = 70
MAX_ANCHOR_OVER_REFERENCE = 1.5


def format_brl(cents: int | None) -> str:
    if cents is None:
        return "—"
    reais = f"{cents / 100:,.2f}"
    return "R$ " + reais.replace(",", "_").replace(".", ",").replace("_", ".")


def product_page_url(permalink: str | None, external_id: str) -> str:
    """Página do produto de catálogo no ML (/p/MLB…), mesmo quando a API não trouxe o link."""
    return permalink or f"https://www.mercadolivre.com.br/p/{external_id}"


def build_affiliate_url(target_url: str, *, tag: str, tool_id: str) -> str:
    """Link de afiliado no formato do ML: página da oferta + `matt_word` (etiqueta) +
    `matt_tool` (código do afiliado). O link curto do gerador leva ainda um `ref` cifrado que
    não dá para reproduzir; se a atribuição vale sem ele é o teste da etiqueta `teste1`
    (decisão do dono em 10/10/2026 de automatizar o link, ciente da cláusula 3.1.2)."""
    if not ML_TAG_RE.match(tag) or not tool_id.isdigit():
        raise LinkError("etiqueta ou código de afiliado inválido")
    query = f"matt_word={tag}&matt_tool={tool_id}&forceInApp=true"
    return f"{target_url}{'&' if '?' in target_url else '?'}{query}"


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


def spacing_minutes(max_per_day: int, window_start_hour: int, window_end_hour: int) -> int:
    """Intervalo médio entre envios do grupo: das 07h às 22h, com 20 por dia → 45 min."""
    window = max(window_end_hour - window_start_hour, 0) * 60
    return max(window // max(max_per_day, 1), MIN_SPACING_MINUTES)


def max_sends_in_window(window_start_hour: int, window_end_hour: int) -> int:
    """Quantos envios cabem na janela sem descer do piso de intervalo."""
    return max(window_end_hour - window_start_hour, 0) * 60 // MIN_SPACING_MINUTES


def min_gap(max_per_day: int, window_start_hour: int, window_end_hour: int) -> timedelta:
    """Menor espera entre dois envios: o intervalo médio menos metade da variação.

    O envio automático soma de 0 a 10 min fixos por publicação (`jitter`), então a espera média
    fica no intervalo médio e os envios do dia cabem na janela.
    """
    return timedelta(
        minutes=spacing_minutes(max_per_day, window_start_hour, window_end_hour)
    ) - timedelta(seconds=MAX_JITTER_SECONDS // 2)


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
    original_cents: int | None = None  # o "de" do próprio anúncio, se houver


def recheck_offer(raw_offers: list[dict[str, Any]], item_id: str, approved_cents: int) -> Recheck:
    """Preço atual da oferta que o link aponta (não do produto): é o que o membro vai ver."""
    for raw in raw_offers:
        offer = Offer.from_api(raw)
        if offer is not None and offer.item_id == item_id:
            if offer.price_cents > approved_cents * (1 + PRICE_UP_TOLERANCE):
                return Recheck("price_up", offer.price_cents, offer.original_price_cents)
            return Recheck("ok", offer.price_cents, offer.original_price_cents)
    return Recheck("gone", None)


def is_fresh(checked_at: datetime | None, now: datetime) -> bool:
    return checked_at is not None and now - checked_at <= FRESH_FOR_SEND


def pix_to_show(
    pix_cents: int | None, pix_at: datetime | None, price_cents: int, checked_at: datetime
) -> int | None:
    """O Pix informado vale por 3 horas e só enquanto continua abaixo do preço relido."""
    if pix_cents is None or pix_at is None:
        return None
    if checked_at - pix_at > PIX_VALID_FOR or pix_cents >= price_cents:
        return None
    return pix_cents


@dataclass(frozen=True, slots=True)
class ListingDiscount:
    original_cents: int
    percent: int


def listing_discount(
    price_cents: int, original_cents: int | None, reference_cents: int | None
) -> ListingDiscount | None:
    """ "De/por" do próprio anúncio, com travas contra o "de" inflado (metade do dobro):

    - de 5% a 70% (acima disso é erro de preço ou golpe; abaixo não vale a linha);
    - com histórico nosso, o "de" não pode passar de 1,5 x o preço de referência medido.
    """
    if original_cents is None or original_cents <= price_cents:
        return None
    percent = (original_cents - price_cents) * 100 // original_cents  # truncado, como a página
    if not MIN_DISCOUNT_PCT <= percent <= MAX_DISCOUNT_PCT:
        return None
    if reference_cents is not None and original_cents > reference_cents * MAX_ANCHOR_OVER_REFERENCE:
        return None
    return ListingDiscount(original_cents, percent)


def jitter(publication_id: int) -> timedelta:
    """De 0 a 10 minutos, fixo por publicação (o mesmo item não "anda" entre ciclos)."""
    return timedelta(seconds=(publication_id * 7919) % MAX_JITTER_SECONDS)


def label_of(niche_name: str) -> str:
    """ "Beleza: maquiagem, perfumes e cuidados" → "BELEZA"."""
    return niche_name.split(":", 1)[0].strip().upper()


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
    pix_cents: int | None = None,
    discount: ListingDiscount | None = None,
    coupon_cents: int | None = None,
) -> str:
    """Formato do WhatsApp: *negrito*, _itálico_ e blocos separados por linha em branco.

    `#publi` abre a mensagem (identificação em primeiro plano); o resto é para ler no celular.
    """
    local = checked_at.astimezone(SP)
    lines = [
        f"#publi · {NICHE_EMOJI.get(niche_slug, '🛍️')} *{label_of(niche_name)}*",
        "",
        f"*{short_name(product_name)}*",
    ]
    if official:
        lines.append("✅ Loja oficial")
    lines.append("")
    if discount is not None:
        lines += [
            f"🔥 *{discount.percent}% OFF*",
            f"💰 De ~{format_brl(discount.original_cents)}~ por *{format_brl(price_cents)}* "
            "no Mercado Livre",
        ]
    else:
        lines.append(f"💰 *{format_brl(price_cents)}* no Mercado Livre")
    if pix_cents:
        lines.append(f"⚡ *{format_brl(pix_cents)} no Pix*")
    if coupon_cents:
        lines.append(f"🎟️ *{format_brl(coupon_cents)} com cupom da loja* (ative na página)")
    lines += [
        "",
        f"👉 {url}",
        "",
        f"_Preço conferido em {local:%d/%m} às {local:%H:%M}; pode mudar a qualquer momento._",
    ]
    return "\n".join(lines)


def short_name(product_name: str) -> str:
    """Título do anúncio sem os marcadores do WhatsApp e cortado na palavra (até 90)."""
    name = " ".join(product_name.translate(str.maketrans("", "", "*_~`")).split())
    if len(name) <= MAX_NAME:
        return name
    cut = name[: MAX_NAME - 1].rsplit(" ", 1)[0].rstrip(" ,.-\u2013")
    return cut + "…"


class PixError(ValueError):
    """Preço do Pix digitado que não confere."""


def parse_brl(text: str) -> int | None:
    """'49,50', 'R$ 1.299,00', '49.50' → centavos. Vazio → None."""
    raw = text.strip().removeprefix("R$").strip().replace(" ", "")
    if not raw:
        return None
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif raw.count(".") == 1 and len(raw.split(".")[1]) == 3:
        raw = raw.replace(".", "")  # 1.299 = mil duzentos e noventa e nove
    try:
        cents = Decimal(raw) * 100
    except InvalidOperation as exc:
        raise PixError("preço do Pix: use o formato 49,90") from exc
    if not cents.is_finite() or cents <= 0 or cents != cents.to_integral_value():
        raise PixError("preço do Pix: use o formato 49,90")
    return int(cents)


def check_special_price(cents: int, card_cents: int, what: str) -> None:
    """Preço visto pelo operador na página (Pix, com cupom): menor e até 30% abaixo."""
    if cents >= card_cents:
        raise PixError(f"o preço {what} precisa ser menor que o preço do anúncio")
    if cents < card_cents * (1 - PIX_MAX_DISCOUNT):
        raise PixError(f"preço {what} mais de 30% abaixo do anúncio: confira o valor digitado")


def check_pix(pix_cents: int, card_cents: int) -> None:
    check_special_price(pix_cents, card_cents, "do Pix")


def check_coupon(coupon_cents: int, card_cents: int) -> None:
    check_special_price(coupon_cents, card_cents, "com cupom")


def share_url(message: str) -> str:
    """Abre o WhatsApp do celular com o texto pronto; o operador escolhe o grupo."""
    return "https://wa.me/?text=" + quote(message, safe="")
