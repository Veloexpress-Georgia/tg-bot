"""Pure reconciliation of immutable day money against mutable seat bookings."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CoverageTarget:
    """Chargeable seats on one lift, in the order money should cover them."""

    lift_time: str
    seats: int


@dataclass(frozen=True)
class CoveredTarget:
    lift_time: str
    seats: int
    covered_seats: int


@dataclass(frozen=True)
class DayCoverage:
    targets: tuple[CoveredTarget, ...]
    received_gel: int
    price_gel: int

    @property
    def due_gel(self) -> int:
        charged = sum(target.seats for target in self.targets) * self.price_gel
        return max(charged - self.received_gel, 0)

    @property
    def credit_gel(self) -> int:
        charged = sum(target.seats for target in self.targets) * self.price_gel
        return max(self.received_gel - charged, 0)

    def covered_on(self, lift_time: str) -> int:
        return sum(target.covered_seats for target in self.targets if target.lift_time == lift_time)


def reconcile_coverage(
    *,
    received_gel: int,
    price_gel: int,
    targets: tuple[CoverageTarget, ...],
) -> DayCoverage:
    """Assign paid seat-units to current targets without changing the payment.

    Callers choose target order. Before the deadline this makes coverage movable:
    replacing one lift with another keeps the same number of covered seat-units.
    """
    if price_gel <= 0:
        msg = "price_gel must be greater than zero"
        raise ValueError(msg)
    remaining = max(received_gel, 0) // price_gel
    covered: list[CoveredTarget] = []
    for target in targets:
        seats = max(target.seats, 0)
        assigned = min(seats, remaining)
        covered.append(
            CoveredTarget(
                lift_time=target.lift_time,
                seats=seats,
                covered_seats=assigned,
            )
        )
        remaining -= assigned
    return DayCoverage(
        targets=tuple(covered),
        received_gel=max(received_gel, 0),
        price_gel=price_gel,
    )
