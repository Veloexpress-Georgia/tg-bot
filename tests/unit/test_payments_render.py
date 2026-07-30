from datetime import date

from veloexpress_bot.payments.render import (
    OutstandingRider,
    PaymentsBoardView,
    RiderPayment,
    decode_board_date,
    encode_board_date,
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
    return {button.callback_data: button.text for row in markup.inline_keyboard for button in row}


def test_board_lists_running_lifts_price_and_the_four_taps() -> None:
    draft = render_payments_board(_view(outstanding=_owing(6)))

    assert "💸 Payments · Sat, 18 Jul" in draft.text
    assert "Running: 8:30, 10:00" in draft.text
    assert "15 GEL per seat · pay by 20:00" in draft.text
    assert "➕/➖ for a guest or fewer laps than you booked." in draft.text
    assert "Waiting on:" in draft.text
    assert draft.reply_markup is not None
    assert _buttons(draft.reply_markup) == {
        "pay:paid:20260718": "💸 I paid",
        "pay:guest:20260718": "➕ Seat",
        "pay:fewer:20260718": "➖ Seat",
        "pay:undo:20260718": "↩️ Undo",
    }


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


def test_payment_post_tags_the_rider_with_amount_and_lifts() -> None:
    text = render_payment_post(
        label="@stas",
        service_date=SATURDAY,
        lift_times=("8:30", "10:00"),
        seats=2,
        amount_gel=30,
        user_id=10,
    )

    assert text == '💸 <a href="tg://user?id=10">@stas</a> — 30 GEL · Sat, 18 Jul · 8:30, 10:00'


def test_payment_post_reports_guest_seats_beyond_the_rider_own_lifts() -> None:
    text = render_payment_post(
        label="Anna",
        service_date=SATURDAY,
        lift_times=("8:30",),
        seats=3,
        amount_gel=45,
        user_id=11,
    )

    assert text.endswith("8:30 · +2 guests")

    single = render_payment_post(
        label="Anna",
        service_date=SATURDAY,
        lift_times=("8:30",),
        seats=2,
        amount_gel=30,
        user_id=11,
    )
    assert single.endswith("+1 guest")


def test_board_date_survives_the_callback_round_trip() -> None:
    assert decode_board_date(encode_board_date(SATURDAY)) == SATURDAY
