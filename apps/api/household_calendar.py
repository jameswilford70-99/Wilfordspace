from datetime import date, time
from typing import Optional

from fastapi import APIRouter, Cookie, HTTPException, Query
from pydantic import BaseModel, Field


router = APIRouter(
    prefix="/api/calendar",
    tags=["calendar"],
)


class CalendarEventRequest(BaseModel):
    title: str = Field(min_length=1, max_length=250)
    description: str = ""
    event_date: date
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    all_day: bool = False
    location: str = ""
    category: str = "Other"
    colour: str = "#ef8b2c"


class CalendarEventUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=250)
    description: str = ""
    event_date: date
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    all_day: bool = False
    location: str = ""
    category: str = "Other"
    colour: str = "#ef8b2c"


async def get_connection():
    from main import get_connection

    return await get_connection()


async def get_household(wilfordspace_session: str | None):
    from main import get_current_user

    if not wilfordspace_session:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    user = await get_current_user(wilfordspace_session)

    connection = await get_connection()

    try:
        household = await connection.fetchrow(
            """
            SELECT
                h.id,
                h.name,
                hm.role
            FROM households h
            JOIN household_members hm
                ON hm.household_id = h.id
            WHERE hm.user_id = $1
            ORDER BY h.id
            LIMIT 1
            """,
            user["id"],
        )
    finally:
        await connection.close()

    if not household:
        raise HTTPException(
            status_code=404,
            detail="Household not found",
        )

    return user, dict(household)


async def ensure_calendar_table():
    connection = await get_connection()

    try:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS calendar_events (
                id BIGSERIAL PRIMARY KEY,
                household_id BIGINT NOT NULL
                    REFERENCES households(id)
                    ON DELETE CASCADE,
                title VARCHAR(250) NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                event_date DATE NOT NULL,
                start_time TIME NULL,
                end_time TIME NULL,
                all_day BOOLEAN NOT NULL DEFAULT FALSE,
                location VARCHAR(250) NOT NULL DEFAULT '',
                category VARCHAR(80) NOT NULL DEFAULT 'Other',
                colour VARCHAR(20) NOT NULL DEFAULT '#ef8b2c',
                created_by BIGINT NOT NULL
                    REFERENCES users(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS
                calendar_events_household_date_idx
            ON calendar_events(household_id, event_date);
            """
        )
    finally:
        await connection.close()


def event_to_dict(event):
    result = dict(event)

    if result.get("event_date") is not None:
        result["event_date"] = result["event_date"].isoformat()

    if result.get("start_time") is not None:
        result["start_time"] = result["start_time"].strftime("%H:%M")

    if result.get("end_time") is not None:
        result["end_time"] = result["end_time"].strftime("%H:%M")

    result["all_day"] = bool(result.get("all_day", False))

    return result


async def get_event(
    connection,
    event_id: int,
    household_id: int,
):
    return await connection.fetchrow(
        """
        SELECT
            id,
            household_id,
            title,
            description,
            event_date,
            start_time,
            end_time,
            all_day,
            location,
            category,
            colour,
            created_by,
            created_at,
            updated_at
        FROM calendar_events
        WHERE id = $1
          AND household_id = $2
        """,
        event_id,
        household_id,
    )


@router.get("/events")
async def list_calendar_events(
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    wilfordspace_session: str | None = Cookie(default=None),
):
    if start_date and end_date and end_date < start_date:
        raise HTTPException(
            status_code=400,
            detail="end_date must not be before start_date",
        )

    await ensure_calendar_table()

    _, household = await get_household(
        wilfordspace_session
    )

    connection = await get_connection()

    try:
        if start_date and end_date:
            events = await connection.fetch(
                """
                SELECT
                    id,
                    household_id,
                    title,
                    description,
                    event_date,
                    start_time,
                    end_time,
                    all_day,
                    location,
                    category,
                    colour,
                    created_by,
                    created_at,
                    updated_at
                FROM calendar_events
                WHERE household_id = $1
                  AND event_date BETWEEN $2 AND $3
                ORDER BY
                    event_date,
                    all_day DESC,
                    start_time NULLS FIRST,
                    title,
                    id
                """,
                household["id"],
                start_date,
                end_date,
            )
        else:
            events = await connection.fetch(
                """
                SELECT
                    id,
                    household_id,
                    title,
                    description,
                    event_date,
                    start_time,
                    end_time,
                    all_day,
                    location,
                    category,
                    colour,
                    created_by,
                    created_at,
                    updated_at
                FROM calendar_events
                WHERE household_id = $1
                ORDER BY
                    event_date,
                    all_day DESC,
                    start_time NULLS FIRST,
                    title,
                    id
                LIMIT 500
                """,
                household["id"],
            )
    finally:
        await connection.close()

    return {
        "events": [
            event_to_dict(event)
            for event in events
        ]
    }


@router.post("/events")
async def create_calendar_event(
    data: CalendarEventRequest,
    wilfordspace_session: str | None = Cookie(default=None),
):
    await ensure_calendar_table()

    user, household = await get_household(
        wilfordspace_session
    )

    title = data.title.strip()

    if not title:
        raise HTTPException(
            status_code=400,
            detail="Event title is required",
        )

    if (
        not data.all_day
        and data.start_time
        and data.end_time
        and data.end_time < data.start_time
    ):
        raise HTTPException(
            status_code=400,
            detail="End time must not be before start time",
        )

    connection = await get_connection()

    try:
        event = await connection.fetchrow(
            """
            INSERT INTO calendar_events (
                household_id,
                title,
                description,
                event_date,
                start_time,
                end_time,
                all_day,
                location,
                category,
                colour,
                created_by
            )
            VALUES (
                $1,
                $2,
                $3,
                $4,
                $5,
                $6,
                $7,
                $8,
                $9,
                $10,
                $11
            )
            RETURNING
                id,
                household_id,
                title,
                description,
                event_date,
                start_time,
                end_time,
                all_day,
                location,
                category,
                colour,
                created_by,
                created_at,
                updated_at
            """,
            household["id"],
            title,
            data.description.strip(),
            data.event_date,
            None if data.all_day else data.start_time,
            None if data.all_day else data.end_time,
            data.all_day,
            data.location.strip(),
            data.category.strip() or "Other",
            data.colour.strip() or "#ef8b2c",
            user["id"],
        )
    finally:
        await connection.close()

    return {
        "message": "Calendar event created",
        "event": event_to_dict(event),
    }


@router.put("/events/{event_id}")
async def update_calendar_event(
    event_id: int,
    data: CalendarEventUpdateRequest,
    wilfordspace_session: str | None = Cookie(default=None),
):
    await ensure_calendar_table()

    _, household = await get_household(
        wilfordspace_session
    )

    title = data.title.strip()

    if not title:
        raise HTTPException(
            status_code=400,
            detail="Event title is required",
        )

    if (
        not data.all_day
        and data.start_time
        and data.end_time
        and data.end_time < data.start_time
    ):
        raise HTTPException(
            status_code=400,
            detail="End time must not be before start time",
        )

    connection = await get_connection()

    try:
        event = await connection.fetchrow(
            """
            UPDATE calendar_events
            SET
                title = $1,
                description = $2,
                event_date = $3,
                start_time = $4,
                end_time = $5,
                all_day = $6,
                location = $7,
                category = $8,
                colour = $9,
                updated_at = NOW()
            WHERE id = $10
              AND household_id = $11
            RETURNING
                id,
                household_id,
                title,
                description,
                event_date,
                start_time,
                end_time,
                all_day,
                location,
                category,
                colour,
                created_by,
                created_at,
                updated_at
            """,
            title,
            data.description.strip(),
            data.event_date,
            None if data.all_day else data.start_time,
            None if data.all_day else data.end_time,
            data.all_day,
            data.location.strip(),
            data.category.strip() or "Other",
            data.colour.strip() or "#ef8b2c",
            event_id,
            household["id"],
        )
    finally:
        await connection.close()

    if not event:
        raise HTTPException(
            status_code=404,
            detail="Calendar event not found",
        )

    return {
        "message": "Calendar event updated",
        "event": event_to_dict(event),
    }


@router.delete("/events/{event_id}")
async def delete_calendar_event(
    event_id: int,
    wilfordspace_session: str | None = Cookie(default=None),
):
    await ensure_calendar_table()

    _, household = await get_household(
        wilfordspace_session
    )

    connection = await get_connection()

    try:
        deleted = await connection.fetchrow(
            """
            DELETE FROM calendar_events
            WHERE id = $1
              AND household_id = $2
            RETURNING id
            """,
            event_id,
            household["id"],
        )
    finally:
        await connection.close()

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Calendar event not found",
        )

    return {
        "message": "Calendar event deleted",
        "id": deleted["id"],
    }
