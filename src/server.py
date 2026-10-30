from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from src import database
from src.access import AccessMiddleware
from src.project_routes import router as project_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await database.init_db()
        yield
    finally:
        await database.close_db()


app = FastAPI(title="Мой план лучше API", version="1.0.0", lifespan=lifespan)
app.include_router(project_router)

app.add_middleware(AccessMiddleware)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/runtime")
async def runtime():
    from src.config import settings

    return {
        "service": "deadline",
        "mode": "live",
        "model_configured": bool(settings.yandex_api_key and settings.yandex_folder_id),
        "authentication_required": bool(settings.app_password.get_secret_value()),
    }


@app.post("/api/process", deprecated=True)
async def process():
    raise HTTPException(
        410, "Старый агент отключён. Используйте /api/projects и интерфейс проектов"
    )


frontend = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if frontend.is_dir():
    app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
