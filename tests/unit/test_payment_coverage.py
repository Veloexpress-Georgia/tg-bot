import pytest

from veloexpress_bot.payments.coverage import CoverageTarget, reconcile_coverage


def test_coverage_moves_when_the_target_lift_changes() -> None:
    before = reconcile_coverage(
        received_gel=15,
        price_gel=15,
        targets=(CoverageTarget("8:30", 1),),
    )
    after = reconcile_coverage(
        received_gel=15,
        price_gel=15,
        targets=(CoverageTarget("10:00", 1),),
    )

    assert before.covered_on("8:30") == 1
    assert after.covered_on("10:00") == 1
    assert after.due_gel == 0


def test_an_added_seat_becomes_due_without_changing_received_money() -> None:
    coverage = reconcile_coverage(
        received_gel=15,
        price_gel=15,
        targets=(CoverageTarget("8:30", 1), CoverageTarget("10:00", 1)),
    )

    assert coverage.received_gel == 15
    assert coverage.covered_on("8:30") == 1
    assert coverage.covered_on("10:00") == 0
    assert coverage.due_gel == 15


def test_removed_seat_becomes_credit() -> None:
    coverage = reconcile_coverage(
        received_gel=30,
        price_gel=15,
        targets=(CoverageTarget("10:00", 1),),
    )

    assert coverage.credit_gel == 15


def test_invalid_price_is_rejected() -> None:
    with pytest.raises(ValueError, match="price_gel"):
        reconcile_coverage(received_gel=15, price_gel=0, targets=())
