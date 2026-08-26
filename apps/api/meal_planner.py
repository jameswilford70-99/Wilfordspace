from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from auth import get_current_user


router = APIRouter(
    prefix="/api/meal-plan",
    tags=["meal-plan"],
)


class MealPlanCreate(BaseModel):
    date: date
    meal_date: Optional[date] = None
    meal_type: str = "Dinner"
    recipe_id: Optional[int] = None
    custom_name: Optional[str] = None
    servings: Optional[int] = Field(default=2, ge=1)
    notes: Optional[str] = None


class MealPlanUpdate(BaseModel):
    date: Optional[date] = None
    meal_date: Optional[date] = None
    meal_type: Optional[str] = None
    recipe_id: Optional[int] = None
    custom_name: Optional[str] = None
    servings: Optional[int] = Field(default=None, ge=1)
    notes: Optional[str] = None


def get_household_id(user):
    household_id = (
        user.get("household_id")
        if isinstance(user, dict)
        else getattr(user, "household_id", None)
    )

    if household_id is None:
        raise HTTPException(
            status_code=400,
            detail="Your account is not assigned to a household.",
        )

    return household_id


def get_user_id(user):
    user_id = (
        user.get("id")
        if isinstance(user, dict)
        else getattr(user, "id", None)
    )

    if user_id is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid user session.",
        )

    return user_id


def effective_date(value: MealPlanCreate | MealPlanUpdate) -> Optional[date]:
    return value.date or value.meal_date


async def validate_recipe(
    db: AsyncSession,
    recipe_id: Optional[int],
    household_id: int,
):
    if recipe_id is None:
        return

    result = await db.execute(
        text(
            """
            SELECT id
            FROM recipes
            WHERE id = :recipe_id
              AND household_id = :household_id
            """
        ),
        {
            "recipe_id": recipe_id,
            "household_id": household_id,
        },
    )

    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=404,
            detail="The selected recipe could not be found.",
        )


async def recipe_join_sql():
    return """
        SELECT
            mp.id,
            mp.meal_date,
            mp.meal_type,
            mp.recipe_id,
            mp.custom_name,
            mp.servings,
            mp.notes,
            mp.household_id,
            mp.created_by,
            r.title AS recipe_title,
            r.image_url AS recipe_image_url
        FROM meal_plan mp
        LEFT JOIN recipes r
          ON r.id = mp.recipe_id
        WHERE mp.household_id = :household_id
    """


def meal_row(row):
    item = dict(row)

    result = {
        "id": item["id"],
        "date": item["meal_date"].isoformat()
        if item.get("meal_date")
        else None,
        "meal_date": item["meal_date"].isoformat()
        if item.get("meal_date")
        else None,
        "meal_type": item.get("meal_type") or "Dinner",
        "recipe_id": item.get("recipe_id"),
        "custom_name": item.get("custom_name"),
        "servings": item.get("servings"),
        "notes": item.get("notes"),
    }

    if item.get("recipe_id"):
        result["recipe"] = {
            "id": item["recipe_id"],
            "title": item.get("recipe_title"),
            "name": item.get("recipe_title"),
            "image_url": item.get("recipe_image_url"),
        }
    else:
        result["recipe"] = None

    return result


@router.get("")
async def list_meals(
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    household_id = get_household_id(current_user)

    if start_date is None:
        today = date.today()
        start_date = today - timedelta(days=today.weekday())

    if end_date is None:
        end_date = start_date + timedelta(days=6)

    sql = await recipe_join_sql()

    sql += """
        AND mp.meal_date BETWEEN :start_date AND :end_date
        ORDER BY mp.meal_date, mp.meal_type, mp.id
    """

    result = await db.execute(
        text(sql),
        {
            "household_id": household_id,
            "start_date": start_date,
            "end_date": end_date,
        },
    )

    return {
        "meals": [meal_row(row) for row in result.mappings().all()]
    }


@router.post("")
async def create_meal(
    payload: MealPlanCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    household_id = get_household_id(current_user)
    user_id = get_user_id(current_user)
    selected_date = effective_date(payload)

    if selected_date is None:
        raise HTTPException(
            status_code=422,
            detail="A meal date is required.",
        )

    if payload.recipe_id is None and not payload.custom_name:
        raise HTTPException(
            status_code=422,
            detail="Choose a recipe or enter a custom meal name.",
        )

    await validate_recipe(
        db,
        payload.recipe_id,
        household_id,
    )

    result = await db.execute(
        text(
            """
            INSERT INTO meal_plan (
                household_id,
                created_by,
                meal_date,
                meal_type,
                recipe_id,
                custom_name,
                servings,
                notes
            )
            VALUES (
                :household_id,
                :created_by,
                :meal_date,
                :meal_type,
                :recipe_id,
                :custom_name,
                :servings,
                :notes
            )
            RETURNING id
            """
        ),
        {
            "household_id": household_id,
            "created_by": user_id,
            "meal_date": selected_date,
            "meal_type": payload.meal_type or "Dinner",
            "recipe_id": payload.recipe_id,
            "custom_name": payload.custom_name,
            "servings": payload.servings or 2,
            "notes": payload.notes,
        },
    )

    meal_id = result.scalar_one()
    await db.commit()

    return {
        "message": "Meal created successfully.",
        "id": meal_id,
    }


@router.put("/{meal_id}")
async def update_meal(
    meal_id: int,
    payload: MealPlanUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    household_id = get_household_id(current_user)

    existing_result = await db.execute(
        text(
            """
            SELECT *
            FROM meal_plan
            WHERE id = :meal_id
              AND household_id = :household_id
            """
        ),
        {
            "meal_id": meal_id,
            "household_id": household_id,
        },
    )

    existing = existing_result.mappings().first()

    if existing is None:
        raise HTTPException(
            status_code=404,
            detail="Meal not found.",
        )

    selected_date = effective_date(payload) or existing["meal_date"]
    meal_type = payload.meal_type or existing["meal_type"] or "Dinner"
    recipe_id = (
        payload.recipe_id
        if payload.recipe_id is not None
        else existing["recipe_id"]
    )
    custom_name = (
        payload.custom_name
        if payload.custom_name is not None
        else existing["custom_name"]
    )
    servings = (
        payload.servings
        if payload.servings is not None
        else existing["servings"]
    )
    notes = (
        payload.notes
        if payload.notes is not None
        else existing["notes"]
    )

    if recipe_id is None and not custom_name:
        raise HTTPException(
            status_code=422,
            detail="Choose a recipe or enter a custom meal name.",
        )

    await validate_recipe(
        db,
        recipe_id,
        household_id,
    )

    await db.execute(
        text(
            """
            UPDATE meal_plan
            SET
                meal_date = :meal_date,
                meal_type = :meal_type,
                recipe_id = :recipe_id,
                custom_name = :custom_name,
                servings = :servings,
                notes = :notes
            WHERE id = :meal_id
              AND household_id = :household_id
            """
        ),
        {
            "meal_id": meal_id,
            "household_id": household_id,
            "meal_date": selected_date,
            "meal_type": meal_type,
            "recipe_id": recipe_id,
            "custom_name": custom_name,
            "servings": servings or 2,
            "notes": notes,
        },
    )

    await db.commit()

    return {
        "message": "Meal updated successfully.",
        "id": meal_id,
    }


@router.delete("/{meal_id}")
async def delete_meal(
    meal_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    household_id = get_household_id(current_user)

    result = await db.execute(
        text(
            """
            DELETE FROM meal_plan
            WHERE id = :meal_id
              AND household_id = :household_id
            RETURNING id
            """
        ),
        {
            "meal_id": meal_id,
            "household_id": household_id,
        },
    )

    deleted_id = result.scalar_one_or_none()

    if deleted_id is None:
        raise HTTPException(
            status_code=404,
            detail="Meal not found.",
        )

    await db.commit()

    return {
        "message": "Meal deleted successfully.",
        "id": deleted_id,
    }
