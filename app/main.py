from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import settings


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/live", tags=["system"])
def liveness() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/ready", tags=["system"])
def readiness() -> dict[str, str]:
    return {"status": "ready"}