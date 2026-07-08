from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src import database
from src.agent import TaskMasterAgent
from src.tools import export_to_excel

app = FastAPI(title="AI Task Master API", version="1.0.0")


@app.on_event("startup")
def on_startup():
    database.init_db()


class ProcessRequest(BaseModel):
    text: str
    user_id: int = 1


class MoveRequest(BaseModel):
    quadrant: int


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/process")
def process(req: ProcessRequest):
    try:
        response = TaskMasterAgent.run_once(req.text, user_id=req.user_id)
        return {"response": response}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/matrix")
def get_matrix(user_id: int = 1):
    return database.read_matrix(user_id)


@app.get("/api/matrix/{quadrant}")
def get_quadrant(quadrant: int, user_id: int = 1):
    if quadrant not in range(1, 5):
        raise HTTPException(400, "Quadrant must be 1-4")
    data = database.read_matrix(user_id).get(quadrant, [])
    return {"quadrant": quadrant, "tasks": data}


@app.delete("/api/tasks/{task_id}")
def remove_task(task_id: int, user_id: int = 1):
    msg = database.delete_task_db(task_id, user_id)
    if "не найдена" in msg:
        raise HTTPException(404, msg)
    return {"message": msg}


@app.patch("/api/tasks/{task_id}/move")
def move_task_route(task_id: int, body: MoveRequest, user_id: int = 1):
    msg = database.move_task_db(task_id, body.quadrant, user_id)
    if "не найдена" in msg:
        raise HTTPException(404, msg)
    if "должен быть" in msg:
        raise HTTPException(400, msg)
    return {"message": msg}


@app.post("/api/export")
def export(user_id: int = 1):
    export_to_excel(user_id=user_id)
    return {"message": "Экспортировано в matrix.xlsx"}
