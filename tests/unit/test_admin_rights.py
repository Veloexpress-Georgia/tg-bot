from veloexpress_bot.bot.admin_rights import (
    DEFAULT_GROUP_ADMIN_RIGHTS,
    GROUP_ADMIN_LINK_RIGHTS,
    build_group_admin_invite_link,
)


def test_default_group_admin_rights_are_minimal_for_poll_management() -> None:
    assert DEFAULT_GROUP_ADMIN_RIGHTS.can_manage_chat is True
    assert DEFAULT_GROUP_ADMIN_RIGHTS.can_delete_messages is True
    assert DEFAULT_GROUP_ADMIN_RIGHTS.can_pin_messages is True

    assert DEFAULT_GROUP_ADMIN_RIGHTS.is_anonymous is False
    assert DEFAULT_GROUP_ADMIN_RIGHTS.can_manage_video_chats is False
    assert DEFAULT_GROUP_ADMIN_RIGHTS.can_restrict_members is False
    assert DEFAULT_GROUP_ADMIN_RIGHTS.can_promote_members is False
    assert DEFAULT_GROUP_ADMIN_RIGHTS.can_change_info is False
    assert DEFAULT_GROUP_ADMIN_RIGHTS.can_invite_users is False
    assert DEFAULT_GROUP_ADMIN_RIGHTS.can_manage_topics is False


def test_group_admin_invite_link_requests_only_needed_rights() -> None:
    assert GROUP_ADMIN_LINK_RIGHTS == ("delete_messages", "pin_messages")
    assert (
        build_group_admin_invite_link("@veloexpress_bot")
        == "https://t.me/veloexpress_bot?startgroup&admin=delete_messages+pin_messages"
    )
