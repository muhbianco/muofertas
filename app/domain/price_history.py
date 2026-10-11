"""Estatística da série de preço de um produto. Puro, sem I/O.

A série é gravada só quando o preço muda (mais um batimento diário), então ela é uma função
degrau: o preço vale do instante da observação até a próxima. Daqui saem o mínimo de cada dia,
o preço de referência e os sinais de "metade do dobro" (§4.4 do levantamento).
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

REFERENCE_WINDOW_DAYS = 30
REFERENCE_EXCLUDE_HOURS = 48
MIN_COVERAGE_DAYS = 10
INFLATION_LOOKBACK_DAYS = 14
INFLATION_BASELINE_DAYS = 60
INFLATION_RATIO = 1.15


@dataclass(frozen=True, slots=True)
class PricePoint:
    at: datetime  # aware, UTC
    price_cents: int


def daily_minimums(points: Sequence[PricePoint], start: date, end: date) -> dict[date, int]:
    """Menor preço vigente em cada dia de [start, end], só nos dias em que a série já existia.

    O preço de uma observação vale até a próxima; o dia de hoje conta até o último ponto.
    """
    ordered = sorted(points, key=lambda p: p.at)
    if not ordered:
        return {}
    result: dict[date, int] = {}
    for i, point in enumerate(ordered):
        valid_until = ordered[i + 1].at if i + 1 < len(ordered) else None
        first_day = max(point.at.date(), start)
        last_day = (valid_until - timedelta(microseconds=1)).date() if valid_until else end
        last_day = min(last_day, end)
        day = first_day
        while day <= last_day:
            current = result.get(day)
            if current is None or point.price_cents < current:
                result[day] = point.price_cents
            day += timedelta(days=1)
    return result


@dataclass(frozen=True, slots=True)
class PriceStats:
    reference_cents: int | None  # mediana dos mínimos diários (30 d, sem as últimas 48 h)
    coverage_days: int  # dias com leitura dentro da janela de referência
    min_90d_cents: int | None
    median_60d_cents: int | None
    max_before_drop_cents: int | None  # máximo dos 14 dias que antecedem as últimas 48 h
    first_seen: datetime | None

    @property
    def has_reference(self) -> bool:
        return self.reference_cents is not None

    def real_discount(self, price_cents: int) -> float | None:
        """1 - preço/referência. None sem referência confiável (partida a frio)."""
        if not self.reference_cents:
            return None
        return 1 - price_cents / self.reference_cents

    def inflated_before_drop(self) -> bool:
        """Sinal S1, "subiu para descer": preço inflado logo antes da queda."""
        if self.max_before_drop_cents is None or self.median_60d_cents is None:
            return False
        return self.max_before_drop_cents > self.median_60d_cents * INFLATION_RATIO

    def is_lowest_90d(self, price_cents: int) -> bool:
        return (
            self.min_90d_cents is not None
            and self.coverage_days >= MIN_COVERAGE_DAYS
            and (price_cents <= self.min_90d_cents)
        )


def compute_stats(
    points: Sequence[PricePoint], now: datetime, *, reference_until: date | None = None
) -> PriceStats:
    """`reference_until`: referência congelada (evento com esquenta, como a Black Friday).

    A janela de 30 dias da referência termina nesse dia em vez de andar com o relógio, para o
    preço que sobe no esquenta não virar a base do desconto do dia.
    """
    if not points:
        return PriceStats(None, 0, None, None, None, None)
    today = now.date()
    cutoff = (now - timedelta(hours=REFERENCE_EXCLUDE_HOURS)).date()
    ref_start = today - timedelta(days=REFERENCE_WINDOW_DAYS)
    ref_end = cutoff
    if reference_until is not None and reference_until < cutoff:
        ref_start = reference_until - timedelta(days=REFERENCE_WINDOW_DAYS)
        ref_end = reference_until + timedelta(days=1)
    mins_90 = daily_minimums(points, today - timedelta(days=90), today)

    ref_days = [v for d, v in mins_90.items() if ref_start <= d < ref_end]
    reference = int(statistics.median(ref_days)) if len(ref_days) >= MIN_COVERAGE_DAYS else None

    baseline_start = today - timedelta(days=INFLATION_BASELINE_DAYS)
    baseline = [v for d, v in mins_90.items() if baseline_start <= d < cutoff]
    lookback_start = cutoff - timedelta(days=INFLATION_LOOKBACK_DAYS)
    lookback = [v for d, v in mins_90.items() if lookback_start <= d < cutoff]

    return PriceStats(
        reference_cents=reference,
        coverage_days=len(ref_days),
        min_90d_cents=min(mins_90.values()) if mins_90 else None,
        median_60d_cents=int(statistics.median(baseline))
        if len(baseline) >= MIN_COVERAGE_DAYS
        else None,
        max_before_drop_cents=max(lookback) if lookback else None,
        first_seen=min(p.at for p in points),
    )
