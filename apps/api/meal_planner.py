from datetime import date as date_type
from typing import Optional

from fastapi import APIRouter, Cookie, HTTPException, Query
from pydantic import BaseModel, Field


router = APIRouter(
    prefix="/api/meal-plan",
    tags=["meal planner"],
)


class MealPlanRequest(BaseModel):
    date: Optional[date_type] = None
    meal_date: Optional[date_type] = None

    meal_type: str = "dinner"

    title: Optional[str] = ""
    custom_name: Optional[str] = None

    recipe_id: Optional[int] = None

    servings: int = Field(
        default=4,
        ge=1,
        le=100,
    )

    notes: Optional[str] = ""


class MealPlanUpdateRequest(BaseModel):
    date: Optional[date_type] = None
    meal_date: Optional[date_type] = None

    meal_type: Optional[str] = None

    title: Optional[str] = None
    custom_name: Optional[str] = None

    recipe_id: Optional[int] = None

    servings: Optional[int] = Field(
        default=None,
        ge=1,
        le=100,
    )

    notes: Optional[str] = None


async def ensure_meal_plan_table():
    from main import get_connection

    connection = await get_connection()

    try:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS meal_plan_entries (
                id BIGSERIAL PRIMARY KEY,
                household_id BIGINT NOT NULL
                    REFERENCES households(id)
                    ON DELETE CASCADE,
                recipe_id BIGINT
                    REFERENCES recipes(id)
                    ON DELETE SET NULL,
                meal_date DATE NOT NULL,
                meal_type VARCHAR(30) NOT NULL DEFAULT 'dinner',
                title VARCHAR(250) NOT NULL DEFAULT '',
                servings INTEGER NOT NULL DEFAULT 4,
                notes TEXT NOT NULL DEFAULT '',
                created_by BIGINT NOT NULL
                    REFERENCES users(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS meal_plan_household_date_idx
                ON meal_plan_entries(household_id, meal_date);
            """
        )
    finally:
        await connection.close()


async def get_authenticated_household(
    wilfordspace_session: str | None,
):
    from main import get_connection, get_current_user

    if not wilfordspace_session:
        raise HTTPException(
            status_code=401,
            detail="Authentication required.",
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
            status_code=400,
            detail="Your account is not assigned to a household.",
        )

    return user, household


def get_meal_date(payload):
    return payload.date or payload.meal_date


def get_meal_title(payload):
    if payload.custom_name is not None:
        return payload.custom_name.strip()

    if payload.title is not None:
        return payload.title.strip()

    return ""


def clean_notes(value):
    return (value or "").strip()


def meal_to_dict(row):
    meal_date = row["meal_date"]
    recipe_id = row["recipe_id"]

    recipe = None

    if recipe_id is not None:
        recipe = {
            "id": recipe_id,
            "title": row["recipe_title"],
            "name": row["recipe_title"],
            "image_url": row["recipe_image_url"],
        }

    return {
        "id": row["id"],
        "date": meal_date.isoformat() if meal_date else None,
        "meal_date": meal_date.isoformat() if meal_date else None,
        "meal_type": row["meal_type"],
        "recipe_id": recipe_id,
        "title": row["title"],
        "name": row["title"],
        "custom_name": row["title"],
        "servings": row["servings"],
        "notes": row["notes"],
        "recipe": recipe,
    }


async def validate_recipe(
    connection,
    recipe_id: int | None,
    household_id: int,
):
    if recipe_id is None:
        return

    recipe = await connection.fetchrow(
        """
        SELECT id
        FROM recipes
        WHERE id = $1
          AND household_id = $2
        """,
        recipe_id,
        household_id,
    )

    if not recipe:
        raise HTTPException(
            status_code=404,
            detail="The selected recipe was not found.",
        )


@router.get("")
async def list_meals(
    start_date: Optional[date_type] = Query(default=None),
    end_date: Optional[date_type] = Query(default=None),
    wilfordspace_session: str | None = Cookie(default=None),
):
    await ensure_meal_plan_table()

    _, household = await get_authenticated_household(
        wilfordspace_session
    )

    if start_date is None:
        today = date_type.today()
        start_date = today.fromordinal(
            today.toordinal() - today.weekday()
        )

    if end_date is None:
        end_date = start_date.fromordinal(
            start_date.toordinal() + 6
        )

    from main import get_connection

    connection = await get_connection()

    try:
        rows = await connection.fetch(
            """
            SELECT
                mpe.id,
                mpe.household_id,
                mpe.recipe_id,
                mpe.meal_date,
                mpe.meal_type,
                mpe.title,
                mpe.servings,
                mpe.notes,
                r.title AS recipe_title,
                r.image_url AS recipe_image_url
            FROM meal_plan_entries mpe
            LEFT JOIN recipes r
                ON r.id = mpe.recipe_id
            WHERE mpe.household_id = $1
              AND mpe.meal_date BETWEEN $2 AND $3
            ORDER BY
                mpe.meal_date,
                mpe.meal_type,
                mpe.id
            """,
            household["id"],
            start_date,
            end_date,
        )
    finally:
        await connection.close()

    return {
        "meals": [meal_to_dict(row) for row in rows]
    }


@router.post("")
async def create_meal(
    payload: MealPlanRequest,
    wilfordspace_session: str | None = Cookie(default=None),
):
    await ensure_meal_plan_table()

    user, household = await get_authenticated_household(
        wilfordspace_session
    )

    selected_date = get_meal_date(payload)

    if selected_date is None:
        raise HTTPException(
            status_code=422,
            detail="A meal date is required.",
        )

    title = get_meal_title(payload)

    if payload.recipe_id is None and not title:
        raise HTTPException(
            status_code=422,
            detail="Choose a recipe or enter a custom meal name.",
        )

    from main import get_connection

    connection = await get_connection()

    try:
        await validate_recipe(
            connection,
            payload.recipe_id,
            household["id"],
        )

        row = await connection.fetchrow(
            """
            INSERT INTO meal_plan_entries (
                household_id,
                recipe_id,
                meal_date,
                meal_type,
                title,
                servings,
                notes,
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
                $8
            )
            RETURNING id
            """,
            household["id"],
            payload.recipe_id,
            selected_date,
            payload.meal_type or "dinner",
            title,
            payload.servings,
            clean_notes(payload.notes),
            user["id"],
        )

        meal_id = row["id"]
    finally:
        await connection.close()

    return {
        "message": "Meal created successfully.",
        "id": meal_id,
    }


@router.put("/{meal_id}")
async def update_meal(
    meal_id: int,
    payload: MealPlanUpdateRequest,
    wilfordspace_session: str | None = Cookie(default=None),
):
    await ensure_meal_plan_table()

    _, household = await get_authenticated_household(
        wilfordspace_session
    )

    from main import get_connection

    connection = await get_connection()

    try:
        existing = await connection.fetchrow(
            """
            SELECT
                id,
                recipe_id,
                meal_date,
                meal_type,
                title,
                servings,
                notes
            FROM meal_plan_entries
            WHERE id = $1
              AND household_id = $2
            """,
            meal_id,
            household["id"],
        )

        if not existing:
            raise HTTPException(
                status_code=404,
                detail="Meal not found.",
            )

        selected_date = (
            payload.date
            or payload.meal_date
            or existing["meal_date"]
        )

        meal_type = (
            payload.meal_type
            if payload.meal_type is not None
            else existing["meal_type"]
        )

        if payload.custom_name is not None:
            title = payload.custom_name.strip()
        elif payload.title is not None:
            title = payload.title.strip()
        else:
            title = existing["title"]

        recipe_id = (
            payload.recipe_id
            if payload.recipe_id is not None
            else existing["recipe_id"]
        )

        servings = (
            payload.servings
            if payload.servings is not None
            else existing["servings"]
        )

        notes = (
            clean_notes(payload.notes)
            if payload.notes is not None
            else existing["notes"]
        )

        if recipe_id is None and not title:
            raise HTTPException(
                status_code=422,
                detail="Choose a recipe or enter a custom meal name.",
            )

        await validate_recipe(
            connection,
            recipe_id,
            household["id"],
        )

        await connection.execute(
            """
            UPDATE meal_plan_entries
            SET
                recipe_id = $1,
                meal_date = $2,
                meal_type = $3,
                title = $4,
                servings = $5,
                notes = $6,
                updated_at = NOW()
            WHERE id = $7
              AND household_id = $8
            """,
            recipe_id,
            selected_date,
            meal_type or "dinner",
            title,
            servings or 4,
            notes,
            meal_id,
            household["id"],
        )
    finally:
        await connection.close()

    return {
        "message": "Meal updated successfully.",
        "id": meal_id,
    }


@router.delete("/{meal_id}")
async def delete_meal(
    meal_id: int,
    wilfordspace_session: str | None = Cookie(default=None),
):
    await ensure_meal_plan_table()

    _, household = await get_authenticated_household(
        wilfordspace_session
    )

    from main import get_connection

    connection = await get_connection()

    try:
        deleted = await connection.fetchrow(
            """
            DELETE FROM meal_plan_entries
            WHERE id = $1
              AND household_id = $2
            RETURNING id
            """,
            meal_id,
            household["id"],
        )
    finally:
        await connection.close()

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Meal not found.",
        )

    return {
        "message": "Meal deleted successfully.",
        "id": deleted["id"],
    }
