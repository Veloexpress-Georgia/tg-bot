from veloexpress_bot.service_day_defaults import (
    ServiceDayDefaultValues,
    render_service_day_defaults,
)


def test_service_day_defaults_card_explains_snapshot_scope() -> None:
    card = render_service_day_defaults(
        ServiceDayDefaultValues(
            price_gel=15,
            deadline_time="20:00",
            timezone="Asia/Tbilisi",
        )
    )

    assert "15 GEL per seat" in card.text
    # Which evening, spelled out: "the day before" left the reader to work out
    # which day the deadline belonged to.
    assert "20:00 the evening before each lift day" in card.text
    assert "Asia/Tbilisi" in card.text
    assert "These apply to lift days published from now on." in card.text
    assert "Days already published keep the price and deadline they opened with." in card.text
    assert [
        [button.callback_data for button in row] for row in card.reply_markup.inline_keyboard
    ] == [
        ["defaults:price:sub", "defaults:price:add"],
        ["defaults:deadline:sub", "defaults:deadline:add"],
        # When polls open is a setting too, and this is where an admin looks.
        ["plan:schedule"],
        ["defaults:menu"],
    ]
    # A seat costs 15 GEL, so a 5 GEL step moved it by a third and could never
    # land on 16 or 18.
    assert [button.text for button in card.reply_markup.inline_keyboard[0]] == [
        "➖ 1 GEL",
        "➕ 1 GEL",
    ]
