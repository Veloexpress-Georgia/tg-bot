from datetime import date

from veloexpress_bot.bot.handlers import (
    _next_saturday,
    _upcoming_weekend_dates,
)


def test_next_saturday_returns_upcoming_saturday() -> None:
    assert _next_saturday(date(2026, 5, 15)) == date(2026, 5, 16)
    assert _next_saturday(date(2026, 5, 16)) == date(2026, 5, 16)
    assert _next_saturday(date(2026, 5, 17)) == date(2026, 5, 23)


def test_upcoming_weekend_dates_returns_saturday_and_sunday() -> None:
    assert _upcoming_weekend_dates(date(2026, 5, 15)) == (
        date(2026, 5, 16),
        date(2026, 5, 17),
    )
