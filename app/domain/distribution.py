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
# Nível das publicações de link avulso (o operador escolheu o anúncio e digitou o preço).
MANUAL_TIER = "avulso"
# Volta ao grupo em 7 dias se 10% mais barato: Excelente+, e o avulso (o operador escolheu).
REPOST_TIERS = frozenset({"excepcional", "excelente", MANUAL_TIER})
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
# "Últimas N unidades" (visto na página pelo operador; a API não traz estoque): vale menos que o
# Pix porque estoque baixo acaba rápido, e só para número pequeno (é o que a página mostra).
LAST_UNITS_VALID_FOR = timedelta(hours=2)
MAX_LAST_UNITS = 20
# Variação no intervalo do envio automático: grupos não recebem no mesmo minuto exato.
MAX_JITTER_SECONDS = 600
# Envio espalhado na janela: intervalo médio = janela ÷ envios do dia, nunca abaixo deste piso.
# 13/10/2026: até 200 por dia (decisão do dono); das 07h às 22h dá um a cada ~4,5 min.
MIN_SPACING_MINUTES = 4
MAX_PER_DAY = 200
# Modo de envio do grupo (0031). "ritmo": `max_per_day` espalhados na janela, como sempre foi.
# "lote": `batch_size` anúncios a cada `batch_every_minutes`, nos horários alinhados ao começo da
# janela (das 7h, a cada 180 min: 7h, 10h, 13h, 16h, 19h), pedido do dono em 16/10/2026. Link
# avulso, relâmpago e últimas unidades saem a qualquer hora da janela e não gastam o lote.
SEND_STEADY = "ritmo"
SEND_BATCH = "lote"
SEND_MODES = (SEND_STEADY, SEND_BATCH)
BATCH_GAP = timedelta(minutes=1)  # entre um post e o seguinte dentro do lote
BATCH_JITTER_SECONDS = 20
MAX_BATCH_SIZE = 50
MIN_BATCH_EVERY_MINUTES = 30
# O lote fica aberto só um tempo depois do horário: o bastante para sair inteiro (um post por
# ciclo do worker, 1 min entre eles), não um conta-gotas até o próximo. Em 16/10/2026 o lote das
# 7h ainda tinha cota às 9h e seguiu soltando post, contra o "20 às 7h, 10h…" da descrição.
MIN_BATCH_OPEN = timedelta(minutes=30)
BATCH_OPEN_PER_POST = timedelta(minutes=2)
MIN_DISCOUNT_PCT = 5
MAX_DISCOUNT_PCT = 70
MIN_OFFICIAL_GAP_PCT = 10  # "mais barato que a loja oficial" só a partir disso
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
class BatchPlan:
    size: int
    every: timedelta


@dataclass(frozen=True, slots=True)
class ChannelPolicy:
    max_per_day: int
    min_interval: timedelta
    window_start_hour: int
    window_end_hour: int
    paused: bool = False
    jitter_cap_seconds: int = MAX_JITTER_SECONDS
    batch: BatchPlan | None = None  # só no modo "lote"


def batch_starts(
    day: datetime, window_start_hour: int, window_end_hour: int, every: timedelta
) -> list[datetime]:
    """Horários dos lotes do dia de `day` (Brasília): do começo da janela, a cada `every`."""
    midnight = day.astimezone(SP).replace(hour=0, minute=0, second=0, microsecond=0)
    start = midnight + timedelta(hours=window_start_hour)
    end = midnight + timedelta(hours=window_end_hour)
    slots: list[datetime] = []
    while start < end and every > timedelta(0):
        slots.append(start)
        start += every
    return slots


def hour_label(moment: datetime) -> str:
    """ "10h", ou "10:30" quando não é hora cheia (Brasília)."""
    local = moment.astimezone(SP)
    return f"{local:%H}h" if local.minute == 0 else f"{local:%H:%M}"


def batch_open_for(policy: ChannelPolicy) -> timedelta:
    """Quanto tempo o lote aceita post comum depois do horário dele (20 posts: 40 min)."""
    if policy.batch is None:
        return timedelta(0)
    wanted = max(MIN_BATCH_OPEN, policy.batch.size * BATCH_OPEN_PER_POST)
    return min(policy.batch.every, wanted)


def current_batch_start(now: datetime, policy: ChannelPolicy) -> datetime | None:
    if policy.batch is None:
        return None
    slots = batch_starts(now, policy.window_start_hour, policy.window_end_hour, policy.batch.every)
    started = [s for s in slots if s <= now]
    return started[-1] if started else None


def next_batch_start(now: datetime, policy: ChannelPolicy) -> datetime | None:
    if policy.batch is None:
        return None
    slots = batch_starts(now, policy.window_start_hour, policy.window_end_hour, policy.batch.every)
    return next((s for s in slots if s > now), None)


def jitter_cap(spacing: int) -> int:
    """Variação do envio: até 10 min, mas nunca maior que o próprio intervalo (com 200 por dia o
    intervalo é de ~4 min, e 10 min de variação colavam dois posts no mesmo minuto)."""
    return min(MAX_JITTER_SECONDS, spacing * 60)


def spacing_minutes(max_per_day: int, window_start_hour: int, window_end_hour: int) -> int:
    """Intervalo médio entre envios do grupo: das 07h às 22h, com 20 por dia → 45 min."""
    window = max(window_end_hour - window_start_hour, 0) * 60
    return max(window // max(max_per_day, 1), MIN_SPACING_MINUTES)


def max_sends_in_window(window_start_hour: int, window_end_hour: int) -> int:
    """Quantos envios cabem na janela sem descer do piso de intervalo."""
    return max(window_end_hour - window_start_hour, 0) * 60 // MIN_SPACING_MINUTES


def min_gap(max_per_day: int, window_start_hour: int, window_end_hour: int) -> timedelta:
    """Menor espera entre dois envios: o intervalo médio menos metade da variação.

    O envio automático soma uma variação fixa por publicação (`jitter`, até `jitter_cap`), então a
    espera média fica no intervalo médio e os envios do dia cabem na janela.
    """
    spacing = spacing_minutes(max_per_day, window_start_hour, window_end_hour)
    return timedelta(minutes=spacing) - timedelta(seconds=jitter_cap(spacing) // 2)


def local_day_start(now: datetime) -> datetime:
    """Meia-noite de hoje em Brasília (aware), para contar os envios do dia."""
    local = now.astimezone(SP)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def send_block_reason(
    policy: ChannelPolicy,
    *,
    now: datetime,
    sent_today: int,
    last_sent_at: datetime | None,
    batch_sent: int = 0,
    priority: bool = False,
) -> str | None:
    """Por que este grupo não pode receber agora (ou None se pode).

    No modo lote, `batch_sent` é quantos posts comuns já saíram no lote atual; o post
    `priority` (avulso, relâmpago, últimas unidades) sai mesmo com o lote completo."""
    if policy.paused:
        return "grupo pausado"
    local = now.astimezone(SP)
    if not policy.window_start_hour <= local.hour < policy.window_end_hour:
        return f"fora da janela ({policy.window_start_hour:02d}h às {policy.window_end_hour:02d}h)"
    if sent_today >= policy.max_per_day:
        return f"limite do dia atingido ({policy.max_per_day})"
    if policy.batch is not None and not priority:
        current = current_batch_start(now, policy)
        upcoming = next_batch_start(now, policy)
        then = f"próximo lote às {hour_label(upcoming)}" if upcoming else "era o último do dia"
        if current is None:
            return then
        if now >= current + batch_open_for(policy):
            return f"lote das {hour_label(current)} encerrado; {then}"
        if batch_sent >= policy.batch.size:
            return f"lote das {hour_label(current)} completo ({policy.batch.size}); {then}"
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
    status: str  # ok | gone | price_up | not_found (404: confirmar antes de tirar da fila)
    price_cents: int | None
    original_cents: int | None = None  # o "de" do próprio anúncio, se houver
    official_cents: int | None = None  # menor preço de loja oficial do produto, na mesma leitura


def recheck_offer(raw_offers: list[dict[str, Any]], item_id: str, approved_cents: int) -> Recheck:
    """Preço atual da oferta que o link aponta (não do produto): é o que o membro vai ver.

    Junto vem o menor preço de loja oficial (novo) do mesmo produto, para o post comparar.
    """
    offers = [o for o in map(Offer.from_api, raw_offers) if o is not None]
    official = min(
        (
            o.price_cents
            for o in offers
            if o.official_store_id is not None and o.condition in (None, "new")
        ),
        default=None,
    )
    for offer in offers:
        if offer.item_id == item_id:
            status = (
                "price_up"
                if offer.price_cents > approved_cents * (1 + PRICE_UP_TOLERANCE)
                else "ok"
            )
            return Recheck(status, offer.price_cents, offer.original_price_cents, official)
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


def official_comparison(price_cents: int, official_cents: int | None) -> ListingDiscount | None:
    """Quanto o anúncio sai mais barato que a loja oficial do mesmo produto (post de vendedor
    comum). Mesmas travas do % OFF, com piso maior: abaixo de 10% não vale a linha, acima de 70%
    cheira a falsificação ou erro de preço."""
    if official_cents is None or official_cents <= price_cents:
        return None
    percent = (official_cents - price_cents) * 100 // official_cents
    if not MIN_OFFICIAL_GAP_PCT <= percent <= MAX_DISCOUNT_PCT:
        return None
    return ListingDiscount(official_cents, percent)


REPUTATION = {
    "5_green": "verde",
    "4_light_green": "verde-claro",
    "3_yellow": "amarela",
    "2_orange": "laranja",
    "1_red": "vermelha",
}


def seller_line(level_id: str | None, sales: int | None) -> str | None:
    """ "Vendedor com reputação verde e 2.211 vendas" (só para oferta fora da loja oficial)."""
    color = REPUTATION.get(level_id or "")
    if color is None:
        return None
    if sales:
        return f"Vendedor com reputação {color} e {sales:,} vendas".replace(",", ".")
    return f"Vendedor com reputação {color}"


def jitter(publication_id: int, cap_seconds: int = MAX_JITTER_SECONDS) -> timedelta:
    """De 0 ao teto (10 min, ou o intervalo do grupo se menor), fixo por publicação (o mesmo item
    não "anda" entre ciclos)."""
    return timedelta(seconds=(publication_id * 7919) % max(cap_seconds, 1))


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
    versus_official: ListingDiscount | None = None,
    seller: str | None = None,
    last_units: int | None = None,
    flash_ends_at: datetime | None = None,
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
    if flash_ends_at is not None:
        end = flash_ends_at.astimezone(SP)
        when = f"às {end:%H:%M}" if end.date() == local.date() else f"em {end:%d/%m} às {end:%H:%M}"
        lines.append(f"⏰ *OFERTA RELÂMPAGO* · acaba {when}")
    if discount is not None:
        lines += [
            f"🔥 *{discount.percent}% OFF*",
            f"💰 De ~{format_brl(discount.original_cents)}~ por *{format_brl(price_cents)}* "
            "no Mercado Livre",
        ]
    elif versus_official is not None:
        # Sem "de" no próprio anúncio: a comparação é com a loja oficial do mesmo produto, e o
        # texto diz de onde vem o preço riscado.
        lines += [
            f"🔥 *{versus_official.percent}% mais barato que a loja oficial*",
            f"🏬 Na loja oficial: ~{format_brl(versus_official.original_cents)}~",
            f"💰 Aqui: *{format_brl(price_cents)}* no Mercado Livre",
        ]
    else:
        lines.append(f"💰 *{format_brl(price_cents)}* no Mercado Livre")
    if seller and not official:
        lines.append(f"👍 {seller}")
    if pix_cents:
        lines.append(f"⚡ *{format_brl(pix_cents)} no Pix*")
    if coupon_cents:
        lines.append(f"🎟️ *{format_brl(coupon_cents)} com cupom da loja* (ative na página)")
    if last_units:
        lines.append(
            "⏳ *Última unidade!*" if last_units == 1 else f"⏳ *Últimas {last_units} unidades!*"
        )
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


class UnitsError(ValueError):
    """Quantidade de últimas unidades que não confere."""


def parse_last_units(text: str) -> int | None:
    """'4' → 4; vazio → None. Só de 1 a 20: acima disso não é "últimas unidades"."""
    raw = text.strip()
    if not raw:
        return None
    if not raw.isdigit() or not 1 <= int(raw) <= MAX_LAST_UNITS:
        raise UnitsError(f"últimas unidades: um número de 1 a {MAX_LAST_UNITS}")
    return int(raw)


def units_to_show(units: int | None, units_at: datetime | None, checked_at: datetime) -> int | None:
    """As últimas unidades informadas valem por 2 horas; depois o post sai sem a linha."""
    if units is None or units_at is None or checked_at - units_at > LAST_UNITS_VALID_FOR:
        return None
    return units


def check_pix(pix_cents: int, card_cents: int) -> None:
    check_special_price(pix_cents, card_cents, "do Pix")


def check_coupon(coupon_cents: int, card_cents: int) -> None:
    check_special_price(coupon_cents, card_cents, "com cupom")


def share_url(message: str) -> str:
    """Abre o WhatsApp do celular com o texto pronto; o operador escolhe o grupo."""
    return "https://wa.me/?text=" + quote(message, safe="")


# --------------------------------------------------------------------------- link avulso

# O preço do link avulso é o que o operador digitou (a API do app não lê anúncio fora do
# catálogo): vale 6 horas, ou até a oferta relâmpago acabar. Relâmpago que acaba em menos de 10
# minutos não sai (o lead clicaria numa oferta que já acabou).
MANUAL_VALID_FOR = timedelta(hours=6)
FLASH_MIN_LEFT = timedelta(minutes=10)
FLASH_MAX = timedelta(hours=24)
ML_PAGE_HOSTS = frozenset(
    {"www.mercadolivre.com.br", "mercadolivre.com.br", "produto.mercadolivre.com.br"}
)
_CATALOG_ID = re.compile(r"/p/(MLB\d{5,15})(?=[/?#]|$)", re.IGNORECASE)
_USER_PRODUCT_ID = re.compile(r"/up/(MLBU\d{5,15})(?=[/?#]|$)", re.IGNORECASE)
_ITEM_ID = re.compile(r"(?<![A-Za-z0-9])MLB-?(\d{6,15})(?![0-9])", re.IGNORECASE)
_ITEM_PARAM = re.compile(r"(?:^|[?&])(?:wid|item_id)=(MLB\d{6,15})", re.IGNORECASE)
_PDP_ITEM = re.compile(r"item_id(?::|%3A)(MLB\d{6,15})", re.IGNORECASE)
_FLASH_LEFT = re.compile(r"^(\d{1,2}):([0-5]\d)(?::([0-5]\d))?$")


@dataclass(frozen=True, slots=True)
class ManualLink:
    external_id: str  # o código do produto ou do anúncio (repetição em 7 dias)
    item_id: str | None  # o anúncio, quando o link diz qual
    target_url: str  # a página, sem rastreio, para o gerador de links do afiliado


def parse_manual_link(url: str) -> ManualLink:
    """Link de página do ML colado pelo operador: produto (/p/MLB…), produto do vendedor
    (/up/MLBU…) ou anúncio (MLB-123…). Só páginas do ML (nada de encurtado: o link de cada grupo
    é gerado com a etiqueta dele)."""
    raw = url.strip()
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    if host == "meli.la" or parts.path.startswith("/sec/"):
        raise LinkError(
            "cole o link da página do anúncio, não o encurtado: o link de cada grupo o sistema "
            "gera com a etiqueta do grupo"
        )
    if parts.scheme != "https" or host not in ML_PAGE_HOSTS or len(raw) > 2000:
        raise LinkError("cole o link da página do anúncio no Mercado Livre (mercadolivre.com.br)")
    query_item = _ITEM_PARAM.search("?" + parts.query) or _PDP_ITEM.search(parts.query)
    item = query_item.group(1).upper() if query_item else None
    page = f"https://{host}{parts.path}"
    if catalog := _CATALOG_ID.search(parts.path):
        external = catalog.group(1).upper()
        return ManualLink(external, item, offer_page_url(page, external, item))
    if user_product := _USER_PRODUCT_ID.search(parts.path):
        external = user_product.group(1).upper()
        return ManualLink(external, item, offer_page_url(page, external, item))
    if listing := _ITEM_ID.search(parts.path):
        item_id = f"MLB{listing.group(1)}"
        return ManualLink(item_id, item_id, page)
    raise LinkError("não achei o código do anúncio (MLB…) no link")


class FlashError(ValueError):
    """Tempo da oferta relâmpago que não confere (mensagem pronta para o operador)."""


def parse_flash_left(text: str) -> timedelta:
    """O relógio "Encerra em 07:10:38" da página (horas:minutos, segundos opcionais)."""
    match = _FLASH_LEFT.match(text.strip())
    if match is None:
        raise FlashError("o tempo da oferta relâmpago é o relógio da página, ex.: 07:10:38")
    hours, minutes, seconds = (int(g or 0) for g in match.groups())
    left = timedelta(hours=hours, minutes=minutes, seconds=seconds)
    if left <= FLASH_MIN_LEFT:
        raise FlashError("a oferta relâmpago acaba em menos de 10 minutos: não dá tempo de postar")
    if left > FLASH_MAX:
        raise FlashError("oferta relâmpago dura no máximo 24 horas: confira o relógio da página")
    return left


def flash_to_show(flash_ends_at: datetime | None, at: datetime) -> datetime | None:
    """A linha da relâmpago só vai enquanto faltam mais de 10 minutos para ela acabar. Post do
    robô que sai depois disso vai sem a linha (o preço foi relido, a oferta é que não é mais
    relâmpago); o avulso nem sai (`manual_block_reason`)."""
    if flash_ends_at is None or at > flash_ends_at - FLASH_MIN_LEFT:
        return None
    return flash_ends_at


def is_urgent(
    flash_ends_at: datetime | None,
    last_units: int | None,
    last_units_at: datetime | None,
    now: datetime,
) -> bool:
    """Marcado pelo operador e ainda valendo: relâmpago que não acaba nos próximos 10 minutos, ou
    últimas unidades vistas há menos de 2 horas (depois disso o post sai sem a linha e não há
    pressa). Passa na frente da fila do grupo (`ready_order`)."""
    if flash_ends_at is not None and flash_ends_at - FLASH_MIN_LEFT > now:
        return True
    return (
        last_units is not None
        and last_units_at is not None
        and now - last_units_at < LAST_UNITS_VALID_FOR
    )


def manual_block_reason(
    created_at: datetime, flash_ends_at: datetime | None, now: datetime
) -> str | None:
    """Por que o post avulso não sai mais (o preço digitado venceu), ou None."""
    if flash_ends_at is not None:
        if now > flash_ends_at - FLASH_MIN_LEFT:
            return "a oferta relâmpago acabou (ou acaba em menos de 10 min)"
        return None
    if now - created_at > MANUAL_VALID_FOR:
        return "preço digitado há mais de 6 h: cole o link de novo se ainda valer"
    return None
