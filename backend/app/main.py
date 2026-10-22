from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app.api import auth, content, costs, review, system
from app.core.config import settings
from app.core.errors import install_error_handling
from app.core.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_for_runtime()
    configure_logging(settings.log_level)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Nhìn Hình Đoán Chuyện API", version="0.1.0", lifespan=lifespan)
    install_error_handling(app)
    api = APIRouter(prefix="/api")
    api.include_router(auth.router)
    api.include_router(system.router)
    api.include_router(content.router)
    api.include_router(review.router)
    api.include_router(costs.router)
    app.include_router(api)

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {"service": "nhin-hinh-doan-chuyen", "status": "scaffold"}

    return app


app = create_app()
