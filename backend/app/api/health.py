from fastapi import APIRouter

router = APIRouter(tags=["health"])

@router.get("/system/health")
def health() -> dict[str, str]:
    # TODO: add DB/Redis dependency checks without exposing secrets.
    return {"status": "ok", "service": "api", "dependencies": "not_checked"}
