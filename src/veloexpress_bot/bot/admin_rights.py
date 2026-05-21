import asyncio

from aiogram import Bot
from aiogram.types import ChatAdministratorRights

from veloexpress_bot.config import Settings, get_settings
from veloexpress_bot.observability import configure_logging

GROUP_ADMIN_LINK_RIGHTS = ("delete_messages", "pin_messages")

DEFAULT_GROUP_ADMIN_RIGHTS = ChatAdministratorRights(
    is_anonymous=False,
    can_manage_chat=True,
    can_delete_messages=True,
    can_manage_video_chats=False,
    can_restrict_members=False,
    can_promote_members=False,
    can_change_info=False,
    can_invite_users=False,
    can_post_stories=False,
    can_edit_stories=False,
    can_delete_stories=False,
    can_post_messages=False,
    can_edit_messages=False,
    can_pin_messages=True,
    can_manage_topics=False,
    can_manage_direct_messages=False,
)


def build_group_admin_invite_link(bot_username: str) -> str:
    username = bot_username.removeprefix("@")
    rights = "+".join(GROUP_ADMIN_LINK_RIGHTS)
    return f"https://t.me/{username}?startgroup&admin={rights}"


async def register_default_admin_rights(bot: Bot) -> None:
    await bot.set_my_default_administrator_rights(
        rights=DEFAULT_GROUP_ADMIN_RIGHTS,
        for_channels=False,
    )


async def register_default_admin_rights_from_settings(settings: Settings) -> None:
    if not settings.telegram_bot_token:
        msg = "TELEGRAM_BOT_TOKEN is required."
        raise RuntimeError(msg)

    bot = Bot(token=settings.telegram_bot_token)
    try:
        await register_default_admin_rights(bot)
        me = await bot.get_me()
        print("Registered default group admin rights.")
        print(f"Admin invite link: {build_group_admin_invite_link(me.username or str(me.id))}")
    finally:
        await bot.session.close()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    asyncio.run(register_default_admin_rights_from_settings(settings))


if __name__ == "__main__":
    main()
