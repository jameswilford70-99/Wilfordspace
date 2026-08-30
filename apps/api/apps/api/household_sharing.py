from typing import Optional

from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel, EmailStr


router = APIRouter(
    prefix="/api/household",
    tags=["household sharing"],
)


class AddMemberRequest(BaseModel):
    email: EmailStr


async def get_connection():
    from main import get_connection

    return await get_connection()


async def get_authenticated_user(
    wilfordspace_session: str | None,
):
    from main import get_current_user

    if not wilfordspace_session:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    user = await get_current_user(wilfordspace_session)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    return user


async def get_user_household(
    user_id: int,
):
    connection = await get_connection()

    try:
        household = await connection.fetchrow(
            """
            SELECT
                h.id,
                h.name,
                h.owner_id,
                hm.role
            FROM households h
            JOIN household_members hm
                ON hm.household_id = h.id
            WHERE hm.user_id = $1
            ORDER BY h.id
            LIMIT 1
            """,
            user_id,
        )
    finally:
        await connection.close()

    if not household:
        raise HTTPException(
            status_code=404,
            detail="Household not found",
        )

    return household


async def require_owner(
    wilfordspace_session: str | None,
):
    user = await get_authenticated_user(
        wilfordspace_session
    )

    household = await get_user_household(user["id"])

    if (
        household["role"] != "owner"
        and household["owner_id"] != user["id"]
    ):
        raise HTTPException(
            status_code=403,
            detail="Only the household owner can manage members",
        )

    return user, household


@router.get("/members")
async def list_household_members(
    wilfordspace_session: str | None = Cookie(default=None),
):
    user = await get_authenticated_user(
        wilfordspace_session
    )

    household = await get_user_household(user["id"])

    connection = await get_connection()

    try:
        members = await connection.fetch(
            """
            SELECT
                u.id AS user_id,
                u.name,
                u.email,
                hm.role,
                hm.joined_at
            FROM household_members hm
            JOIN users u
                ON u.id = hm.user_id
            WHERE hm.household_id = $1
            ORDER BY
                CASE
                    WHEN hm.role = 'owner' THEN 0
                    ELSE 1
                END,
                LOWER(u.name),
                LOWER(u.email)
            """,
            household["id"],
        )
    finally:
        await connection.close()

    return {
        "household": {
            "id": household["id"],
            "name": household["name"],
            "owner_id": household["owner_id"],
        },
        "members": [
            {
                "user_id": member["user_id"],
                "name": member["name"],
                "email": member["email"],
                "role": member["role"],
                "joined_at": member["joined_at"].isoformat()
                if member["joined_at"]
                else None,
            }
            for member in members
        ],
    }


@router.post("/members")
async def add_household_member(
    data: AddMemberRequest,
    wilfordspace_session: str | None = Cookie(default=None),
):
    current_user, household = await require_owner(
        wilfordspace_session
    )

    email = str(data.email).strip().lower()

    connection = await get_connection()

    try:
        invited_user = await connection.fetchrow(
            """
            SELECT
                id,
                name,
                email
            FROM users
            WHERE LOWER(email) = LOWER($1)
            LIMIT 1
            """,
            email,
        )

        if not invited_user:
            raise HTTPException(
                status_code=404,
                detail=(
                    "No WilfordSpace account exists for that email. "
                    "The person must register first."
                ),
            )

        if invited_user["id"] == current_user["id"]:
            raise HTTPException(
                status_code=400,
                detail="You are already the household owner.",
            )

        existing = await connection.fetchrow(
            """
            SELECT
                household_id,
                user_id,
                role
            FROM household_members
            WHERE household_id = $1
              AND user_id = $2
            """,
            household["id"],
            invited_user["id"],
        )

        if existing:
            raise HTTPException(
                status_code=409,
                detail="That user is already a member of this household.",
            )

        await connection.execute(
            """
            INSERT INTO household_members (
                household_id,
                user_id,
                role
            )
            VALUES ($1, $2, 'member')
            """,
            household["id"],
            invited_user["id"],
        )
    finally:
        await connection.close()

    return {
        "message": "Household member added",
        "member": {
            "user_id": invited_user["id"],
            "name": invited_user["name"],
            "email": invited_user["email"],
            "role": "member",
        },
    }


@router.delete("/members/{member_user_id}")
async def remove_household_member(
    member_user_id: int,
    wilfordspace_session: str | None = Cookie(default=None),
):
    current_user, household = await require_owner(
        wilfordspace_session
    )

    if member_user_id == household["owner_id"]:
        raise HTTPException(
            status_code=400,
            detail="The household owner cannot be removed.",
        )

    connection = await get_connection()

    try:
        removed = await connection.fetchrow(
            """
            DELETE FROM household_members
            WHERE household_id = $1
              AND user_id = $2
              AND user_id <> $3
            RETURNING user_id
            """,
            household["id"],
            member_user_id,
            current_user["id"],
        )
    finally:
        await connection.close()

    if not removed:
        raise HTTPException(
            status_code=404,
            detail="Household member not found.",
        )

    return {
        "message": "Household member removed",
        "user_id": removed["user_id"],
    }


@router.put("/members/{member_user_id}/role")
async def update_household_member_role(
    member_user_id: int,
    role: str,
    wilfordspace_session: str | None = Cookie(default=None),
):
    current_user, household = await require_owner(
        wilfordspace_session
    )

    if role not in {"owner", "member"}:
        raise HTTPException(
            status_code=400,
            detail="Role must be owner or member.",
        )

    if member_user_id == household["owner_id"]:
        raise HTTPException(
            status_code=400,
            detail="The household owner role cannot be changed here.",
        )

    if role == "owner":
        raise HTTPException(
            status_code=400,
            detail=(
                "Owner transfer is not available yet. "
                "The original owner remains the owner."
            ),
        )

    connection = await get_connection()

    try:
        updated = await connection.fetchrow(
            """
            UPDATE household_members
            SET role = $1
            WHERE household_id = $2
              AND user_id = $3
            RETURNING user_id, role
            """,
            role,
            household["id"],
            member_user_id,
        )
    finally:
        await connection.close()

    if not updated:
        raise HTTPException(
            status_code=404,
            detail="Household member not found.",
        )

    return {
        "message": "Household member role updated",
        "user_id": updated["user_id"],
        "role": updated["role"],
    }
