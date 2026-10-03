"""Render the public availability board as a deterministic Telegram photo."""

from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from veloexpress_bot.polls.defaults import MINIMUM_RIDERS
from veloexpress_bot.polls.render import (
    EN_SHORT_MONTHS,
    SHORT_DAY_LABELS,
    LiftAvailability,
    partition_availability_lifts,
)

WIDTH = 1000
PADDING = 42
INK = "#F3F7FF"
MUTED = "#9EADC1"
BACKGROUND = "#101827"
PANEL = "#1D2B3E"
QUIET_PANEL = "#192432"
PAST_DEADLINE_PANEL = "#17212D"
TRACK = "#35465B"
GREEN = "#61D6A3"
QUIET_GOLD = "#C8B68D"
BLUE = "#75BAF9"
PURPLE = "#C5A6FF"
# State colours stay apart from the seat-source colours: a blue FULL read as
# "external seats", a purple WAITLIST as "guests".
ORANGE = "#FFA46E"
QUIET_RED = "#AF858C"
QUIET_VOTE = "#7EAE98"
QUIET_GUEST = "#A594BA"
QUIET_OFFLINE = "#7D9FBE"
SEAT_GAP = 6
THRESHOLD_GAP = 20


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    # DejaVu is installed in the container; the macOS path makes local previews work.
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for path in (
        Path("/usr/share/fonts/truetype/dejavu") / name,
        Path("/System/Library/Fonts/SFNS.ttf"),
    ):
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def _wrap(
    draw: ImageDraw.ImageDraw,
    value: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    width: int,
) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in value.split():
        while draw.textlength(word, font=font) > width:
            if line:
                lines.append(line)
                line = ""
            split_at = 1
            while draw.textlength(word[: split_at + 1], font=font) <= width:
                split_at += 1
            lines.append(word[:split_at])
            word = word[split_at:]
        candidate = f"{line} {word}" if line else word
        if line and draw.textlength(candidate, font=font) > width:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


def _state(lift: LiftAvailability, *, deadline_passed: bool) -> tuple[str, str, str]:
    if lift.cancelled:
        return "CANCELLED", QUIET_RED, PAST_DEADLINE_PANEL
    if lift.seat_count > lift.capacity:
        return f"WAITLIST +{lift.seat_count - lift.capacity}", ORANGE, PANEL
    if lift.seat_count >= lift.capacity:
        return "FULL", INK, PANEL
    if lift.seat_count < MINIMUM_RIDERS:
        if deadline_passed:
            return "BELOW MINIMUM", MUTED, PAST_DEADLINE_PANEL
        return f"NEEDS {MINIMUM_RIDERS - lift.seat_count} MORE", QUIET_GOLD, QUIET_PANEL
    return f"{lift.capacity - lift.seat_count} SEATS LEFT", GREEN, PANEL


def _notes(lift: LiftAvailability) -> list[str]:
    if lift.cancelled:
        return []
    notes: list[str] = []
    if lift.guests:
        names = [f"{party.label} +{party.count}" for party in lift.guests if party.count]
        if names:
            notes.append("GUESTS  ·  " + ", ".join(names))
    return notes


def _breakdown(lift: LiftAvailability) -> tuple[tuple[str, str], ...]:
    # With votes only, the count beside the time already says it all.
    if lift.cancelled or not lift.off_poll_count:
        return ()
    segments = _seat_segments(lift)
    external_seats, guest_seats, seated_votes = (segments[0][0], segments[1][0], segments[2][0])
    parts: list[tuple[str, str]] = []
    if external_seats:
        parts.append((f"EXTERNAL +{external_seats}", BLUE))
    if guest_seats:
        parts.append((f"GUESTS +{guest_seats}", PURPLE))
    parts.append((f"VOTES {seated_votes}", GREEN))
    return tuple(parts)


def _seat_segments(lift: LiftAvailability) -> tuple[tuple[int, str], ...]:
    """Reserved seats lead the bar; waiting poll votes do not fill it."""
    capacity = max(lift.capacity, 0)
    offline = min(max(lift.manual_count, 0), capacity)
    guests = min(max(lift.guest_count, 0), capacity - offline)
    poll_votes = max(lift.seat_count - lift.off_poll_count, 0)
    votes = min(poll_votes, capacity - offline - guests)
    return ((offline, BLUE), (guests, PURPLE), (votes, GREEN))


def _draw_legend(
    draw: ImageDraw.ImageDraw, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, y: int
) -> None:
    x = PADDING + 2
    for label, color in (("EXTERNAL", BLUE), ("GUESTS", PURPLE), ("TG VOTES", GREEN)):
        draw.rounded_rectangle((x, y + 10, x + 20, y + 30), radius=6, fill=color)
        draw.text((x + 30, y), label, font=font, fill=MUTED)
        x += 30 + round(draw.textlength(label, font=font)) + 38


def _draw_bar(
    draw: ImageDraw.ImageDraw,
    lift: LiftAvailability,
    *,
    left: int,
    right: int,
    top: int,
    height: int,
    quiet: bool,
) -> None:
    """One cell per seat, so a glance counts seats; a wider gap marks the minimum."""
    if lift.capacity <= 0:
        draw.rounded_rectangle((left, top, right, top + height), radius=height // 2, fill=TRACK)
        return
    muted = {GREEN: QUIET_VOTE, PURPLE: QUIET_GUEST, BLUE: QUIET_OFFLINE}
    colors: list[str] = []
    if not lift.cancelled:
        for count, color in _seat_segments(lift):
            colors += [muted[color] if quiet else color] * count
    colors += [TRACK] * (lift.capacity - len(colors))
    threshold = MINIMUM_RIDERS if 0 < MINIMUM_RIDERS < lift.capacity else 0
    gaps = (lift.capacity - 1) * SEAT_GAP + (THRESHOLD_GAP - SEAT_GAP if threshold else 0)
    cell = (right - left - gaps) / lift.capacity
    x = float(left)
    for index, color in enumerate(colors):
        if index:
            x += THRESHOLD_GAP if index == threshold else SEAT_GAP
        if threshold and index == threshold and not lift.cancelled:
            tick = round(x - THRESHOLD_GAP / 2)
            draw.line((tick, top - 6, tick, top + height + 6), fill=INK, width=3)
        draw.rounded_rectangle(
            (round(x), top, round(x + cell), top + height),
            radius=min(5, height // 2),
            fill=color,
        )
        x += cell


def _draw_full_row(
    draw: ImageDraw.ImageDraw,
    lift: LiftAvailability,
    lines: list[str],
    breakdown: tuple[tuple[str, str], ...],
    *,
    y: int,
    row_height: int,
    deadline_passed: bool,
    time_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    number_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    state_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    pill_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    note_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> None:
    left, right = PADDING, WIDTH - PADDING
    state, accent, panel = _state(lift, deadline_passed=deadline_passed)
    draw.rounded_rectangle((left, y, right, y + row_height), radius=28, fill=panel)
    if panel == PANEL:
        draw.rounded_rectangle((left, y + 25, left + 8, y + row_height - 25), radius=4, fill=accent)
    primary_ink = MUTED if panel == PAST_DEADLINE_PANEL else INK
    draw.text((left + 30, y + 19), lift.time, font=time_font, fill=primary_ink)
    count = "—" if lift.cancelled else f"{min(lift.seat_count, lift.capacity)}/{lift.capacity}"
    count_width = draw.textlength(count, font=number_font)
    draw.text((right - 30 - count_width, y + 23), count, font=number_font, fill=primary_ink)
    _draw_bar(
        draw,
        lift,
        left=left + 32,
        right=right - 32,
        top=y + 100,
        height=17,
        quiet=panel != PANEL,
    )
    draw.text((left + 32, y + 132), state, font=state_font, fill=accent)

    detail_y = y + 185
    x = left + 32
    for label, color in breakdown:
        pill_width = round(draw.textlength(label, font=pill_font)) + 29
        draw.rounded_rectangle(
            (x, detail_y - 8, x + pill_width, detail_y + 45), radius=19, fill="#2A3B50"
        )
        draw.text((x + 14, detail_y - 2), label, font=pill_font, fill=color)
        x += pill_width + 12
    if breakdown:
        detail_y += 55
    for index, line in enumerate(lines):
        draw.text((left + 32, detail_y + index * 47), line, font=note_font, fill=MUTED)


def _draw_short_row(draw: ImageDraw.ImageDraw, lift: LiftAvailability, *, y: int) -> None:
    left, right = PADDING, WIDTH - PADDING
    draw.rounded_rectangle((left, y, right, y + 140), radius=24, fill=PAST_DEADLINE_PANEL)
    time_font, count_font, note_font = _font(48, bold=True), _font(43, bold=True), _font(30)
    draw.text((left + 26, y + 12), lift.time, font=time_font, fill=MUTED)
    count = f"{lift.seat_count}/{lift.capacity}"
    draw.text(
        (right - 26 - draw.textlength(count, font=count_font), y + 17),
        count,
        font=count_font,
        fill=MUTED,
    )
    _draw_bar(draw, lift, left=left + 26, right=right - 26, top=y + 75, height=13, quiet=True)
    draw.text((left + 26, y + 99), _short_summary(lift), font=note_font, fill=MUTED)


def _short_summary(lift: LiftAvailability) -> str:
    names = (("external", "external"), ("guest", "guests"), ("TG vote", "TG votes"))
    source_labels = [
        f"{count} {one if count == 1 else many}"
        for (count, _), (one, many) in zip(_seat_segments(lift), names, strict=True)
        if count
    ]
    sources = " · ".join(source_labels) if source_labels else "No bookings"
    return f"{sources}  ·  needs {MINIMUM_RIDERS - lift.seat_count} more"


def _draw_cancelled_row(draw: ImageDraw.ImageDraw, lift: LiftAvailability, *, y: int) -> None:
    left, right = PADDING, WIDTH - PADDING
    draw.rounded_rectangle((left, y, right, y + 78), radius=22, fill=PAST_DEADLINE_PANEL)
    draw.text((left + 26, y + 10), lift.time, font=_font(45, bold=True), fill=MUTED)
    label = "CANCELLED"
    font = _font(32, bold=True)
    draw.text(
        (right - 26 - draw.textlength(label, font=font), y + 19), label, font=font, fill=QUIET_RED
    )


def render_availability_card(
    service_date: date,
    lifts: tuple[LiftAvailability, ...],
    *,
    deadline_passed: bool = False,
) -> bytes:
    """Return a PNG sized for a legible Telegram chat preview."""
    note_font = _font(37)
    pill_font = _font(35, bold=True)
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    notes = [
        [wrapped for note in _notes(lift) for wrapped in _wrap(measure, note, note_font, 840)]
        for lift in lifts
    ]
    breakdowns = [_breakdown(lift) for lift in lifts]
    row_heights = [
        188 + (55 if breakdown else 0) + len(items) * 47
        for items, breakdown in zip(notes, breakdowns, strict=True)
    ]
    running, short, cancelled = partition_availability_lifts(lifts)
    full_rows = [
        (lift, lines, breakdown, row_height)
        for lift, lines, breakdown, row_height in zip(
            lifts, notes, breakdowns, row_heights, strict=True
        )
        if not deadline_passed or lift in running
    ]
    short_lifts = short if deadline_passed else ()
    cancelled_lifts = cancelled if deadline_passed else ()
    height = 270 + sum(row_height + 12 for _, _, _, row_height in full_rows)
    if deadline_passed:
        height += 60 + (65 if not full_rows else 0)
        if short_lifts:
            height += 23 + 55 + len(short_lifts) * 150
        if cancelled_lifts:
            height += 20 + 55 + len(cancelled_lifts) * 88
    height += 42
    image = Image.new("RGB", (WIDTH, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    eyebrow = _font(25, bold=True)
    title = _font(67, bold=True)
    subtitle = _font(31)
    time_font = _font(62, bold=True)
    number_font = _font(58, bold=True)
    state_font = _font(40, bold=True)

    draw.rounded_rectangle((42, 38, 58, 64), radius=8, fill=GREEN)
    draw.text((75, 37), "VELOEXPRESS  /  AVAILABILITY", font=eyebrow, fill=GREEN)
    date_label = (
        f"{SHORT_DAY_LABELS[service_date.weekday()]}, "
        f"{service_date.day} {EN_SHORT_MONTHS[service_date.month]}"
    )
    draw.text((42, 83), date_label, font=title, fill=INK)
    capacities = {lift.capacity for lift in lifts}
    subtitle_text = f"Runs from {MINIMUM_RIDERS} seats"
    if len(capacities) == 1:
        subtitle_text += f"  ·  {capacities.pop()} per lift"
    draw.text((44, 171), subtitle_text, font=subtitle, fill=MUTED)
    _draw_legend(draw, _font(30, bold=True), 219)

    y = 270
    if deadline_passed:
        draw.text((44, y + 8), f"RUNNING · {len(full_rows)}", font=_font(38, bold=True), fill=GREEN)
        y += 60
        if not full_rows:
            draw.text((44, y + 5), "No lifts reached five seats.", font=_font(32), fill=MUTED)
            y += 65
    for lift, lines, breakdown, row_height in full_rows:
        _draw_full_row(
            draw,
            lift,
            lines,
            breakdown,
            y=y,
            row_height=row_height,
            deadline_passed=deadline_passed,
            time_font=time_font,
            number_font=number_font,
            state_font=state_font,
            pill_font=pill_font,
            note_font=note_font,
        )
        y += row_height + 12
    if short_lifts:
        y += 23
        draw.text((44, y + 7), "BELOW MINIMUM", font=_font(36, bold=True), fill=MUTED)
        y += 55
        for lift in short_lifts:
            _draw_short_row(draw, lift, y=y)
            y += 150
    if cancelled_lifts:
        y += 20
        draw.text((44, y + 7), "CANCELLED", font=_font(36, bold=True), fill=QUIET_RED)
        y += 55
        for lift in cancelled_lifts:
            _draw_cancelled_row(draw, lift, y=y)
            y += 88

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
