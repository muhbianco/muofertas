from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.distribution import (
    UnitsError,
    parse_last_units,
    render_message,
    units_to_show,
)

NOW = datetime(2026, 10, 12, 13, 0, tzinfo=UTC)


def test_parse_last_units() -> None:
    assert parse_last_units("4") == 4
    assert parse_last_units(" ") is None
    for bad in ("0", "21", "4,5", "abc"):
        with pytest.raises(UnitsError):
            parse_last_units(bad)


def test_units_last_two_hours_only() -> None:
    assert units_to_show(4, NOW, NOW + timedelta(hours=2)) == 4
    assert units_to_show(4, NOW, NOW + timedelta(hours=2, minutes=1)) is None
    assert units_to_show(None, NOW, NOW) is None


@pytest.mark.parametrize(
    ("units", "line"), [(4, "⏳ *Últimas 4 unidades!*"), (1, "⏳ *Última unidade!*")]
)
def test_message_has_the_urgency_line(units: int, line: str) -> None:
    text = render_message(
        niche_slug="beleza",
        niche_name="Beleza",
        product_name="Tintura Beautycolor 3.0",
        price_cents=1990,
        checked_at=NOW,
        official=False,
        url="https://meli.la/x",
        last_units=units,
    )
    assert line in text
    no_units = render_message(
        niche_slug="beleza",
        niche_name="Beleza",
        product_name="Tintura Beautycolor 3.0",
        price_cents=1990,
        checked_at=NOW,
        official=False,
        url="https://meli.la/x",
    )
    assert "⏳" not in no_units
