from datetime import date

import pytest

from veloexpress_bot.polls.defaults import CHECK_ANSWERS_OPTION, StartLocation
from veloexpress_bot.polls.render import PollRenderInput, render_poll, render_poll_notice


def test_render_poll_uses_ru_en_weekend_template() -> None:
    draft = render_poll(PollRenderInput(service_date=date(2026, 5, 16)))

    assert draft.question == "🚐 Суббота · 16 мая\nSaturday · May 16"
    assert draft.options[-1] == CHECK_ANSWERS_OPTION
    assert draft.options[0] == "🚲 8:30"
    assert draft.options[1] == "🚲 10:00"
    assert draft.is_anonymous is False
    assert draft.allows_multiple_answers is True


def test_render_poll_notice_explains_dynamic_first_lift_location() -> None:
    assert render_poll_notice(StartLocation.JUSTICE_HALL) == (
        "📍 Первая состоявшаяся заброска дня — от Дома Юстиции. Если заброска "
        "на 8:30 не набралась, первой считается следующее набравшееся время. "
        "Остальные — от Ваке-парка.\n"
        "The first running lift of the day departs from Justice Hall. If the 8:30 "
        "lift does not run, the next running time becomes the first lift. All later "
        "lifts depart from Vake Park.\n\n"
        "💳 После голосования внесите предоплату.\n"
        "Please send the prepayment after voting."
    )


def test_render_poll_notice_supports_future_vake_only_override() -> None:
    assert render_poll_notice(StartLocation.VAKE) == (
        "📍 Все заброски — от Ваке-парка.\n"
        "All lifts depart from Vake Park.\n\n"
        "💳 После голосования внесите предоплату.\n"
        "Please send the prepayment after voting."
    )


def test_render_poll_can_cancel_lifts() -> None:
    draft = render_poll(
        PollRenderInput(
            service_date=date(2026, 5, 16),
            cancelled_lift_times=("11:45", "15:30"),
        )
    )

    assert "11:45" not in "\n".join(draft.options)
    assert "15:30" not in "\n".join(draft.options)
    assert "8:30" in draft.options[0]
    assert "10:00" in draft.options[1]
    assert "13:30" in draft.options[2]


def test_render_poll_requires_at_least_one_lift() -> None:
    with pytest.raises(ValueError, match="At least one lift"):
        render_poll(
            PollRenderInput(
                service_date=date(2026, 5, 16),
                cancelled_lift_times=("8:30", "10:00", "11:45", "13:30", "15:30"),
            )
        )
