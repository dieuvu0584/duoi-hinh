from fastapi import FastAPI

from app.api.health import router as health_router

app = FastAPI(title="Nhìn Hình Đoán Chuyện API", version="0.1.0")
app.include_router(health_router, prefix="/api")

@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"service": "nhin-hinh-doan-chuyen", "status": "scaffold"}
