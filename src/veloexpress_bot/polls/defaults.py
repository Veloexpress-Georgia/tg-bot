from dataclasses import dataclass
from enum import StrEnum


class StartLocation(StrEnum):
    JUSTICE_HALL = "justice_hall"
    VAKE = "vake"


@dataclass(frozen=True)
class LocationLabel:
    ru: str
    en: str

    def format(self) -> str:
        return f"{self.ru} / {self.en}"


LOCATION_LABELS: dict[StartLocation, LocationLabel] = {
    StartLocation.JUSTICE_HALL: LocationLabel("Дом Юстиции", "Justice hall"),
    StartLocation.VAKE: LocationLabel("от Ваке парка", "Vake park"),
}


@dataclass(frozen=True)
class LiftTemplate:
    time: str
    default_location: StartLocation
    capacity: int = 10


DEFAULT_LIFTS: tuple[LiftTemplate, ...] = (
    LiftTemplate("10:00", StartLocation.JUSTICE_HALL),
    LiftTemplate("11:45", StartLocation.VAKE),
    LiftTemplate("13:30", StartLocation.VAKE),
    LiftTemplate("15:30", StartLocation.VAKE),
)

CHECK_ANSWERS_OPTION = "👀 Посмотреть ответы / Check answers"
PAYMENT_REMINDER = (
    "💳 После голосования внесите предоплату.\nPlease send the prepayment after voting."
)
