# Telegram Smoke Test

Use the test bot and test group/topic first.

1. Set `.env` values:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_ADMIN_IDS`
   - `TELEGRAM_TARGET_CHAT_ID`
   - `TELEGRAM_TARGET_THREAD_ID` when posting to a forum topic
2. Start the local database, run migrations, and start the bot with `just dev`.
3. From an allowed admin account, send `/create_lift_poll` in the target chat/topic.
4. Repeat `/create_lift_poll` in a direct message with the bot.
5. Disable and re-enable Saturday or Sunday.
6. Toggle the first lift location.
7. Cancel and re-enable one lift.
8. Confirm poll creation.
9. Verify native polls appear only for enabled days in the target chat/topic.
10. Verify pin behavior and whether non-anonymous poll answers are visible.
11. From a non-admin account, verify the command is rejected.

If Telegram permissions prevent pinning or cleanup, record that in the deployment notes. Poll creation should still succeed.

## Native Poll Tradeoff

The current Veloexpress poll pattern includes a `Посмотреть ответы/Check answers` option. Telegram native polls do not provide a separate "view results without voting" control. The MVP keeps the existing option text for continuity and uses multiple answers so riders can choose a lift and still check answers.

This limitation must be checked in the test group before production use. If it causes confusion, the next iteration should either remove the option or switch to a custom inline-keyboard voting flow.
