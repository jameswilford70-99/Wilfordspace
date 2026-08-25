import os
from datetime import datetime, timedelta, timezone

import asyncpg
from fastapi import Cookie, FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr
from pwdlib import PasswordHash

DATABASE_URL = os.environ["DATABASE_URL"]
SECRET_KEY = os.environ["SECRET_KEY"]
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24 * 7

password_hash = PasswordHash.recommended()
app = FastAPI(title="WilfordSpace API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    household_name: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


async def get_connection():
    return await asyncpg.connect(DATABASE_URL)


async def create_token(user_id: int) -> str:
    expires = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode(
        {"sub": str(user_id), "exp": expires},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


async def get_current_user(session: str | None):
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    try:
        payload = jwt.decode(session, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
    except (JWTError, KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )

    connection = await get_connection()
    try:
        user = await connection.fetchrow(
            """
            SELECT id, name, email
            FROM users
            WHERE id = $1
            """,
            user_id,
        )
    finally:
        await connection.close()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    return dict(user)


@app.on_event("startup")
async def startup():
    connection = await get_connection()
    try:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id BIGSERIAL PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                email VARCHAR(320) UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS households (
                id BIGSERIAL PRIMARY KEY,
                name VARCHAR(150) NOT NULL,
                owner_id BIGINT NOT NULL REFERENCES users(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS household_members (
                household_id BIGINT NOT NULL REFERENCES households(id)
                    ON DELETE CASCADE,
                user_id BIGINT NOT NULL REFERENCES users(id)
                    ON DELETE CASCADE,
                role VARCHAR(20) NOT NULL DEFAULT 'member',
                joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (household_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS recipes (
                id BIGSERIAL PRIMARY KEY,
                household_id BIGINT NOT NULL REFERENCES households(id)
                    ON DELETE CASCADE,
                created_by BIGINT NOT NULL REFERENCES users(id),
                title VARCHAR(250) NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                servings INTEGER NOT NULL DEFAULT 4,
                prep_time_minutes INTEGER,
                cook_time_minutes INTEGER,
                source_url TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS recipe_ingredients (
                id BIGSERIAL PRIMARY KEY,
                recipe_id BIGINT NOT NULL REFERENCES recipes(id)
                    ON DELETE CASCADE,
                quantity VARCHAR(50) NOT NULL DEFAULT '',
                unit VARCHAR(50) NOT NULL DEFAULT '',
                name VARCHAR(250) NOT NULL,
                position INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS recipe_instructions (
                id BIGSERIAL PRIMARY KEY,
                recipe_id BIGINT NOT NULL REFERENCES recipes(id)
                    ON DELETE CASCADE,
                step_number INTEGER NOT NULL,
                instruction TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS recipes_household_id_idx
                ON recipes(household_id);

            CREATE INDEX IF NOT EXISTS recipes_title_idx
                ON recipes(title);
            """
        )
    finally:
        await connection.close()


@app.get("/")
async def root():
    return {
        "application": "WilfordSpace",
        "message": "WilfordSpace API is running",
    }


@app.get("/api/health")
async def health():
    connection = await get_connection()
    try:
        await connection.execute("SELECT 1")
        return {
            "status": "ok",
            "application": "WilfordSpace",
            "database": "connected",
        }
    finally:
        await connection.close()


@app.post("/api/auth/register")
async def register(data: RegisterRequest, response: Response):
    if len(data.password) < 8:
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 8 characters",
        )

    name = data.name.strip()
    household_name = data.household_name.strip()
    email = str(data.email).lower().strip()

    if not name or not household_name:
        raise HTTPException(
            status_code=400,
            detail="Name and household name are required",
        )

    connection = await get_connection()

    try:
        async with connection.transaction():
            existing = await connection.fetchval(
                "SELECT id FROM users WHERE email = $1",
                email,
            )

            if existing:
                raise HTTPException(
                    status_code=409,
                    detail="An account with that email already exists",
                )

            user = await connection.fetchrow(
                """
                INSERT INTO users (name, email, password_hash)
                VALUES ($1, $2, $3)
                RETURNING id, name, email
                """,
                name,
                email,
                password_hash.hash(data.password),
            )

            household = await connection.fetchrow(
                """
                INSERT INTO households (name, owner_id)
                VALUES ($1, $2)
                RETURNING id, name
                """,
                household_name,
                user["id"],
            )

            await connection.execute(
                """
                INSERT INTO household_members
                    (household_id, user_id, role)
                VALUES ($1, $2, 'owner')
                """,
                household["id"],
                user["id"],
            )

        token = await create_token(user["id"])
        response.set_cookie(
            key="wilfordspace_session",
            value=token,
            httponly=True,
            secure=COOKIE_SECURE,
            samesite="lax",
            max_age=TOKEN_EXPIRE_HOURS * 60 * 60,
            path="/",
        )

        return {
            "message": "Account created",
            "user": dict(user),
            "household": dict(household),
        }

    finally:
        await connection.close()


@app.post("/api/auth/login")
async def login(data: LoginRequest, response: Response):
    email = str(data.email).lower().strip()
    connection = await get_connection()

    try:
        user = await connection.fetchrow(
            """
            SELECT id, name, email, password_hash
            FROM users
            WHERE email = $1
            """,
            email,
        )
    finally:
        await connection.close()

    if not user or not password_hash.verify(data.password, user["password_hash"]):
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password",
        )

    token = await create_token(user["id"])
    response.set_cookie(
        key="wilfordspace_session",
        value=token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=TOKEN_EXPIRE_HOURS * 60 * 60,
        path="/",
    )

    return {
        "message": "Logged in",
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
        },
    }


@app.post("/api/auth/logout")
async def logout(response: Response):
    response.delete_cookie(
        key="wilfordspace_session",
        path="/",
    )
    return {"message": "Logged out"}


@app.get("/api/auth/me")
async def me(
    wilfordspace_session: str | None = Cookie(default=None),
):
    user = await get_current_user(wilfordspace_session)
    return {"user": user}



class IngredientRequest(BaseModel):
    quantity: str = ""
    unit: str = ""
    name: str


class InstructionRequest(BaseModel):
    instruction: str


class RecipeRequest(BaseModel):
    title: str
    description: str = ""
    servings: int = 4
    prep_time_minutes: int | None = None
    cook_time_minutes: int | None = None
    source_url: str | None = None
    ingredients: list[IngredientRequest] = []
    instructions: list[InstructionRequest] = []


async def get_user_household(user_id: int):
    connection = await get_connection()
    try:
        household = await connection.fetchrow(
            """
            SELECT h.id, h.name, hm.role
            FROM households h
            JOIN household_members hm ON hm.household_id = h.id
            WHERE hm.user_id = $1
            ORDER BY h.id
            LIMIT 1
            """,
            user_id,
        )
        return dict(household) if household else None
    finally:
        await connection.close()


async def recipe_response(recipe: dict):
    connection = await get_connection()
    try:
        ingredients = await connection.fetch(
            """
            SELECT id, quantity, unit, name, position
            FROM recipe_ingredients
            WHERE recipe_id = $1
            ORDER BY position, id
            """,
            recipe["id"],
        )

        instructions = await connection.fetch(
            """
            SELECT id, step_number, instruction
            FROM recipe_instructions
            WHERE recipe_id = $1
            ORDER BY step_number, id
            """,
            recipe["id"],
        )

        result = dict(recipe)
        result["ingredients"] = [dict(item) for item in ingredients]
        result["instructions"] = [dict(item) for item in instructions]
        return result
    finally:
        await connection.close()


@app.get("/api/recipes")
async def list_recipes(
    search: str = "",
    wilfordspace_session: str | None = Cookie(default=None),
):
    user = await get_current_user(wilfordspace_session)
    household = await get_user_household(user["id"])

    if not household:
        raise HTTPException(status_code=404, detail="Household not found")

    connection = await get_connection()
    try:
        recipes = await connection.fetch(
            """
            SELECT id, title, description, servings,
                   prep_time_minutes, cook_time_minutes,
                   source_url, created_at, updated_at
            FROM recipes
            WHERE household_id = $1
              AND ($2 = '' OR title ILIKE '%' || $2 || '%')
            ORDER BY title
            """,
            household["id"],
            search.strip(),
        )

        return {"recipes": [dict(recipe) for recipe in recipes]}
    finally:
        await connection.close()


@app.get("/api/recipes/{recipe_id}")
async def get_recipe(
    recipe_id: int,
    wilfordspace_session: str | None = Cookie(default=None),
):
    user = await get_current_user(wilfordspace_session)
    household = await get_user_household(user["id"])

    if not household:
        raise HTTPException(status_code=404, detail="Household not found")

    connection = await get_connection()
    try:
        recipe = await connection.fetchrow(
            """
            SELECT id, household_id, created_by, title, description,
                   servings, prep_time_minutes, cook_time_minutes,
                   source_url, created_at, updated_at
            FROM recipes
            WHERE id = $1 AND household_id = $2
            """,
            recipe_id,
            household["id"],
        )
    finally:
        await connection.close()

    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    return await recipe_response(dict(recipe))


@app.post("/api/recipes")
async def create_recipe(
    data: RecipeRequest,
    wilfordspace_session: str | None = Cookie(default=None),
):
    user = await get_current_user(wilfordspace_session)
    household = await get_user_household(user["id"])

    if not household:
        raise HTTPException(status_code=404, detail="Household not found")

    title = data.title.strip()

    if not title:
        raise HTTPException(status_code=400, detail="Recipe title is required")

    if data.servings < 1:
        raise HTTPException(status_code=400, detail="Servings must be at least 1")

    connection = await get_connection()

    try:
        async with connection.transaction():
            recipe = await connection.fetchrow(
                """
                INSERT INTO recipes (
                    household_id, created_by, title, description,
                    servings, prep_time_minutes, cook_time_minutes, source_url
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id, household_id, created_by, title, description,
                          servings, prep_time_minutes, cook_time_minutes,
                          source_url, created_at, updated_at
                """,
                household["id"],
                user["id"],
                title,
                data.description.strip(),
                data.servings,
                data.prep_time_minutes,
                data.cook_time_minutes,
                data.source_url,
            )

            for position, ingredient in enumerate(data.ingredients):
                if ingredient.name.strip():
                    await connection.execute(
                        """
                        INSERT INTO recipe_ingredients
                            (recipe_id, quantity, unit, name, position)
                        VALUES ($1, $2, $3, $4, $5)
                        """,
                        recipe["id"],
                        ingredient.quantity.strip(),
                        ingredient.unit.strip(),
                        ingredient.name.strip(),
                        position,
                    )

            for number, instruction in enumerate(data.instructions, start=1):
                if instruction.instruction.strip():
                    await connection.execute(
                        """
                        INSERT INTO recipe_instructions
                            (recipe_id, step_number, instruction)
                        VALUES ($1, $2, $3)
                        """,
                        recipe["id"],
                        number,
                        instruction.instruction.strip(),
                    )

        return await recipe_response(dict(recipe))
    finally:
        await connection.close()


@app.delete("/api/recipes/{recipe_id}")
async def delete_recipe(
    recipe_id: int,
    wilfordspace_session: str | None = Cookie(default=None),
):
    user = await get_current_user(wilfordspace_session)
    household = await get_user_household(user["id"])

    if not household:
        raise HTTPException(status_code=404, detail="Household not found")

    connection = await get_connection()
    try:
        result = await connection.execute(
            """
            DELETE FROM recipes
            WHERE id = $1 AND household_id = $2
            """,
            recipe_id,
            household["id"],
        )

        if result == "DELETE 0":
            raise HTTPException(status_code=404, detail="Recipe not found")

        return {"message": "Recipe deleted"}
    finally:
        await connection.close()
@app.put("/api/recipes/{recipe_id}")
async def update_recipe(
    recipe_id: int,
    data: RecipeRequest,
    wilfordspace_session: str | None = Cookie(default=None),
):
    user = await get_current_user(wilfordspace_session)
    household = await get_user_household(user["id"])

    if not household:
        raise HTTPException(status_code=404, detail="Household not found")

    title = data.title.strip()

    if not title:
        raise HTTPException(status_code=400, detail="Recipe title is required")

    if data.servings < 1:
        raise HTTPException(status_code=400, detail="Servings must be at least 1")

    connection = await get_connection()

    try:
        async with connection.transaction():
            existing = await connection.fetchrow(
                """
                SELECT id
                FROM recipes
                WHERE id = $1 AND household_id = $2
                """,
                recipe_id,
                household["id"],
            )

            if not existing:
                raise HTTPException(
                    status_code=404,
                    detail="Recipe not found",
                )

            recipe = await connection.fetchrow(
                """
                UPDATE recipes
                SET title = $1,
                    description = $2,
                    servings = $3,
                    prep_time_minutes = $4,
                    cook_time_minutes = $5,
                    source_url = $6,
                    updated_at = NOW()
                WHERE id = $7 AND household_id = $8
                RETURNING id, household_id, created_by, title, description,
                          servings, prep_time_minutes, cook_time_minutes,
                          source_url, created_at, updated_at
                """,
                title,
                data.description.strip(),
                data.servings,
                data.prep_time_minutes,
                data.cook_time_minutes,
                data.source_url,
                recipe_id,
                household["id"],
            )

            await connection.execute(
                "DELETE FROM recipe_ingredients WHERE recipe_id = $1",
                recipe_id,
            )

            await connection.execute(
                "DELETE FROM recipe_instructions WHERE recipe_id = $1",
                recipe_id,
            )

            for position, ingredient in enumerate(data.ingredients):
                if ingredient.name.strip():
                    await connection.execute(
                        """
                        INSERT INTO recipe_ingredients
                            (recipe_id, quantity, unit, name, position)
                        VALUES ($1, $2, $3, $4, $5)
                        """,
                        recipe_id,
                        ingredient.quantity.strip(),
                        ingredient.unit.strip(),
                        ingredient.name.strip(),
                        position,
                    )

            for number, instruction in enumerate(data.instructions, start=1):
                if instruction.instruction.strip():
                    await connection.execute(
                        """
                        INSERT INTO recipe_instructions
                            (recipe_id, step_number, instruction)
                        VALUES ($1, $2, $3)
                        """,
                        recipe_id,
                        number,
                        instruction.instruction.strip(),
                    )

        return await recipe_response(dict(recipe))

    finally:
        await connection.close()
