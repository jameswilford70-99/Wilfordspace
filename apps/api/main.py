import ipaddress
import json
import os
import re
import socket
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import asyncpg
import httpx
from bs4 import BeautifulSoup
from fastapi import Cookie, FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr
from pwdlib import PasswordHash
from meal_planner import router as meal_plan_router
from shopping_list import router as shopping_list_router
from household_calendar import router as calendar_router
from calendar_feed import router as calendar_feed_router
from household_sharing import router as household_router


DATABASE_URL = os.environ["DATABASE_URL"]
SECRET_KEY = os.environ["SECRET_KEY"]
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() == "true"

ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24 * 7

password_hash = PasswordHash.recommended()

app = FastAPI(
    title="WilfordSpace API",
    version="0.5.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meal_plan_router)

app.include_router(shopping_list_router)

app.include_router(calendar_router)

app.include_router(calendar_feed_router)

app.include_router(household_router)

class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    household_name: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


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
    image_url: str | None = None
    ingredients: list[IngredientRequest] = []
    instructions: list[InstructionRequest] = []


class RecipeImportRequest(BaseModel):
    url: str


async def get_connection():
    return await asyncpg.connect(DATABASE_URL)


async def create_token(user_id: int) -> str:
    expires = datetime.now(timezone.utc) + timedelta(
        hours=TOKEN_EXPIRE_HOURS
    )

    return jwt.encode(
        {
            "sub": str(user_id),
            "exp": expires,
        },
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
        payload = jwt.decode(
            session,
            SECRET_KEY,
            algorithms=[ALGORITHM],
        )
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


async def get_user_household(user_id: int):
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
        result["ingredients"] = [
            dict(item) for item in ingredients
        ]
        result["instructions"] = [
            dict(item) for item in instructions
        ]

        return result
    finally:
        await connection.close()


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
                household_id BIGINT NOT NULL
                    REFERENCES households(id)
                    ON DELETE CASCADE,
                user_id BIGINT NOT NULL
                    REFERENCES users(id)
                    ON DELETE CASCADE,
                role VARCHAR(20) NOT NULL DEFAULT 'member',
                joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (household_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS recipes (
                id BIGSERIAL PRIMARY KEY,
                household_id BIGINT NOT NULL
                    REFERENCES households(id)
                    ON DELETE CASCADE,
                created_by BIGINT NOT NULL
                    REFERENCES users(id),
                title VARCHAR(250) NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                servings INTEGER NOT NULL DEFAULT 4,
                prep_time_minutes INTEGER,
                cook_time_minutes INTEGER,
                source_url TEXT,
                image_url TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            ALTER TABLE recipes
                ADD COLUMN IF NOT EXISTS image_url TEXT;

            CREATE TABLE IF NOT EXISTS recipe_ingredients (
                id BIGSERIAL PRIMARY KEY,
                recipe_id BIGINT NOT NULL
                    REFERENCES recipes(id)
                    ON DELETE CASCADE,
                quantity VARCHAR(50) NOT NULL DEFAULT '',
                unit VARCHAR(50) NOT NULL DEFAULT '',
                name VARCHAR(250) NOT NULL,
                position INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS recipe_instructions (
                id BIGSERIAL PRIMARY KEY,
                recipe_id BIGINT NOT NULL
                    REFERENCES recipes(id)
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
async def register(
    data: RegisterRequest,
    response: Response,
):
    if len(data.password) < 8:
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 8 characters",
        )

    name = data.name.strip()
    email = str(data.email).lower().strip()
    household_name = data.household_name.strip()

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
                INSERT INTO users
                    (name, email, password_hash)
                VALUES ($1, $2, $3)
                RETURNING id, name, email
                """,
                name,
                email,
                password_hash.hash(data.password),
            )

            household = await connection.fetchrow(
                """
                INSERT INTO households
                    (name, owner_id)
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
async def login(
    data: LoginRequest,
    response: Response,
):
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

    if not user or not password_hash.verify(
        data.password,
        user["password_hash"],
    ):
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


@app.get("/api/recipes")
async def list_recipes(
    search: str = "",
    wilfordspace_session: str | None = Cookie(default=None),
):
    user = await get_current_user(wilfordspace_session)
    household = await get_user_household(user["id"])

    if not household:
        raise HTTPException(
            status_code=404,
            detail="Household not found",
        )

    connection = await get_connection()

    try:
        recipes = await connection.fetch(
            """
            SELECT id, title, description, servings,
                   prep_time_minutes, cook_time_minutes,
                   source_url, image_url,
                   created_at, updated_at
            FROM recipes
            WHERE household_id = $1
              AND (
                $2 = ''
                OR title ILIKE '%' || $2 || '%'
              )
            ORDER BY title
            """,
            household["id"],
            search.strip(),
        )

        return {
            "recipes": [dict(recipe) for recipe in recipes],
        }
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
        raise HTTPException(
            status_code=404,
            detail="Household not found",
        )

    connection = await get_connection()

    try:
        recipe = await connection.fetchrow(
            """
            SELECT id, household_id, created_by, title,
                   description, servings,
                   prep_time_minutes, cook_time_minutes,
                   source_url, image_url,
                   created_at, updated_at
            FROM recipes
            WHERE id = $1
              AND household_id = $2
            """,
            recipe_id,
            household["id"],
        )
    finally:
        await connection.close()

    if not recipe:
        raise HTTPException(
            status_code=404,
            detail="Recipe not found",
        )

    return await recipe_response(dict(recipe))


@app.post("/api/recipes")
async def create_recipe(
    data: RecipeRequest,
    wilfordspace_session: str | None = Cookie(default=None),
):
    user = await get_current_user(wilfordspace_session)
    household = await get_user_household(user["id"])

    if not household:
        raise HTTPException(
            status_code=404,
            detail="Household not found",
        )

    title = data.title.strip()

    if not title:
        raise HTTPException(
            status_code=400,
            detail="Recipe title is required",
        )

    if data.servings < 1:
        raise HTTPException(
            status_code=400,
            detail="Servings must be at least 1",
        )

    connection = await get_connection()

    try:
        async with connection.transaction():
            recipe = await connection.fetchrow(
                """
                INSERT INTO recipes (
                    household_id, created_by, title,
                    description, servings,
                    prep_time_minutes, cook_time_minutes,
                    source_url, image_url
                )
                VALUES (
                    $1, $2, $3, $4, $5,
                    $6, $7, $8, $9
                )
                RETURNING id, household_id, created_by, title,
                          description, servings,
                          prep_time_minutes, cook_time_minutes,
                          source_url, image_url,
                          created_at, updated_at
                """,
                household["id"],
                user["id"],
                title,
                data.description.strip(),
                data.servings,
                data.prep_time_minutes,
                data.cook_time_minutes,
                data.source_url,
                data.image_url,
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

            for number, instruction in enumerate(
                data.instructions,
                start=1,
            ):
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


@app.put("/api/recipes/{recipe_id}")
async def update_recipe(
    recipe_id: int,
    data: RecipeRequest,
    wilfordspace_session: str | None = Cookie(default=None),
):
    user = await get_current_user(wilfordspace_session)
    household = await get_user_household(user["id"])

    if not household:
        raise HTTPException(
            status_code=404,
            detail="Household not found",
        )

    title = data.title.strip()

    if not title:
        raise HTTPException(
            status_code=400,
            detail="Recipe title is required",
        )

    if data.servings < 1:
        raise HTTPException(
            status_code=400,
            detail="Servings must be at least 1",
        )

    connection = await get_connection()

    try:
        async with connection.transaction():
            recipe = await connection.fetchrow(
                """
                UPDATE recipes
                SET title = $1,
                    description = $2,
                    servings = $3,
                    prep_time_minutes = $4,
                    cook_time_minutes = $5,
                    source_url = $6,
                    image_url = $7,
                    updated_at = NOW()
                WHERE id = $8
                  AND household_id = $9
                RETURNING id, household_id, created_by, title,
                          description, servings,
                          prep_time_minutes, cook_time_minutes,
                          source_url, image_url,
                          created_at, updated_at
                """,
                title,
                data.description.strip(),
                data.servings,
                data.prep_time_minutes,
                data.cook_time_minutes,
                data.source_url,
                data.image_url,
                recipe_id,
                household["id"],
            )

            if not recipe:
                raise HTTPException(
                    status_code=404,
                    detail="Recipe not found",
                )

            await connection.execute(
                """
                DELETE FROM recipe_ingredients
                WHERE recipe_id = $1
                """,
                recipe_id,
            )

            await connection.execute(
                """
                DELETE FROM recipe_instructions
                WHERE recipe_id = $1
                """,
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

            for number, instruction in enumerate(
                data.instructions,
                start=1,
            ):
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


@app.delete("/api/recipes/{recipe_id}")
async def delete_recipe(
    recipe_id: int,
    wilfordspace_session: str | None = Cookie(default=None),
):
    user = await get_current_user(wilfordspace_session)
    household = await get_user_household(user["id"])

    if not household:
        raise HTTPException(
            status_code=404,
            detail="Household not found",
        )

    connection = await get_connection()

    try:
        result = await connection.execute(
            """
            DELETE FROM recipes
            WHERE id = $1
              AND household_id = $2
            """,
            recipe_id,
            household["id"],
        )

        if result == "DELETE 0":
            raise HTTPException(
                status_code=404,
                detail="Recipe not found",
            )

        return {"message": "Recipe deleted"}
    finally:
        await connection.close()


def parse_duration_minutes(value):
    if not value:
        return None

    if isinstance(value, list):
        value = value[0] if value else None

    if not value:
        return None

    value = str(value).strip().upper()

    match = re.fullmatch(
        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?",
        value,
    )

    if match:
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)

        total = hours * 60 + minutes

        if seconds >= 30:
            total += 1

        return total or None

    hour_match = re.search(r"(\d+(?:\.\d+)?)\s*H", value)
    minute_match = re.search(r"(\d+)\s*M", value)

    if hour_match or minute_match:
        hours = float(hour_match.group(1)) if hour_match else 0
        minutes = int(minute_match.group(1)) if minute_match else 0
        return round(hours * 60 + minutes)

    number = re.search(r"\d+", value)

    return int(number.group()) if number else None


def normalize_image(value):
    if isinstance(value, str):
        return value.strip() or None

    if isinstance(value, list):
        for item in value:
            result = normalize_image(item)
            if result:
                return result

    if isinstance(value, dict):
        for key in ("url", "contentUrl", "thumbnailUrl"):
            result = normalize_image(value.get(key))
            if result:
                return result

    return None


def find_recipe_jsonld(value):
    if isinstance(value, dict):
        value_type = value.get("@type", [])

        if isinstance(value_type, str):
            value_type = [value_type]

        if any(
            str(item).lower() == "recipe"
            for item in value_type
        ):
            return value

        for key in ("@graph", "itemListElement", "mainEntity"):
            if key in value:
                found = find_recipe_jsonld(value[key])
                if found:
                    return found

    elif isinstance(value, list):
        for item in value:
            found = find_recipe_jsonld(item)
            if found:
                return found

    return None


def normalize_ingredients(value):
    if not isinstance(value, list):
        return []

    result = []

    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(
                {
                    "quantity": "",
                    "unit": "",
                    "name": item.strip(),
                }
            )

    return result


def normalize_instructions(value):
    if isinstance(value, str):
        value = [value]

    if not isinstance(value, list):
        return []

    result = []

    for item in value:
        if isinstance(item, str):
            instruction = item.strip()
        elif isinstance(item, dict):
            instruction = str(
                item.get("text")
                or item.get("name")
                or ""
            ).strip()
        else:
            instruction = ""

        if instruction:
            result.append({"instruction": instruction})

    return result


def recipe_preview_from_html(
    html: str,
    source_url: str,
):
    soup = BeautifulSoup(html, "html.parser")

    for script in soup.find_all(
        "script",
        attrs={"type": "application/ld+json"},
    ):
        raw = script.string or script.get_text()

        if not raw.strip():
            continue

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        recipe = find_recipe_jsonld(data)

        if not recipe:
            continue

        servings = recipe.get("recipeYield", 4)

        if isinstance(servings, list):
            servings = servings[0] if servings else 4

        servings_match = re.search(
            r"\d+",
            str(servings),
        )

        servings = (
            int(servings_match.group())
            if servings_match
            else 4
        )

        image_url = normalize_image(recipe.get("image"))

        return {
            "title": str(recipe.get("name") or "").strip(),
            "description": str(
                recipe.get("description") or ""
            ).strip(),
            "servings": max(servings, 1),
            "prep_time_minutes": parse_duration_minutes(
                recipe.get("prepTime")
            ),
            "cook_time_minutes": parse_duration_minutes(
                recipe.get("cookTime")
            ),
            "source_url": source_url,
            "image_url": image_url,
            "ingredients": normalize_ingredients(
                recipe.get("recipeIngredient", [])
            ),
            "instructions": normalize_instructions(
                recipe.get("recipeInstructions", [])
            ),
        }

    return None


def validate_import_url(url: str) -> str:
    parsed = urlparse(url.strip())

    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(
            status_code=400,
            detail="Only HTTP and HTTPS URLs are supported",
        )

    if not parsed.hostname:
        raise HTTPException(
            status_code=400,
            detail="A valid hostname is required",
        )

    if parsed.username or parsed.password:
        raise HTTPException(
            status_code=400,
            detail="URLs containing login details are not allowed",
        )

    hostname = parsed.hostname.rstrip(".").lower()

    if hostname in {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
    }:
        raise HTTPException(
            status_code=400,
            detail="Local URLs are not allowed",
        )

    try:
        addresses = socket.getaddrinfo(
            hostname,
            parsed.port or (
                443 if parsed.scheme == "https" else 80
            ),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror:
        raise HTTPException(
            status_code=400,
            detail="The hostname could not be resolved",
        )

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])

        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise HTTPException(
                status_code=400,
                detail="Private or local network URLs are not allowed",
            )

    return parsed.geturl()


async def download_recipe_page(url: str):
    current_url = url

    timeout = httpx.Timeout(
        connect=5,
        read=10,
        write=5,
        pool=5,
    )

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        headers={
            "User-Agent": (
                "WilfordSpace Recipe Importer/0.2 "
                "(recipe preview only)"
            )
        },
    ) as client:
        for _ in range(4):
            current_url = validate_import_url(current_url)

            try:
                response = await client.get(current_url)
            except httpx.RequestError:
                raise HTTPException(
                    status_code=400,
                    detail="The recipe page could not be downloaded",
                )

            if response.status_code in {
                301,
                302,
                303,
                307,
                308,
            }:
                location = response.headers.get("location")

                if not location:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid redirect",
                    )

                current_url = urljoin(
                    current_url,
                    location,
                )
                continue

            if response.status_code >= 400:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "The recipe page returned HTTP "
                        f"{response.status_code}"
                    ),
                )

            content_type = response.headers.get(
                "content-type",
                "",
            ).lower()

            if "text/html" not in content_type:
                raise HTTPException(
                    status_code=400,
                    detail="The URL did not return an HTML page",
                )

            content_length = response.headers.get(
                "content-length"
            )

            if content_length:
                try:
                    if int(content_length) > 2_000_000:
                        raise HTTPException(
                            status_code=400,
                            detail="The recipe page is too large",
                        )
                except ValueError:
                    pass

            content = response.content

            if len(content) > 2_000_000:
                raise HTTPException(
                    status_code=400,
                    detail="The recipe page is too large",
                )

            return (
                content.decode(
                    response.encoding or "utf-8",
                    errors="replace",
                ),
                current_url,
            )

        raise HTTPException(
            status_code=400,
            detail="Too many redirects",
        )


@app.post("/api/recipes/import")
async def import_recipe(
    data: RecipeImportRequest,
    wilfordspace_session: str | None = Cookie(default=None),
):
    await get_current_user(wilfordspace_session)

    url = validate_import_url(data.url)
    html, final_url = await download_recipe_page(url)
    preview = recipe_preview_from_html(html, final_url)

    if not preview or not preview["title"]:
        raise HTTPException(
            status_code=422,
            detail=(
                "No structured recipe data was found on this page. "
                "AI extraction will be added later."
            ),
        )

    return {
        "preview": preview,
        "saved": False,
    }
