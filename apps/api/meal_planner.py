from datetime import date
from typing import Optional

from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel, Field


router = APIRouter(
    prefix="/api/meal-plan",
    tags=["meal planner"],
)


class MealPlanRequest(BaseModel):
    meal_date: date
    meal_type: str = "dinner"
    title: str = ""
    recipe_id: Optional[int] = None
    servings: int = Field(default=4, ge=1, le=100)
    notes: str = ""


async def ensure_meal_plan_table():
    # Imported at request time to avoid a circular import with main.py.
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


async def get_authenticated_household(session: str | None):
    from main import get_connection, get_current_user

    user = await get_current_user(session)
    connection = await get_connection()

    try:
        household = await connection.fetchrow(
            """
            SELECT h.id, h.name, hm.role
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


def validate_meal_type(value: str) -> str:
    meal_type = value.strip().lower()

    allowed = {
        "breakfast",
        "lunch",
        "dinner",
        "snack",
        "other",
    }

    if meal_type not in allowed:
        raise HTTPException(
            status_code=400,
            detail=(
                "Meal type must be breakfast, lunch, dinner, "
                "snack, or other"
            ),
        )

    return meal_type


async def validate_recipe(
    connection,
    recipe_id: int | None,
    household_id: int,
):
    if recipe_id is None:
        return None

    recipe = await connection.fetchrow(
        """
        SELECT id, title
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
            detail="Recipe not found in this household",
        )

    return recipe


async def meal_response(connection, meal):
    recipe_title = None

    if meal["recipe_id"] is not None:
        recipe_title = await connection.fetchval(
            """
            SELECT title
            FROM recipes
            WHERE id = $1
            """,
            meal["recipe_id"],
        )

    result = dict(meal)
    result["recipe_title"] = recipe_title

    return result


@router.get("")
async def list_meals(
    start_date: date,
    end_date: date,
    wilfordspace_session: str | None = Cookie(default=None),
):
    if end_date < start_date:
        raise HTTPException(
            status_code=400,
            detail="end_date must not be before start_date",
        )

    if (end_date - start_date).days > 31:
        raise HTTPException(
            status_code=400,
            detail="Date range cannot exceed 31 days",
        )

    await ensure_meal_plan_table()
    _, household = await get_authenticated_household(
        wilfordspace_session
    )

    from main import get_connection

    connection = await get_connection()

    try:
        meals = await connection.fetch(
            """
            SELECT id, household_id, recipe_id, meal_date,
                   meal_type, title, servings, notes,
                   created_by, created_at, updated_at
            FROM meal_plan_entries
            WHERE household_id = $1
              AND meal_date BETWEEN $2 AND $3
            ORDER BY meal_date, meal_type, id
            """,
            household["id"],
            start_date,
            end_date,
        )

        result = []

        for meal in meals:
            result.append(
                await meal_response(connection, meal)
            )

        return {"meals": result}
    finally:
        await connection.close()


@router.post("")
async def create_meal(
    data: MealPlanRequest,
    wilfordspace_session: str | None = Cookie(default=None),
):
    await ensure_meal_plan_table()
    user, household = await get_authenticated_household(
        wilfordspace_session
    )

    meal_type = validate_meal_type(data.meal_type)
    title = data.title.strip()
    notes = data.notes.strip()

    from main import get_connection

    connection = await get_connection()

    try:
        async with connection.transaction():
            recipe = await validate_recipe(
                connection,
                data.recipe_id,
                household["id"],
            )

            if data.recipe_id is not None and not title:
                title = recipe["title"]

            if not title:
                raise HTTPException(
                    status_code=400,
                    detail="A meal title or recipe is required",
                )

            meal = await connection.fetchrow(
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
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id, household_id, recipe_id, meal_date,
                          meal_type, title, servings, notes,
                          created_by, created_at, updated_at
                """,
                household["id"],
                data.recipe_id,
                data.meal_date,
                meal_type,
                title,
                data.servings,
                notes,
                user["id"],
            )

            return await meal_response(connection, meal)
    finally:
        await connection.close()


@router.put("/{meal_id}")
async def update_meal(
    meal_id: int,
    data: MealPlanRequest,
    wilfordspace_session: str | None = Cookie(default=None),
):
    await ensure_meal_plan_table()
    user, household = await get_authenticated_household(
        wilfordspace_session
    )

    meal_type = validate_meal_type(data.meal_type)
    title = data.title.strip()
    notes = data.notes.strip()

    from main import get_connection

    connection = await get_connection()

    try:
        async with connection.transaction():
            recipe = await validate_recipe(
                connection,
                data.recipe_id,
                household["id"],
            )

            if data.recipe_id is not None and not title:
                title = recipe["title"]

            if not title:
                raise HTTPException(
                    status_code=400,
                    detail="A meal title or recipe is required",
                )

            meal = await connection.fetchrow(
                """
                UPDATE meal_plan_entries
                SET recipe_id = $1,
                    meal_date = $2,
                    meal_type = $3,
                    title = $4,
                    servings = $5,
                    notes = $6,
                    updated_at = NOW()
                WHERE id = $7
                  AND household_id = $8
                RETURNING id, household_id, recipe_id, meal_date,
                          meal_type, title, servings, notes,
                          created_by, created_at, updated_at
                """,
                data.recipe_id,
                data.meal_date,
                meal_type,
                title,
                data.servings,
                notes,
                meal_id,
                household["id"],
            )

            if not meal:
                raise HTTPException(
                    status_code=404,
                    detail="Meal-plan entry not found",
                )

            return await meal_response(connection, meal)
    finally:
        await connection.close()


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
        result = await connection.execute(
            """
            DELETE FROM meal_plan_entries
            WHERE id = $1
              AND household_id = $2
            """,
            meal_id,
            household["id"],
        )

        if result == "DELETE 0":
            raise HTTPException(
                status_code=404,
                detail="Meal-plan entry not found",
            )

        return {"message": "Meal removed"}
    finally:
        await connection.close()
