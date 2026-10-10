from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.domain.price_history import PricePoint, compute_stats, daily_minimums

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def at(days_ago: float, price: int) -> PricePoint:
    return PricePoint(NOW - timedelta(days=days_ago), price)


def test_daily_minimums_step_function() -> None:
    d0 = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    points = [
        PricePoint(d0, 1000),
        PricePoint(d0 + timedelta(hours=5), 900),  # mesmo dia: o mínimo do dia é 900
        PricePoint(d0 + timedelta(days=2), 1100),
    ]
    mins = daily_minimums(points, date(2026, 10, 30), date(2026, 11, 4))
    assert mins == {
        date(2026, 11, 1): 900,
        date(2026, 11, 2): 900,  # 900 vale até a próxima observação
        date(2026, 11, 3): 900,  # a de 1100 entra às 10h do dia 3; o dia começou em 900
        date(2026, 11, 4): 1100,
    }


def test_cold_start_has_no_reference() -> None:
    stats = compute_stats([at(5, 1000), at(1, 800)], NOW)
    assert stats.reference_cents is None
    assert stats.real_discount(800) is None
    assert not stats.is_lowest_90d(800)


def test_reference_is_median_excluding_last_48h() -> None:
    # 20 dias a 1000, depois queda para 700 há 1 dia: a queda não contamina a referência
    stats = compute_stats([at(25, 1000), at(1, 700)], NOW)
    assert stats.reference_cents == 1000
    assert stats.coverage_days >= 10
    discount = stats.real_discount(700)
    assert discount is not None and round(discount, 2) == 0.30
    assert stats.is_lowest_90d(700)


def test_inflate_then_drop_is_flagged() -> None:
    # 40 dias a 1000, sobe para 1500 há 10 dias, cai para 1000 agora: "desconto" falso
    stats = compute_stats([at(50, 1000), at(10, 1500), at(0.5, 1000)], NOW)
    assert stats.inflated_before_drop()


def test_stable_price_is_not_inflated() -> None:
    stats = compute_stats([at(50, 1000), at(0.5, 900)], NOW)
    assert not stats.inflated_before_drop()
