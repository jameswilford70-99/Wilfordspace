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
