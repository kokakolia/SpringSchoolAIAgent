import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from src import database, memory, plans
from src.config import settings
from src.llm import ModelUnavailable
from src.memory_models import ModelOperation, utcnow
from src.memory_schemas import CreateProject, ProjectState, SendMessage
from src.model_operations import claim, release
from src.plan_schemas import ProposePlan
from tests.test_memory import FakeClient
from tests.test_plans import proposal, state


@pytest.mark.parametrize("kind", ["message", "proposal", "compression"])
async def test_concurrent_duplicates_do_not_start_more_model_calls(kind):
    project = await memory.create_project(
        CreateProject(title="Concurrent", state=ProjectState.model_validate(state()))
    )
    pid = project["id"]
    revision = 0
    if kind == "compression":
        await memory.send_message(
            pid,
            SendMessage(request_id=uuid4(), base_revision=0, text="Keep the API. " * 200),
            FakeClient(),
        )
        revision = 1
    entered, finish = asyncio.Event(), asyncio.Event()

    class Blocking(FakeClient):
        calls_started = 0

        async def complete(self, messages, *, json_mode=False):
            self.calls_started += 1
            entered.set()
            await finish.wait()
            return (proposal().model_dump_json() if json_mode else "Done"), {}

        async def summarize(self, messages):
            self.calls_started += 1
            entered.set()
            await finish.wait()
            return self.summary

    client = Blocking()
    request_id = uuid4()

    async def invoke():
        body = dict(request_id=request_id, base_revision=revision, text="Plan")
        if kind == "message":
            return await memory.send_message(pid, SendMessage(**body), client)
        if kind == "proposal":
            return await plans.propose(pid, ProposePlan(**body), client)
        return await memory.compress_context(pid, str(request_id), revision, client)

    first = asyncio.create_task(invoke())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        duplicates = await asyncio.gather(*(invoke() for _ in range(5)), return_exceptions=True)
        assert all(isinstance(result, memory.MemoryError) for result in duplicates)
        assert client.calls_started == 1
    finally:
        finish.set()
        await first
    assert (await invoke())["replayed"]
    assert client.calls_started == 1


async def test_model_failure_releases_reservation_and_preserves_state():
    p = await memory.create_project(CreateProject(title="Failure", state=ProjectState(goal="Ship")))
    request = SendMessage(request_id=uuid4(), base_revision=0, text="Hello")

    class Broken(FakeClient):
        async def complete(self, messages, **kwargs):
            raise ModelUnavailable("Unavailable")

    with pytest.raises(ModelUnavailable):
        await memory.send_message(p["id"], request, Broken())
    assert await memory.get_project(p["id"]) == p
    assert await memory.list_messages(p["id"]) == []
    await memory.send_message(p["id"], request, FakeClient())


async def test_timeout_and_cancellation_release_the_lease(monkeypatch):
    p = await memory.create_project(CreateProject(title="Timeout", state=ProjectState(goal="Ship")))
    entered = asyncio.Event()

    class Slow(FakeClient):
        async def complete(self, messages, **kwargs):
            entered.set()
            await asyncio.Event().wait()

    request = SendMessage(request_id=uuid4(), base_revision=0, text="Hello")
    monkeypatch.setattr(settings, "model_operation_timeout_seconds", 0.05)
    with pytest.raises(ModelUnavailable, match="не успел"):
        await memory.send_message(p["id"], request, Slow())
    monkeypatch.setattr(settings, "model_operation_timeout_seconds", 180)
    entered.clear()
    task = asyncio.create_task(memory.send_message(p["id"], request, Slow()))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await memory.send_message(p["id"], request, FakeClient())


async def test_expired_worker_lease_can_be_reclaimed_without_old_owner_releasing_it():
    p = await memory.create_project(CreateProject(title="Lease", state=ProjectState(goal="Ship")))
    async with database._get_session_maker()() as session:
        session.add(
            ModelOperation(
                project_id=p["id"], owner="old", expires_at=utcnow() - timedelta(seconds=1)
            )
        )
        await session.commit()
    owner = await claim(p["id"])
    await release(p["id"], "old")
    with pytest.raises(memory.MemoryError):
        await claim(p["id"])
    await release(p["id"], owner)
    async with database._get_session_maker()() as session:
        assert await session.scalar(select(ModelOperation)) is None
