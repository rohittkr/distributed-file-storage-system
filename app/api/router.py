from fastapi import APIRouter

api_router = APIRouter()

@api_router.get("/version")
def version() -> dict[str, str]:
    return {"version": "0.1.0", "status": "foundation"}
