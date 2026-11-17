"""Small shared text helpers so counts, money and dates read the same everywhere."""

from __future__ import annotations

from datetime import date

CURRENCY = "₾"
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """`1 seat`, `2 seats`; pass the plural form when adding "s" is wrong."""
    word = singular if abs(count) == 1 else (plural_form or f"{singular}s")
    return f"{count} {word}"


def money(amount: int) -> str:
    """`20 ₾`. One currency style for every bot text."""
    return f"{amount} {CURRENCY}"


def day_label(service_date: date) -> str:
    """`Sat, 21 Nov`."""
    return (
        f"{_WEEKDAYS[service_date.weekday()]}, {service_date.day} {_MONTHS[service_date.month - 1]}"
    )
