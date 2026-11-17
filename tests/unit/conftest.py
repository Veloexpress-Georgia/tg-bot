import pytest


@pytest.fixture(autouse=True)
def cabinet_members_without_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never reach the real Bot API for cabinet membership in unit tests.

    Apps built without an explicit `membership=` treat every user as a chat
    member; membership tests pass their own gate.
    """

    def lookup(_bot: object, _chat_id: int | None, **_: object):
        async def is_member(_user_id: int) -> bool:
            return True

        return is_member

    monkeypatch.setattr("veloexpress_api.app.telegram_member_lookup", lookup)
