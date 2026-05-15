from datetime import date

import pytest

from veloexpress_bot.polls.defaults import CHECK_ANSWERS_OPTION, StartLocation
from veloexpress_bot.polls.render import PollRenderInput, render_poll


def test_render_poll_uses_ru_en_weekend_template() -> None:
    draft = render_poll(PollRenderInput(service_date=date(2026, 5, 16)))

    assert draft.question.startswith("💳 После голосования")
    assert "🚐 Суббота 16.05 / Saturday" in draft.question
    assert "Выберите время заброски / Choose departure time" in draft.question
    assert draft.options[-1] == CHECK_ANSWERS_OPTION
    assert draft.options[0] == "🚲 10:00 · Дом Юстиции / Justice hall"
    assert draft.options[1] == "🚲 11:45 · от Ваке парка / Vake park"
    assert draft.is_anonymous is False
    assert draft.allows_multiple_answers is True


def test_render_poll_can_toggle_first_lift_to_vake() -> None:
    draft = render_poll(
        PollRenderInput(
            service_date=date(2026, 5, 17),
            first_lift_location=StartLocation.VAKE,
        )
    )

    assert "Воскресенье 17.05 / Sunday" in draft.question
    assert draft.options[0] == "🚲 10:00 · от Ваке парка / Vake park"


def test_render_poll_can_cancel_lifts() -> None:
    draft = render_poll(
        PollRenderInput(
            service_date=date(2026, 5, 16),
            cancelled_lift_times=("11:45", "15:30"),
        )
    )

    assert "11:45" not in "\n".join(draft.options)
    assert "15:30" not in "\n".join(draft.options)
    assert "10:00" in draft.options[0]
    assert "13:30" in draft.options[1]


def test_render_poll_requires_at_least_one_lift() -> None:
    with pytest.raises(ValueError, match="At least one lift"):
        render_poll(
            PollRenderInput(
                service_date=date(2026, 5, 16),
                cancelled_lift_times=("10:00", "11:45", "13:30", "15:30"),
            )
        )
