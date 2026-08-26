from datetime import date, timedelta

from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel


router = APIRouter(
    prefix="/api/shopping-list",
    tags=["shopping list"],
)


class ShoppingItemRequest(BaseModel):
    name: str
    quantity: str = ""
    category: str = "Other"
    notes: str = ""


class ShoppingItemUpdateRequest(BaseModel):
    name: str
    quantity: str = ""
    category: str = "Other"
    notes: str = ""
    purchased: bool = False


class GenerateShoppingListRequest(BaseModel):
    start_date: date
    end_date: date


async def get_connection():
    from main import get_connection

    return await get_connection()


async def get_household(session: str | None):
    from main import get_current_user

    if not session:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    user = await get_current_user(session)

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


async def ensure_shopping_tables():
    connection = await get_connection()

    try:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS shopping_lists (
                id BIGSERIAL PRIMARY KEY,
                household_id BIGINT NOT NULL
                    REFERENCES households(id)
                    ON DELETE CASCADE,
                name VARCHAR(150) NOT NULL
                    DEFAULT 'Main shopping list',
                created_by BIGINT NOT NULL
                    REFERENCES users(id),
                created_at TIMESTAMPTZ NOT NULL
                    DEFAULT NOW()
            );

            CREATE UNIQUE INDEX IF NOT EXISTS
                shopping_lists_household_name_idx
            ON shopping_lists(household_id, name);

            CREATE TABLE IF NOT EXISTS shopping_list_items (
                id BIGSERIAL PRIMARY KEY,
                shopping_list_id BIGINT NOT NULL
                    REFERENCES shopping_lists(id)
                    ON DELETE CASCADE,
                name VARCHAR(250) NOT NULL,
                quantity VARCHAR(100) NOT NULL
                    DEFAULT '',
                category VARCHAR(80) NOT NULL
                    DEFAULT 'Other',
                notes TEXT NOT NULL DEFAULT '',
                purchased BOOLEAN NOT NULL
                    DEFAULT FALSE,
                source_recipe_id BIGINT
                    REFERENCES recipes(id)
                    ON DELETE SET NULL,
                created_by BIGINT NOT NULL
                    REFERENCES users(id),
                created_at TIMESTAMPTZ NOT NULL
                    DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL
                    DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS
                shopping_items_list_idx
            ON shopping_list_items(shopping_list_id);

            CREATE INDEX IF NOT EXISTS
                shopping_items_purchased_idx
            ON shopping_list_items(purchased);
            """
        )
    finally:
        await connection.close()


async def get_or_create_list(
    connection,
    household_id,
    user_id,
):
    shopping_list = await connection.fetchrow(
        """
        SELECT
            id,
            household_id,
            name,
            created_by,
            created_at
        FROM shopping_lists
        WHERE household_id = $1
          AND name = 'Main shopping list'
        LIMIT 1
        """,
        household_id,
    )

    if shopping_list:
        return shopping_list

    return await connection.fetchrow(
        """
        INSERT INTO shopping_lists (
            household_id,
            name,
            created_by
        )
        VALUES (
            $1,
            'Main shopping list',
            $2
        )
        RETURNING
            id,
            household_id,
            name,
            created_by,
            created_at
        """,
        household_id,
        user_id,
    )


async def item_response(connection, item):
    result = dict(item)

    if result.get("source_recipe_id") is not None:
        result["source_recipe_title"] = await connection.fetchval(
            """
            SELECT title
            FROM recipes
            WHERE id = $1
            """,
            result["source_recipe_id"],
        )
    else:
        result["source_recipe_title"] = None

    return result


@router.get("")
async def get_shopping_list(
    include_purchased: bool = True,
    wilfordspace_session: str | None = Cookie(default=None),
):
    await ensure_shopping_tables()

    user, household = await get_household(
        wilfordspace_session
    )

    connection = await get_connection()

    try:
        shopping_list = await get_or_create_list(
            connection,
            household["id"],
            user["id"],
        )

        if include_purchased:
            items = await connection.fetch(
                """
                SELECT
                    id,
                    shopping_list_id,
                    name,
                    quantity,
                    category,
                    notes,
                    purchased,
                    source_recipe_id,
                    created_by,
                    created_at,
                    updated_at
                FROM shopping_list_items
                WHERE shopping_list_id = $1
                ORDER BY
                    purchased,
                    category,
                    name,
                    id
                """,
                shopping_list["id"],
            )
        else:
            items = await connection.fetch(
                """
                SELECT
                    id,
                    shopping_list_id,
                    name,
                    quantity,
                    category,
                    notes,
                    purchased,
                    source_recipe_id,
                    created_by,
                    created_at,
                    updated_at
                FROM shopping_list_items
                WHERE shopping_list_id = $1
                  AND purchased = FALSE
                ORDER BY
                    category,
                    name,
                    id
                """,
                shopping_list["id"],
            )

        result = []

        for item in items:
            result.append(
                await item_response(connection, item)
            )

        return {
            "list": dict(shopping_list),
            "items": result,
        }
    finally:
        await connection.close()


@router.post("/items")
async def add_shopping_item(
    data: ShoppingItemRequest,
    wilfordspace_session: str | None = Cookie(default=None),
):
    await ensure_shopping_tables()

    user, household = await get_household(
        wilfordspace_session
    )

    name = data.name.strip()

    if not name:
        raise HTTPException(
            status_code=400,
            detail="Shopping item name is required",
        )

    category = data.category.strip() or "Other"
    quantity = data.quantity.strip()
    notes = data.notes.strip()

    connection = await get_connection()

    try:
        shopping_list = await get_or_create_list(
            connection,
            household["id"],
            user["id"],
        )

        item = await connection.fetchrow(
            """
            INSERT INTO shopping_list_items (
                shopping_list_id,
                name,
                quantity,
                category,
                notes,
                created_by
            )
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING
                id,
                shopping_list_id,
                name,
                quantity,
                category,
                notes,
                purchased,
                source_recipe_id,
                created_by,
                created_at,
                updated_at
            """,
            shopping_list["id"],
            name,
            quantity,
            category,
            notes,
            user["id"],
        )

        return await item_response(connection, item)
    finally:
        await connection.close()


@router.put("/items/{item_id}")
async def update_shopping_item(
    item_id: int,
    data: ShoppingItemUpdateRequest,
    wilfordspace_session: str | None = Cookie(default=None),
):
    await ensure_shopping_tables()

    user, household = await get_household(
        wilfordspace_session
    )

    name = data.name.strip()

    if not name:
        raise HTTPException(
            status_code=400,
            detail="Shopping item name is required",
        )

    connection = await get_connection()

    try:
        shopping_list = await get_or_create_list(
            connection,
            household["id"],
            user["id"],
        )

        item = await connection.fetchrow(
            """
            UPDATE shopping_list_items
            SET
                name = $1,
                quantity = $2,
                category = $3,
                notes = $4,
                purchased = $5,
                updated_at = NOW()
            WHERE id = $6
              AND shopping_list_id = $7
            RETURNING
                id,
                shopping_list_id,
                name,
                quantity,
                category,
                notes,
                purchased,
                source_recipe_id,
                created_by,
                created_at,
                updated_at
            """,
            name,
            data.quantity.strip(),
            data.category.strip() or "Other",
            data.notes.strip(),
            data.purchased,
            item_id,
            shopping_list["id"],
        )

        if not item:
            raise HTTPException(
                status_code=404,
                detail="Shopping item not found",
            )

        return await item_response(connection, item)
    finally:
        await connection.close()


@router.delete("/items/{item_id}")
async def delete_shopping_item(
    item_id: int,
    wilfordspace_session: str | None = Cookie(default=None),
):
    await ensure_shopping_tables()

    user, household = await get_household(
        wilfordspace_session
    )

    connection = await get_connection()

    try:
        shopping_list = await get_or_create_list(
            connection,
            household["id"],
            user["id"],
        )

        result = await connection.execute(
            """
            DELETE FROM shopping_list_items
            WHERE id = $1
              AND shopping_list_id = $2
            """,
            item_id,
            shopping_list["id"],
        )

        if result == "DELETE 0":
            raise HTTPException(
                status_code=404,
                detail="Shopping item not found",
            )

        return {
            "message": "Shopping item deleted",
        }
    finally:
        await connection.close()


@router.post("/generate")
async def generate_shopping_list(
    data: GenerateShoppingListRequest,
    wilfordspace_session: str | None = Cookie(default=None),
):
    if data.end_date < data.start_date:
        raise HTTPException(
            status_code=400,
            detail="end_date must not be before start_date",
        )

    if (data.end_date - data.start_date).days > 31:
        raise HTTPException(
            status_code=400,
            detail="Date range cannot exceed 31 days",
        )

    await ensure_shopping_tables()

    user, household = await get_household(
        wilfordspace_session
    )

    start_date = data.start_date
    end_date = data.end_date

    # The automatic frontend update sends the date of the meal
    # that was just saved. Convert that single date into the
    # complete Monday-to-Sunday week.
    if start_date == end_date:
        start_date = start_date - timedelta(
            days=start_date.weekday()
        )
        end_date = start_date + timedelta(days=6)

    connection = await get_connection()

    try:
        async with connection.transaction():
            shopping_list = await get_or_create_list(
                connection,
                household["id"],
                user["id"],
            )

            # Remove only unpurchased automatically generated
            # recipe ingredients.
            #
            # Manual items have source_recipe_id set to NULL.
            # Purchased items are deliberately preserved.
            await connection.execute(
                """
                DELETE FROM shopping_list_items
                WHERE shopping_list_id = $1
                  AND source_recipe_id IS NOT NULL
                  AND purchased = FALSE
                """,
                shopping_list["id"],
            )

            ingredients = await connection.fetch(
                """
                SELECT
                    r.id AS recipe_id,
                    r.title AS recipe_title,
                    ri.quantity,
                    ri.name,
                    ri.position,
                    ri.id AS ingredient_id
                FROM meal_plan_entries mp
                JOIN recipes r
                    ON r.id = mp.recipe_id
                   AND r.household_id = $1
                JOIN recipe_ingredients ri
                    ON ri.recipe_id = r.id
                WHERE mp.household_id = $1
                  AND mp.meal_date BETWEEN $2 AND $3
                  AND mp.recipe_id IS NOT NULL
                ORDER BY
                    r.title,
                    ri.position,
                    ri.id
                """,
                household["id"],
                start_date,
                end_date,
            )

            added = 0
            skipped = 0

            for ingredient in ingredients:
                ingredient_name = (
                    ingredient["name"] or ""
                ).strip()

                if not ingredient_name:
                    skipped += 1
                    continue

                existing = await connection.fetchval(
                    """
                    SELECT id
                    FROM shopping_list_items
                    WHERE shopping_list_id = $1
                      AND source_recipe_id = $2
                      AND LOWER(name) = LOWER($3)
                      AND purchased = FALSE
                    LIMIT 1
                    """,
                    shopping_list["id"],
                    ingredient["recipe_id"],
                    ingredient_name,
                )

                if existing:
                    skipped += 1
                    continue

                await connection.execute(
                    """
                    INSERT INTO shopping_list_items (
                        shopping_list_id,
                        name,
                        quantity,
                        category,
                        notes,
                        source_recipe_id,
                        created_by
                    )
                    VALUES ($1, $2, $3, 'Other', $4, $5, $6)
                    """,
                    shopping_list["id"],
                    ingredient_name,
                    (
                        ingredient["quantity"] or ""
                    ).strip(),
                    f"From {ingredient['recipe_title']}",
                    ingredient["recipe_id"],
                    user["id"],
                )

                added += 1

            return {
                "message": "Shopping list generated",
                "added": added,
                "skipped": skipped,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            }
    finally:
        await connection.close()
