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
    assert "20:00 the day before" in card.text
    assert "Asia/Tbilisi" in card.text
    assert "Existing days keep their snapshot" in card.text
    assert [
        [button.callback_data for button in row] for row in card.reply_markup.inline_keyboard
    ] == [
        ["defaults:price:sub", "defaults:price:add"],
        ["defaults:deadline:sub", "defaults:deadline:add"],
        ["defaults:menu"],
    ]
