"""Single-user project-memory API. Authentication is required before multi-user hosting."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.routing import APIRoute

from src import history, memory, plans
from src.llm import ModelUnavailable, YandexClient
from src.memory_schemas import (
    ContextOperation,
    CreateProject,
    DeleteProject,
    SendMessage,
    UpdateProject,
)
from src.plan_schemas import ChangeTask, LoseTime, ProposePlan


class MemoryRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                return await original(request)
            except memory.MemoryError as error:
                detail = (
                    {"message": error.detail, "code": error.code} if error.code else error.detail
                )
                raise HTTPException(error.status, detail) from error
            except ModelUnavailable as error:
                raise HTTPException(503, str(error)) from error

        return handler


router = APIRouter(prefix="/api/projects", tags=["Project memory"], route_class=MemoryRoute)


@router.post("", status_code=201)
async def create(body: CreateProject):
    return await memory.create_project(body)


@router.get("")
async def projects():
    return await memory.list_projects()


@router.get("/{project_id}")
async def project(project_id: UUID):
    return await memory.get_project(str(project_id))


@router.patch("/{project_id}")
async def edit(project_id: UUID, body: UpdateProject):
    return await memory.update_project(str(project_id), body)


@router.delete("/{project_id}")
async def remove(project_id: UUID, body: DeleteProject):
    return await memory.delete_project(str(project_id), body.base_revision)


@router.get("/{project_id}/messages")
async def messages(
    project_id: UUID,
    limit: int = Query(50, ge=1, le=100),
    before_id: int | None = Query(None, ge=1),
):
    return await memory.message_page(str(project_id), limit, before_id)


@router.post("/{project_id}/messages")
async def message(project_id: UUID, body: SendMessage):
    return await memory.send_message(str(project_id), body, YandexClient())


@router.get("/{project_id}/context")
async def context(project_id: UUID, exact: bool = False):
    return await memory.get_context(str(project_id), YandexClient() if exact else None)


@router.post("/{project_id}/context/compress")
async def compress(project_id: UUID, body: ContextOperation):
    return await memory.compress_context(
        str(project_id), str(body.request_id), body.base_revision, YandexClient()
    )


@router.get("/{project_id}/context/versions")
async def versions(project_id: UUID):
    return await memory.list_versions(str(project_id))


@router.get("/{project_id}/context/versions/{version_id}")
async def version(project_id: UUID, version_id: UUID):
    return await memory.get_version(str(project_id), str(version_id))


@router.post("/{project_id}/context/versions/{version_id}/restore")
async def restore(project_id: UUID, version_id: UUID, body: ContextOperation):
    return await memory.restore_context(
        str(project_id), str(version_id), str(body.request_id), body.base_revision
    )


@router.get("/{project_id}/workspace")
async def project_workspace(project_id: UUID):
    return await plans.workspace(str(project_id))


@router.post("/{project_id}/plans/propose")
async def propose_plan(project_id: UUID, body: ProposePlan):
    return await plans.propose(str(project_id), body, YandexClient())


@router.post("/{project_id}/plans/{plan_id}/accept")
async def accept_plan(project_id: UUID, plan_id: UUID, body: ContextOperation):
    return await plans.accept(str(project_id), str(plan_id), body)


@router.post("/{project_id}/events/time-lost")
async def time_lost(project_id: UUID, body: LoseTime):
    return await plans.lose_time(str(project_id), body)


@router.patch("/{project_id}/tasks/{task_id}")
async def task_progress(project_id: UUID, task_id: str, body: ChangeTask):
    return await plans.change_task(str(project_id), task_id, body)


@router.get("/{project_id}/tasks/archive")
async def task_archive(
    project_id: UUID, offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100)
):
    return await plans.task_archive(str(project_id), offset, limit)


@router.get("/{project_id}/history")
async def project_history(
    project_id: UUID, offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100)
):
    return await history.page(str(project_id), offset, limit)


@router.get("/{project_id}/plans/{plan_id}")
async def plan_detail(project_id: UUID, plan_id: UUID):
    return await history.plan(str(project_id), str(plan_id))
