from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from src import database, history, memory, plans
from src.llm import YandexClient
from src.memory_models import ContextVersion, Message, PlanVersion, ProjectEvent, TaskProgress
from src.memory_schemas import (
    ContextOperation,
    CreateProject,
    MemorySummary,
    ProjectState,
    UpdateProject,
)
from src.plan_schemas import ChangeTask, PlanProposal, PlanTask, ProposePlan
from src.server import app


class Provider(YandexClient):
    def __init__(self, minutes=30):
        self.minutes = minutes

    async def count_tokens(self, messages):
        return memory.estimate_tokens(messages)

    async def complete(self, messages, **kwargs):
        return PlanProposal(
            summary="Plan",
            buffer_minutes=10,
            tasks=[
                PlanTask(
                    id="review",
                    title="Review",
                    estimate_minutes=self.minutes,
                    priority="must",
                    decision="keep",
                    done_when="Works",
                    reason="Finish",
                )
            ],
        ).model_dump_json(), {}

    async def summarize(self, messages):
        return MemorySummary(goal="Ship")


def operation(revision):
    return ContextOperation(request_id=uuid4(), base_revision=revision)


async def create(title="Project"):
    return await memory.create_project(
        CreateProject(
            title=title,
            state=ProjectState(
                goal="Ship",
                success_criteria=["Works"],
                available_minutes=120,
                deadline="2030-11-01T18:00:00+03:00",
            ),
        )
    )


async def populated(title):
    p = await create(title)
    result = await plans.propose(
        p["id"],
        ProposePlan(**operation(0).model_dump(), text="Consider these requirements. " * 150),
        Provider(),
    )
    await plans.accept(p["id"], result["plan"]["id"], operation(1))
    await plans.change_task(p["id"], "review", ChangeTask(**operation(2).model_dump(), done=True))
    await memory.compress_context(p["id"], str(uuid4()), 3, Provider())
    return await memory.get_project(p["id"])


async def counts(pid):
    async with database._get_session_maker()() as session:
        return [
            await session.scalar(
                select(func.count()).select_from(model).where(model.project_id == pid)
            )
            for model in (Message, ContextVersion, PlanVersion, TaskProgress, ProjectEvent)
        ]


async def test_delete_removes_only_target_project_and_all_its_children():
    target, other = await populated("Delete"), await populated("Keep")
    other_counts = await counts(other["id"])
    assert all(number > 0 for number in await counts(target["id"]))
    async with database._get_session_maker()() as session:
        session.add(database.Task(title="Legacy stays", quadrant=2))
        await session.commit()
    with pytest.raises(memory.MemoryError) as error:
        await memory.delete_project(target["id"], 0)
    assert error.value.status == 409
    assert all(number > 0 for number in await counts(target["id"]))
    await memory.delete_project(target["id"], target["revision"])
    assert await counts(target["id"]) == [0] * 5
    assert await counts(other["id"]) == other_counts
    assert (await memory.list_projects()) == [other]
    async with database._get_session_maker()() as session:
        assert (await session.scalar(select(database.Task))).title == "Legacy stays"
    with pytest.raises(memory.MemoryError) as error:
        await memory.get_project(target["id"])
    assert error.value.status == 404


async def test_rename_preserves_state_and_does_not_invalidate_pending_plan():
    p = await create()
    result = await plans.propose(
        p["id"], ProposePlan(**operation(0).model_dump(), text="Plan"), Provider()
    )
    await plans.accept(p["id"], result["plan"]["id"], operation(1))
    draft = await plans.propose(
        p["id"], ProposePlan(**operation(2).model_dump(), text="Change estimate"), Provider(40)
    )
    before_messages = await memory.list_messages(p["id"])
    renamed = await memory.update_project(p["id"], UpdateProject(base_revision=3, title="New name"))
    assert renamed["state"] == p["state"]
    assert renamed["title"] == "New name"
    assert await memory.list_messages(p["id"]) == before_messages
    workspace = await plans.workspace(p["id"])
    assert not workspace["draft_stale"]
    assert workspace["draft"]["base_revision"] == 2
    assert (await history.page(p["id"]))["items"][0]["title"] == "Проект переименован"
    await plans.accept(p["id"], draft["plan"]["id"], operation(4))
    with pytest.raises(memory.MemoryError) as error:
        await memory.update_project(p["id"], UpdateProject(base_revision=3, title="Stale"))
    assert error.value.status == 409


async def test_project_management_api_validates_input_and_handles_last_project():
    p = await create()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as api:
        url = f"/api/projects/{p['id']}"
        for payload in ({"base_revision": 0}, {"base_revision": 0, "title": "   "}):
            assert (await api.patch(url, json=payload)).status_code == 422
        renamed = await api.patch(url, json={"base_revision": 0, "title": "  Renamed  "})
        assert renamed.status_code == 200
        assert renamed.json()["title"] == "Renamed"
        assert renamed.json()["state"] == p["state"]
        assert (await api.request("DELETE", url, json={"base_revision": 0})).status_code == 409
        removed = await api.request("DELETE", url, json={"base_revision": 1})
        assert removed.status_code == 200 and removed.json()["deleted"]
        assert (await api.get("/api/projects")).json() == []
        assert (await api.get(url + "/workspace")).status_code == 404
        assert (await api.request("DELETE", url, json={"base_revision": 1})).status_code == 404


async def test_pending_model_reply_cannot_recreate_deleted_project():
    p = await create()

    class DeleteDuringReply(Provider):
        async def complete(self, messages, **kwargs):
            await memory.delete_project(p["id"], 0)
            return await super().complete(messages, **kwargs)

    with pytest.raises(memory.MemoryError):
        await plans.propose(
            p["id"], ProposePlan(**operation(0).model_dump(), text="Plan"), DeleteDuringReply()
        )
    assert await memory.list_projects() == []
    assert await counts(p["id"]) == [0] * 5
