# Postgres backup and restore

The database contains bookings, deadline rosters, payment entries, refund reports, and pending Telegram deliveries. A persistent Docker volume is not a backup.

## Backup

Run a daily custom-format dump from the production Postgres service and copy it to storage outside the Coolify host:

```sh
pg_dump --format=custom --no-owner --file=veloexpress-YYYYMMDD.dump "$DATABASE_URL"
```

Keep at least seven daily dumps and four weekly dumps. Encrypt access to the backup location because Telegram identities and payment reports are personal data.

## Restore drill

At least monthly, restore the newest dump into a separate database:

```sh
createdb veloexpress_restore_check
pg_restore --no-owner --dbname=veloexpress_restore_check veloexpress-YYYYMMDD.dump
DATABASE_URL=postgresql+asyncpg://.../veloexpress_restore_check alembic current
```

Confirm that Alembic reports the current head and that counts for `poll_batch`, `payment_entry`, `deadline_roster`, and `telegram_outbox` are plausible. Delete the restore-check database afterwards. Never test a restore over the live database.

## Recovery order

1. Stop the bot so polling and background jobs cannot write during recovery.
2. Restore Postgres into a new database.
3. Run `alembic upgrade head` against the restored database.
4. Point one bot instance at it and start the bot.
5. Check container health and logs for outbox delivery before resuming normal operation.
