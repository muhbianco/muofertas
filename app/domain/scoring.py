"""Nota de uma oferta (§4.6 do levantamento). Puro, determinístico e explicável.

Ordem: portões (bloqueiam) → sinais de suspeita (mandam para revisão) → componentes de 0 a 100 →
nota ponderada → nível. Tudo que entrou na conta volta em `Evaluation.as_snapshot()`, para a
decisão ser auditável depois ("por que esta foi Excepcional?").

LLM não participa da nota.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from typing import Any

from app.domain.catalog_offers import CatalogSnapshot
from app.domain.price_history import PriceStats

RULESET_VERSION = "v1-2026-10-10"

WEIGHTS = {"F": 0.35, "D": 0.20, "Q": 0.15, "U": 0.10, "R": 0.20}

# Título que anuncia cópia: bloqueio (portão G8). Comparação sem acento e sem caixa.
FORBIDDEN_TITLE = re.compile(
    r"\b(replica|inspired|inspirado|primeira linha|1a linha|aaa|tailandesa|contratipo|similar ao)\b"
)
STALE_AFTER = timedelta(hours=6)


class Tier(StrEnum):
    EXCEPCIONAL = "excepcional"
    EXCELENTE = "excelente"
    BOA = "boa"
    REGULAR = "regular"
    SUSPEITA = "suspeita"
    BLOQUEADA = "bloqueada"  # falhou num portão: não publica nem vai para revisão


@dataclass(frozen=True, slots=True)
class ScoringInput:
    title: str
    brand: str | None
    commission_rate: float
    require_official: bool
    high_risk_brand: bool
    snapshot: CatalogSnapshot | None
    stats: PriceStats
    observed_at: datetime | None
    last_changed_at: datetime | None
    highlight_position: int | None
    niche_commission_p90_cents: int | None
    now: datetime


@dataclass(frozen=True, slots=True)
class Evaluation:
    tier: Tier
    score: float
    components: dict[str, float]
    gates_failed: list[str]
    signals: list[str]
    price_cents: int | None
    used_official: bool
    real_discount: float | None
    reference_cents: int | None
    ruleset: str = RULESET_VERSION
    notes: list[str] = field(default_factory=list)

    def as_snapshot(self) -> dict[str, Any]:
        data = asdict(self)
        data["tier"] = self.tier.value
        return data


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def _interpolate(x: float, points: list[tuple[float, float]]) -> float:
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in pairwise(points):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def evaluate(inp: ScoringInput) -> Evaluation:
    gates: list[str] = []
    signals: list[str] = []
    notes: list[str] = []
    snap = inp.snapshot

    # --- portões ------------------------------------------------------------------------
    if snap is None:
        gates.append("G2_sem_oferta")
    if inp.observed_at is None or inp.now - inp.observed_at > STALE_AFTER:
        gates.append("G1_preco_velho")
    if FORBIDDEN_TITLE.search(_plain(inp.title)):
        gates.append("G8_titulo_de_copia")

    must_be_official = inp.require_official or inp.high_risk_brand
    price: int | None = None
    used_official = False
    if snap is not None:
        if snap.min_official_price_cents is not None and (
            must_be_official or snap.min_official_price_cents <= snap.min_price_cents * 1.02
        ):
            # Loja oficial quando é obrigatório, ou quando custa praticamente o mesmo.
            price, used_official = snap.min_official_price_cents, True
        elif must_be_official:
            gates.append("G7_sem_loja_oficial")
        else:
            price = snap.min_price_cents
            notes.append("menor preço de vendedor não oficial")

    if gates:
        return Evaluation(
            Tier.BLOQUEADA,
            0.0,
            {},
            gates,
            signals,
            price,
            used_official,
            None,
            inp.stats.reference_cents,
            notes=notes,
        )
    assert price is not None and snap is not None

    # --- sinais de suspeita ------------------------------------------------------------
    discount = inp.stats.real_discount(price)
    if inp.stats.inflated_before_drop():
        signals.append("S1_subiu_para_descer")
    if discount is not None and discount > 0.70 and inp.brand:
        signals.append("S2_desconto_alto_demais")
    if (
        not used_official
        and snap.min_official_price_cents is not None
        and price < snap.min_official_price_cents * 0.6
    ):
        signals.append("S4_muito_abaixo_da_loja_oficial")

    # --- componentes ---------------------------------------------------------------------
    f = 0.0
    if discount is not None:
        f = _interpolate(
            discount, [(0.0, 0.0), (0.10, 35.0), (0.20, 65.0), (0.30, 85.0), (0.40, 100.0)]
        )
        if inp.stats.is_lowest_90d(price):
            f = min(100.0, f + 10.0)
    else:
        notes.append("sem histórico suficiente: desconto não comprovado")

    d = (
        30.0
        if inp.highlight_position is None
        else max(0.0, 100.0 - (inp.highlight_position - 1) * 4.0)
    )

    q = 50.0 + (25.0 if used_official else 0.0) + (15.0 if inp.stats.has_reference else 0.0)
    if inp.observed_at is not None and inp.now - inp.observed_at <= timedelta(hours=1):
        q += 10.0
    q = min(q, 100.0)

    if inp.last_changed_at is None:
        u = 30.0
    else:
        age = inp.now - inp.last_changed_at
        u = 100.0 if age <= timedelta(hours=6) else 70.0 if age <= timedelta(hours=24) else 30.0

    expected = price * inp.commission_rate
    r = (
        50.0
        if not inp.niche_commission_p90_cents
        else min(100.0, 100.0 * expected / inp.niche_commission_p90_cents)
    )

    components = {
        "F": round(f, 1),
        "D": round(d, 1),
        "Q": round(q, 1),
        "U": round(u, 1),
        "R": round(r, 1),
    }
    score = round(sum(WEIGHTS[k] * v for k, v in components.items()), 1)

    # --- nível ---------------------------------------------------------------------------
    if signals:
        tier = Tier.SUSPEITA
    elif score >= 85 and f >= 70 and q >= 70 and discount is not None:
        tier = Tier.EXCEPCIONAL
    elif score >= 75 and discount is not None:
        tier = Tier.EXCELENTE
    elif score >= 60:
        tier = Tier.BOA
    else:
        tier = Tier.REGULAR
    return Evaluation(
        tier,
        score,
        components,
        gates,
        signals,
        price,
        used_official,
        round(discount, 4) if discount is not None else None,
        inp.stats.reference_cents,
        notes=notes,
    )
