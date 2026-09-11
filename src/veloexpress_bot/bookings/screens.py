"""What the admin's single card is currently showing, and how much of it is live.

The card is one Telegram message edited all week, so two things have to be told
apart: the admin being registered to receive it, and the screen they happen to
have open on it. Background work redraws the first only when the second is a
live view of the day. Anything else — planning, settings, history, a
confirmation — is left alone until the admin navigates away themselves.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date

# Screens built from live booking data. A vote, a payment or a cancellation
# changes what they say, so the background refresh redraws them in place.
LIVE_SCREENS = frozenset({"day", "riders", "lift"})

DEFAULT_SCREEN = "day"


@dataclass(frozen=True)
class AdminScreen:
    """One screen plus the context needed to redraw it or to go back."""

    name: str = DEFAULT_SCREEN
    service_date: date | None = None
    lift_time: str | None = None
    period: str | None = None
    page: int = 0
    rider_id: int | None = None
    # The history page an admin was on, so "older" pages survive a detour.
    before: date | None = None

    @property
    def live(self) -> bool:
        return self.name in LIVE_SCREENS

    def to_state(self) -> str | None:
        """Everything except the name and the day, which have columns of their own."""
        payload = {
            key: value
            for key, value in (
                ("lift_time", self.lift_time),
                ("period", self.period),
                ("page", self.page or None),
                ("rider_id", self.rider_id),
                ("before", self.before.isoformat() if self.before else None),
            )
            if value is not None
        }
        return json.dumps(payload, separators=(",", ":")) if payload else None

    def on_day(self, service_date: date | None) -> AdminScreen:
        return replace(self, service_date=service_date)


def decode_screen(
    name: str | None,
    state: str | None,
    *,
    service_date: date | None = None,
) -> AdminScreen:
    """Read a stored screen back, tolerating anything a downgrade left behind."""
    payload: dict[str, object] = {}
    if state:
        try:
            loaded = json.loads(state)
        except ValueError:
            loaded = None
        if isinstance(loaded, dict):
            payload = loaded
    return AdminScreen(
        name=name or DEFAULT_SCREEN,
        service_date=service_date,
        lift_time=_text(payload.get("lift_time")),
        period=_text(payload.get("period")),
        page=_page(payload.get("page")),
        rider_id=_rider_id(payload.get("rider_id")),
        before=_date(payload.get("before")),
    )


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _page(value: object) -> int:
    return value if isinstance(value, int) and value > 0 else 0


def _rider_id(value: object) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
