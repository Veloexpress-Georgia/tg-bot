from datetime import date

from veloexpress_bot.payments.render import (
    OutstandingRider,
    PaymentsBoardView,
    RefundRow,
    RiderPayment,
    decode_board_date,
    encode_board_date,
    render_cancellation_report,
    render_payment_post,
    render_payments_board,
)

SATURDAY = date(2026, 7, 18)


def _view(**overrides) -> PaymentsBoardView:  # type: ignore[no-untyped-def]
    defaults = {
        "service_date": SATURDAY,
        "running_lift_times": ("8:30", "10:00"),
        "price_gel": 15,
        "payments": (),
        "outstanding": (),
    }
    return PaymentsBoardView(**{**defaults, **overrides})


def _owing(count: int) -> tuple[OutstandingRider, ...]:
    return tuple(
        OutstandingRider(telegram_user_id=index, label=f"@rider{index}") for index in range(count)
    )


def _buttons(markup) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {
        button.callback_data: button.text
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data is not None
    }


def test_board_lists_running_lifts_price_and_the_taps() -> None:
    draft = render_payments_board(
        _view(outstanding=_owing(6), guests_url="https://t.me/bot?start=guests-20260718")
    )

    assert "💸 Payments · Sat, 18 Jul" in draft.text
    assert "Payment open: 8:30, 10:00" in draft.text
    assert "15 GEL per seat · pay by 20:00" in draft.text
    assert "💸 transfer · 💵 cash · 👤 Guests opens a form in the bot" in draft.text
    assert "Waiting on:" in draft.text
    assert draft.reply_markup is not None
    # Cash has its own button rather than a rule telling riders to press the
    # transfer one anyway. No "one fewer seat": underpaying while still holding the
    # seat is the phantom booking the group keeps tripping over.
    assert _buttons(draft.reply_markup) == {
        "pay:paid:20260718": "💸 I paid",
        "pay:cash:20260718": "💵 Cash",
        "pay:undo:20260718": "↩️ Undo",
    }
    # Guests is a url button, not a callback: the tap must open the private chat,
    # which is also a Start for riders who never opened the bot.
    guest_button = next(
        button
        for row in draft.reply_markup.inline_keyboard
        for button in row
        if button.text == "👤 Guests"
    )
    assert guest_button.url == "https://t.me/bot?start=guests-20260718"
    assert guest_button.callback_data is None


def test_board_lists_payments_and_counts_the_rest() -> None:
    draft = render_payments_board(
        _view(
            outstanding=_owing(2),
            payments=(
                RiderPayment(label="@stas", seats=2, amount_gel=30),
                RiderPayment(label="Anna", seats=1, amount_gel=15),
            ),
        )
    )

    assert "✓ @stas — 30 GEL · 2 seats" in draft.text
    assert "✓ Anna — 15 GEL" in draft.text
    assert "Waiting on:" in draft.text


def test_the_board_shows_what_is_still_due_after_a_re_vote() -> None:
    """Paid and owed are two numbers. Re-voting moves the bill, not the payment."""
    draft = render_payments_board(
        _view(
            payments=(
                RiderPayment(label="@whekin", seats=2, amount_gel=30, due_gel=60),
                RiderPayment(label="@anna", seats=2, amount_gel=30, due_gel=15),
                RiderPayment(label="@stas", seats=1, amount_gel=15, due_gel=15),
            ),
        )
    )

    assert "✓ @whekin — 30 GEL · 2 seats · +30 due" in draft.text
    assert "✓ @anna — 30 GEL · 2 seats · 15 back" in draft.text
    # Settled riders stay a plain line; no arithmetic to read where none is owed.
    assert "✓ @stas — 15 GEL" in draft.text


def test_cash_is_named_on_the_board_and_transfers_are_not() -> None:
    """Cash is the line Misho will not find in his bank, so it is worth naming."""
    draft = render_payments_board(
        _view(
            payments=(
                RiderPayment(label="@stas", seats=1, amount_gel=15, due_gel=15, cash=True),
                RiderPayment(label="Anna", seats=1, amount_gel=15, due_gel=15),
            ),
        )
    )

    assert "✓ @stas — 15 GEL · cash" in draft.text
    assert "✓ Anna — 15 GEL" in draft.text


def test_a_cash_post_is_marked_so_misho_does_not_hunt_for_a_transfer() -> None:
    text = render_payment_post(
        label="@konstantin",
        service_date=SATURDAY,
        amount_gel=15,
        user_id=12,
        cash=True,
    )

    assert text.startswith("💵 ")
    assert text.endswith("· cash")


def test_board_tags_who_still_owes() -> None:
    draft = render_payments_board(
        _view(
            outstanding=(
                OutstandingRider(telegram_user_id=11, label="@anna"),
                OutstandingRider(telegram_user_id=12, label="Ivan"),
            ),
            payments=(RiderPayment(label="@stas", seats=1, amount_gel=15),),
        )
    )

    # Tags, not a count. The board is edited in place and a Telegram edit sends no
    # notification, so naming names here shows the gap without nagging anyone.
    assert "Waiting on:" in draft.text
    assert 'tg://user?id=11">@anna</a>' in draft.text
    assert 'tg://user?id=12">Ivan</a>' in draft.text


def test_board_has_no_taps_before_a_lift_reaches_the_minimum() -> None:
    draft = render_payments_board(_view(running_lift_times=()))

    assert "No lift has reached the minimum yet." in draft.text
    assert draft.reply_markup is None


def test_a_cancelled_day_closes_the_board() -> None:
    draft = render_payments_board(_view(cancelled=True))

    assert "❌ The day is cancelled." in draft.text
    assert draft.reply_markup is None


def test_payment_post_tags_the_rider_with_the_amount() -> None:
    text = render_payment_post(
        label="@stas",
        service_date=SATURDAY,
        amount_gel=30,
        user_id=10,
    )

    # No lift names: payment covers the day, and riders may re-vote until the
    # deadline, so a receipt listing lifts would go stale on the next slot change.
    assert text == '💸 <a href="tg://user?id=10">@stas</a> — 30 GEL · Sat, 18 Jul'


def test_payment_post_counts_guest_seats_not_guest_people() -> None:
    """Guests are declared per lift, so one guest riding three lifts is three
    seats. Reported as "+3 guests" it had the group looking for three visitors."""
    text = render_payment_post(
        label="Anna",
        service_date=SATURDAY,
        amount_gel=90,
        user_id=11,
        seats=6,
        guests=3,
    )

    assert text.endswith("6 seats · 3 for guests")

    single = render_payment_post(
        label="Anna",
        service_date=SATURDAY,
        amount_gel=30,
        user_id=11,
        seats=2,
        guests=1,
    )
    assert single.endswith("2 seats · 1 for guests")


def test_board_date_survives_the_callback_round_trip() -> None:
    assert decode_board_date(encode_board_date(SATURDAY)) == SATURDAY


def test_single_lift_report_shows_only_refund_amounts() -> None:
    report = render_cancellation_report(
        service_date=date(2026, 9, 5),
        cancelled_lift_time="15:30",
        rows=(
            RefundRow("@staying", 1, 2, 30, ("10:00", "11:45")),
            RefundRow("@partial", 2, 2, 30, ("10:00",), refund_gel=15),
            RefundRow("@leaving", 3, 1, 15, (), refund_gel=15),
        ),
    )
    assert report == (
        "💸 15:30 · Sat, 5 Sep cancelled\n\n"
        '<a href="tg://user?id=2">@partial</a> — 15 GEL back\n'
        "Paid 30 · rides 15 GEL\n\n"
        '<a href="tg://user?id=3">@leaving</a> — 15 GEL back\n'
        "Paid 15 · rides 0 GEL\n\n"
        "Total to return: 30 GEL\n"
        "Cumulative for the day · payouts not tracked."
    )


def test_single_lift_without_refunds_omits_day_payments() -> None:
    report = render_cancellation_report(
        service_date=SATURDAY,
        cancelled_lift_time="15:30",
        rows=(RefundRow("@staying", 1, 2, 30, ("10:00", "11:45")),),
    )
    assert report is not None
    assert report.endswith("Nothing to refund.")
    assert "@staying" not in report
    assert "30 GEL" not in report


def test_cancellation_without_payments_stays_quiet() -> None:
    assert (
        render_cancellation_report(service_date=SATURDAY, cancelled_lift_time="15:30", rows=())
        is None
    )
