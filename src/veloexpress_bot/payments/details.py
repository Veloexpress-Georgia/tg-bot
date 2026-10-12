"""Shared bank details and cash instructions for payment cards."""

from veloexpress_bot.payment_copy import CASH_BUTTON, PAID_BUTTON

PAYMENT_RECIPIENT = "Mikheil Nozadze"
PAYMENT_ACCOUNTS = (
    ("BoG", "GE54BG0000000526056155"),
    ("TBC", "GE03TB7331745061100055"),
)


def bank_details_text() -> str:
    lines = ["🏦 Bank transfer", f"<code>{PAYMENT_RECIPIENT}</code>"]
    lines.extend(f"{bank}: <code>{account}</code>" for bank, account in PAYMENT_ACCOUNTS)
    lines.extend(
        (
            "",
            f"Transferred? Tap {PAID_BUTTON}.",
            f"Paying Misho cash on site? Tap {CASH_BUTTON}.",
        )
    )
    return "\n".join(lines)
