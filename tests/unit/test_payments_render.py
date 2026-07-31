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


def test_board_lists_running_lifts_price_and_the_taps() -> None:
    draft = render_payments_board(_view(outstanding=_owing(6)))

    assert "💸 Payments · Sat, 18 Jul" in draft.text
    assert "Running: 8:30, 10:00" in draft.text
    assert "15 GEL per seat · pay by 20:00" in draft.text
    assert "💸 transfer · 💵 cash · ➕ Guest adds a seat" in draft.text
    assert "Waiting on:" in draft.text
    assert draft.reply_markup is not None
    # Cash has its own button rather than a rule telling riders to press the
    # transfer one anyway. No "one fewer seat": underpaying while still holding the
    # seat is the phantom booking the group keeps tripping over.
    assert _buttons(draft.reply_markup) == {
        "pay:paid:20260718": "💸 I paid",
        "pay:cash:20260718": "💵 Cash",
        "pay:guest:20260718": "➕ Guest",
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


def test_payment_post_reports_declared_guests() -> None:
    """Guests are declared, not inferred from the seat total: re-voting moves the
    lift count, and inferring would silently turn a new lift into a guest."""
    text = render_payment_post(
        label="Anna",
        service_date=SATURDAY,
        amount_gel=45,
        user_id=11,
        guests=2,
    )

    assert text.endswith("+2 guests")

    single = render_payment_post(
        label="Anna",
        service_date=SATURDAY,
        amount_gel=30,
        user_id=11,
        guests=1,
    )
    assert single.endswith("+1 guest")


def test_board_date_survives_the_callback_round_trip() -> None:
    assert decode_board_date(encode_board_date(SATURDAY)) == SATURDAY
