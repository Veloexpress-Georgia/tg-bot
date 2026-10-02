from dataclasses import dataclass
from enum import StrEnum

from veloexpress_bot.payments.copy import CASH_BUTTON, CASH_ON_SITE_TEXT, CASH_REPORT_TEXT


class StartLocation(StrEnum):
    JUSTICE_HALL = "justice_hall"
    VAKE = "vake"


# A lift only runs once this many riders have booked; below it the slot is
# still "at risk" and shown as needing more people.
MINIMUM_RIDERS = 5


@dataclass(frozen=True)
class LiftTemplate:
    time: str
    capacity: int = 10


DEFAULT_LIFTS: tuple[LiftTemplate, ...] = (
    LiftTemplate("8:30"),
    LiftTemplate("10:00"),
    LiftTemplate("11:45"),
    LiftTemplate("13:30"),
    LiftTemplate("15:30"),
)

# Lifts switched off when a new setup opens; admins can re-enable them.
EXTRA_LIFT_TIME = "15:30"
DEFAULT_CANCELLED_LIFT_TIMES: tuple[str, ...] = (EXTRA_LIFT_TIME,)

CHECK_ANSWERS_OPTION = "👀 Check answers"


@dataclass(frozen=True)
class PaymentTerms:
    """The money rules, in one place so every message states them the same way."""

    price_gel: int
    deadline_time: str
    link: str = ""

    def rules(self) -> tuple[str, ...]:
        lines = (
            f"💳 {self.price_gel} GEL per seat · 10 seats per lift.",
            f"A lift runs from {MINIMUM_RIDERS} booked seats. Transfer for each booked seat "
            f"once its lift reaches {MINIMUM_RIDERS}.",
            f"Bank transfer by {self.deadline_time} the evening before; "
            "if you book later, transfer immediately.",
            "Be at the meeting point 10 minutes before departure. Message the chat if late.",
            "If leaving, remove your vote and post the day and time in the chat early.",
            "A paid seat is not refunded if you miss your lift; find a replacement if possible.",
            f"💵 {CASH_ON_SITE_TEXT} {CASH_REPORT_TEXT} Tap {CASH_BUTTON} after paying him.",
        )
        return (*lines, self._link_line("Where to pay")) if self.link else lines

    def pay_now(self) -> tuple[str, ...]:
        lines = (
            f"💳 Bank transfer: {self.price_gel} GEL per seat, "
            f"by {self.deadline_time} the day before the lift.",
            f"💵 {CASH_ON_SITE_TEXT}",
        )
        return (*lines, self._link_line("Pay here")) if self.link else lines

    def pay_link(self, label: str) -> str:
        return f'<a href="{self.link}">{label}</a>'

    def _link_line(self, label: str) -> str:
        # An anchor, not a bare URL: Telegram renders the raw link as noisy text.
        return f'🔗 <a href="{self.link}">{label}</a>'


# Every message carrying PaymentTerms must be sent as HTML for the link to render.
PAYMENT_TERMS_PARSE_MODE = "HTML"
