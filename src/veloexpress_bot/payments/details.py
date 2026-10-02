"""Shared bank details and cash instructions for payment cards."""

from veloexpress_bot.payment_copy import CASH_ON_SITE_TEXT, CASH_REPORT_TEXT

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
            "Transfer to either account, then mark the completed payment.",
            CASH_ON_SITE_TEXT,
            CASH_REPORT_TEXT,
        )
    )
    return "\n".join(lines)
