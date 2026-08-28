import hmac
import os
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response


router = APIRouter(
    prefix="/api/calendar",
    tags=["calendar feed"],
)


def ics_escape(value) -> str:
    if value is None:
        return ""

    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def fold_ics_line(
    line: str,
    maximum_length: int = 74,
) -> list[str]:
    """
    Fold long iCalendar lines according to RFC 5545.
    """
    if len(line) <= maximum_length:
        return [line]

    folded = []

    while len(line) > maximum_length:
        folded.append(line[:maximum_length])
        line = " " + line[maximum_length:]

    if line:
        folded.append(line)

    return folded


def add_ics_line(
    lines: list[str],
    name: str,
    value: str,
):
    lines.extend(
        fold_ics_line(
            f"{name}:{value}"
        )
    )


def utc_timestamp(
    value: datetime | None = None,
) -> str:
    if value is None:
        value = datetime.now(timezone.utc)

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )


def parse_date(value) -> date | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    return date.fromisoformat(str(value)[:10])


def parse_time(value) -> time | None:
    if value is None:
        return None

    if isinstance(value, time):
        return value

    return time.fromisoformat(str(value)[:5])


def combine_as_utc(
    event_date: date,
    event_time: time,
) -> datetime:
    return datetime.combine(
        event_date,
        event_time,
    ).replace(tzinfo=timezone.utc)


def add_ical_event(
    lines: list[str],
    *,
    uid: str,
    title: str,
    event_date: date,
    description: str = "",
    location: str = "",
    start_time: time | None = None,
    end_time: time | None = None,
    all_day: bool = False,
):
    add_ics_line(lines, "BEGIN", "VEVENT")
    add_ics_line(lines, "UID", uid)
    add_ics_line(lines, "DTSTAMP", utc_timestamp())

    if all_day or start_time is None:
        add_ics_line(
            lines,
            "DTSTART;VALUE=DATE",
            event_date.strftime("%Y%m%d"),
        )

        next_day = event_date + timedelta(days=1)

        add_ics_line(
            lines,
            "DTEND;VALUE=DATE",
            next_day.strftime("%Y%m%d"),
        )
    else:
        start_datetime = combine_as_utc(
            event_date,
            start_time,
        )

        add_ics_line(
            lines,
            "DTSTART",
            utc_timestamp(start_datetime),
        )

        if end_time is not None:
            end_datetime = combine_as_utc(
                event_date,
                end_time,
            )

            if end_datetime <= start_datetime:
                end_datetime += timedelta(days=1)

            add_ics_line(
                lines,
                "DTEND",
                utc_timestamp(end_datetime),
            )

    add_ics_line(
        lines,
        "SUMMARY",
        ics_escape(title or "WilfordSpace event"),
    )

    if description:
        add_ics_line(
            lines,
            "DESCRIPTION",
            ics_escape(description),
        )

    if location:
        add_ics_line(
            lines,
            "LOCATION",
            ics_escape(location),
        )

    add_ics_line(lines, "END", "VEVENT")


def build_icalendar(
    calendar_events,
    meal_events,
) -> str:
    lines: list[str] = []

    add_ics_line(lines, "BEGIN", "VCALENDAR")
    add_ics_line(lines, "VERSION", "2.0")
    add_ics_line(
        lines,
        "PRODID",
        "-//WilfordSpace//Household Calendar//EN",
    )
    add_ics_line(lines, "CALSCALE", "GREGORIAN")
    add_ics_line(lines, "METHOD", "PUBLISH")
    add_ics_line(
        lines,
        "X-WR-CALNAME",
        "WilfordSpace Household Calendar",
    )
    add_ics_line(
        lines,
        "X-WR-CALDESC",
        "WilfordSpace household events and planned meals",
    )

    for event in calendar_events:
        event_date = parse_date(event["event_date"])

        if event_date is None:
            continue

        all_day = bool(event["all_day"])

        start_time = None

        if not all_day:
            start_time = parse_time(event["start_time"])

        end_time = None

        if not all_day:
            end_time = parse_time(event["end_time"])

        add_ical_event(
            lines,
            uid=(
                f"calendar-event-{event['id']}"
                "@wilfordspace.wilfordhome.uk"
            ),
            title=event["title"] or "WilfordSpace event",
            description=event["description"] or "",
            location=event["location"] or "",
            event_date=event_date,
            start_time=start_time,
            end_time=end_time,
            all_day=all_day,
        )

    for meal in meal_events:
        meal_date = parse_date(meal["meal_date"])

        if meal_date is None:
            continue

        recipe_title = meal["recipe_title"] or ""
        custom_title = meal["title"] or ""

        meal_title = (
            recipe_title
            or custom_title
            or "Planned meal"
        )

        description_parts = ["Planned meal"]

        if meal["meal_type"]:
            description_parts.append(
                f"Meal type: {meal['meal_type']}"
            )

        if meal["servings"]:
            description_parts.append(
                f"Servings: {meal['servings']}"
            )

        if meal["notes"]:
            description_parts.append(
                meal["notes"]
            )

        add_ical_event(
            lines,
            uid=(
                f"planned-meal-{meal['id']}"
                "@wilfordspace.wilfordhome.uk"
            ),
            title=f"Meal: {meal_title}",
            description="\n".join(description_parts),
            event_date=meal_date,
            all_day=True,
        )

    add_ics_line(lines, "END", "VCALENDAR")

    return "\r\n".join(lines) + "\r\n"


@router.get("/feed/{feed_token}.ics")
async def calendar_feed(
    feed_token: str,
):
    configured_token = os.getenv(
        "CALENDAR_FEED_TOKEN"
    )

    if not configured_token:
        raise HTTPException(
            status_code=503,
            detail="Calendar feed is not configured.",
        )

    if not hmac.compare_digest(
        feed_token,
        configured_token,
    ):
        raise HTTPException(
            status_code=404,
            detail="Calendar feed not found.",
        )

    from main import get_connection

    connection = await get_connection()

    try:
        calendar_events = await connection.fetch(
            """
            SELECT
                id,
                title,
                description,
                event_date,
                start_time,
                end_time,
                all_day,
                location,
                category,
                colour
            FROM calendar_events
            ORDER BY
                event_date,
                all_day DESC,
                start_time NULLS FIRST,
                title,
                id
            """
        )

        meal_events = await connection.fetch(
            """
            SELECT
                mpe.id,
                mpe.meal_date,
                mpe.meal_type,
                mpe.title,
                mpe.servings,
                mpe.notes,
                r.title AS recipe_title
            FROM meal_plan_entries mpe
            LEFT JOIN recipes r
                ON r.id = mpe.recipe_id
            ORDER BY
                mpe.meal_date,
                mpe.meal_type,
                mpe.id
            """
        )
    finally:
        await connection.close()

    calendar_text = build_icalendar(
        calendar_events,
        meal_events,
    )

    return Response(
        content=calendar_text,
        media_type="text/calendar",
        headers={
            "Content-Disposition": (
                'inline; filename="wilfordspace.ics"'
            ),
            "Cache-Control": (
                "no-cache, no-store, must-revalidate"
            ),
        },
    )
