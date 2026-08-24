import os

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="WilfordSpace API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "application": "WilfordSpace",
        "message": "WilfordSpace API is running",
    }


@app.get("/api/health")
async def health():
    database_url = os.getenv("DATABASE_URL")

    result = {
        "status": "ok",
        "application": "WilfordSpace",
        "database": "not checked",
    }

    if database_url:
        try:
            connection = await asyncpg.connect(database_url)
            await connection.execute("SELECT 1")
            await connection.close()
            result["database"] = "connected"
        except Exception:
            result["status"] = "degraded"
            result["database"] = "unavailable"

    return result
