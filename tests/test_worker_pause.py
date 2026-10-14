from __future__ import annotations

from datetime import datetime, timedelta

from app.worker import RATE_LIMIT_MAX_PAUSE_SECONDS, MlPause

NOW = datetime(2026, 10, 11, 11, 7, 23)


def test_rate_limit_starts_short_and_doubles_up_to_the_cap() -> None:
    pause = MlPause()
    assert pause.rate_limited(NOW) == 120
    assert pause.active(NOW + timedelta(seconds=119))
    assert not pause.active(NOW + timedelta(seconds=120))
    cap = RATE_LIMIT_MAX_PAUSE_SECONDS
    assert [pause.rate_limited(NOW) for _ in range(4)] == [240, 480, cap, cap]


def test_a_good_round_resets_the_streak() -> None:
    pause = MlPause()
    pause.rate_limited(NOW)
    pause.rate_limited(NOW)
    pause.clear()
    assert not pause.active(NOW)
    assert pause.rate_limited(NOW) == 120


def test_hourly_budget_waits_for_the_next_hour() -> None:
    pause = MlPause()
    seconds = pause.budget_exhausted(NOW)
    assert NOW + timedelta(seconds=seconds) == datetime(2026, 10, 11, 12, 0, 5)
    assert pause.active(datetime(2026, 10, 11, 11, 59, 59))
    assert not pause.active(datetime(2026, 10, 11, 12, 0, 5))


def test_unstable_ml_pauses_ten_minutes() -> None:
    pause = MlPause()
    assert pause.unstable(NOW) == 600
    assert pause.active(NOW + timedelta(minutes=9))
    assert not pause.active(NOW + timedelta(minutes=10))
