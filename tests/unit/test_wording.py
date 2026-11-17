from datetime import date

from veloexpress_bot.payments.render import UnpaidLift, render_unpaid_deadline_notice
from veloexpress_bot.polls.seatoffer import DECLINED_TEXT, accepted_text, render_seat_offer
from veloexpress_bot.wording import day_label, money, plural


def test_counts_use_the_singular_for_one() -> None:
    assert plural(1, "day") == "1 day"
    assert plural(0, "day") == "0 days"
    assert plural(3, "lift") == "3 lifts"
    assert plural(1, "day was", "days were") == "1 day was"
    assert plural(2, "day was", "days were") == "2 days were"


def test_money_and_dates_have_one_format() -> None:
    assert money(20) == "20 ₾"
    assert day_label(date(2026, 11, 21)) == "Sat, 21 Nov"


def test_the_seat_offer_asks_and_says_taking_it_means_paying() -> None:
    offer = render_seat_offer(date(2026, 11, 21), "10:00", seats=1, riders=[(1, "@a"), (2, "@b")])
    assert offer.splitlines() == [
        '🎟 Free seat on 10:00 · Sat, 21 Nov — <a href="tg://user?id=1">@a</a> '
        '<a href="tg://user?id=2">@b</a>, do you want it?',
        "Tap ✅ I'm in to take it and pay as usual, or 🙅 Skip.",
        "First tap gets the seat.",
    ]
    single = render_seat_offer(date(2026, 11, 21), "10:00", seats=2, riders=[(1, "@a")])
    assert single.splitlines()[0].startswith("🎟 2 free seats on 10:00")
    assert "First" not in single


def test_offer_toasts_fit_telegram_and_explain_what_happens() -> None:
    accepted = accepted_text(20)
    assert "20 ₾" in accepted and "💸 I paid" in accepted
    # Telegram caps a callback answer at 200 characters.
    assert len(accepted) <= 200
    assert len(DECLINED_TEXT) <= 200
    assert "end of the waitlist" in DECLINED_TEXT


def test_the_deadline_notice_never_says_booking_is_closed() -> None:
    text = render_unpaid_deadline_notice(
        service_date=date(2026, 11, 21), lifts=(UnpaidLift("10:00", 3),), riders=(), minimum=5
    )
    assert "closed" not in text
    assert "10:00 — 3/5 paid" in text
    assert "You can still book while seats are free." in text
