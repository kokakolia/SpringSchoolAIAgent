from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src import database
from src.agent import TaskMasterAgent
from src.project_routes import router as project_router
from src.tools import export_to_excel


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.init_db()
    try:
        yield
    finally:
        if database._async_engine is not None:
            await database._async_engine.dispose()
        database._async_engine = None
        database._async_session_maker = None


app = FastAPI(title="До дедлайна API", version="1.0.0", lifespan=lifespan)
app.include_router(project_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ProcessRequest(BaseModel):
    text: str
    user_id: int = 1


class MoveRequest(BaseModel):
    quadrant: int


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
    }


@app.post("/api/process")
async def process(req: ProcessRequest):
    try:
        response = await TaskMasterAgent.run_once(req.text, user_id=req.user_id)
        return {"response": response}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/matrix")
async def get_matrix(user_id: int = 1):
    return await database.read_matrix(user_id)


@app.get("/api/matrix/{quadrant}")
async def get_quadrant(quadrant: int, user_id: int = 1):
    if quadrant not in range(1, 5):
        raise HTTPException(400, "Quadrant must be 1-4")
    data = await database.read_matrix(user_id)
    return {"quadrant": quadrant, "tasks": data.get(quadrant, [])}


@app.delete("/api/tasks/{task_id}")
async def remove_task(task_id: int, user_id: int = 1):
    msg = await database.delete_task_db(task_id, user_id)
    if "не найдена" in msg:
        raise HTTPException(404, msg)
    return {"message": msg}


@app.patch("/api/tasks/{task_id}/move")
async def move_task_route(task_id: int, body: MoveRequest, user_id: int = 1):
    msg = await database.move_task_db(task_id, body.quadrant, user_id)
    if "не найдена" in msg:
        raise HTTPException(404, msg)
    if "должен быть" in msg:
        raise HTTPException(400, msg)
    return {"message": msg}


@app.post("/api/export")
async def export(user_id: int = 1):
    await export_to_excel(user_id=user_id)
    return {"message": "Экспортировано в matrix.xlsx"}


frontend = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if frontend.is_dir():
    app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
