from dataclasses import dataclass
from enum import StrEnum


class StartLocation(StrEnum):
    JUSTICE_HALL = "justice_hall"
    VAKE = "vake"


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
PAYMENT_REMINDER = "💳 Please prepay after voting."
