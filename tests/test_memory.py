from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from src import database, memory
from src.config import settings
from src.llm import ModelUnavailable, YandexClient
from src.memory_schemas import (
    CreateProject,
    MemorySummary,
    ProjectState,
    SendMessage,
    UpdateProject,
)
from src.server import app


class FakeClient(YandexClient):
    async def count_tokens(self, messages):
        return memory.estimate_tokens(messages)

    def __init__(self):
        self.calls = []
        self.summary_calls = 0
        self.summary = MemorySummary(
            goal="Защитить проект",
            confirmed_constraints=["Шесть часов"],
            user_objections=["Анимации обязательны"],
            open_questions=["Сколько займут тесты?"],
        )

    async def complete(self, messages, *, json_mode=False):
        self.calls.append(messages)
        return "Предлагаю сохранить анимации и сократить остальные улучшения.", {
            "inputTextTokens": "120",
            "completionTokens": "30",
        }

    async def summarize(self, messages):
        self.summary_calls += 1
        return self.summary


async def project(goal="Защитить проект"):
    return await memory.create_project(
        CreateProject(
            title="Защита",
            state=ProjectState(
                goal=goal, available_minutes=360, constraints=["Анимации обязательны"]
            ),
        )
    )


def turn(revision, text="Анимации обязательны. " * 100, request_id=None):
    return SendMessage(request_id=request_id or uuid4(), base_revision=revision, text=text)


async def populated():
    p = await project()
    client = FakeClient()
    await memory.send_message(p["id"], turn(0), client)
    return p["id"], client


async def test_dialogue_survives_engine_restart_and_isolates_projects():
    pid, _ = await populated()
    other = await project("Другой проект")
    await database._async_engine.dispose()
    database._async_engine = None
    database._async_session_maker = None
    client = FakeClient()
    await memory.send_message(pid, turn(1, "Сохрани мои требования"), client)
    context = client.calls[0]
    assert any("Анимации обязательны" in m["text"] for m in context)
    assert context[-1]["text"] == "Сохрани мои требования"
    assert len(await memory.list_messages(pid)) == 4
    assert await memory.list_messages(other["id"]) == []


async def test_compress_preserves_originals_and_reuses_request():
    pid, client = await populated()
    original = await memory.list_messages(pid)
    request_id = str(uuid4())
    result = await memory.compress_context(pid, request_id, 1, client)
    versions = await memory.list_versions(pid)
    assert len(versions) == 2
    before = next(v for v in versions if v["kind"] == "before_compression")
    after = result["context"]
    assert after["parent_id"] == before["id"]
    assert after["estimated_tokens"] < before["estimated_tokens"]
    assert after["summary"]["user_objections"] == ["Анимации обязательны"]
    assert await memory.list_messages(pid) == original
    current = await memory.get_context(pid)
    assert current["current_context_id"] == after["id"]
    assert len(current["messages"]) == 2
    replay = await memory.compress_context(pid, request_id, 1, client)
    assert replay["replayed"]
    assert client.summary_calls == 1
    assert len(await memory.list_versions(pid)) == 2
    await memory.send_message(pid, turn(2, "Добавилось требование: нужен экспорт"), client)
    assert "Краткая память" in client.calls[-1][1]["text"]
    assert client.calls[-1][-1]["text"] == "Добавилось требование: нужен экспорт"


async def test_restore_keeps_new_messages_and_current_canonical_facts():
    pid, client = await populated()
    await memory.compress_context(pid, str(uuid4()), 1, client)
    before = next(v for v in await memory.list_versions(pid) if v["kind"] == "before_compression")
    await memory.send_message(pid, turn(2, "Новое возражение"), client)
    await memory.update_project(
        pid,
        UpdateProject(
            base_revision=3,
            state=ProjectState(
                goal="Защитить проект",
                available_minutes=240,
                constraints=["Нельзя убирать анимации"],
            ),
        ),
    )
    restored = await memory.restore_context(pid, before["id"], str(uuid4()), 4)
    context = await memory.get_context(pid)
    assert restored["context"]["kind"] == "restored"
    assert "240" in context["messages"][0]["text"]
    assert "Нельзя убирать анимации" in context["messages"][0]["text"]
    assert any(m["text"] == "Новое возражение" for m in context["messages"])
    assert len(await memory.list_messages(pid)) == 4


async def test_failed_or_larger_summary_does_not_replace_context():
    pid, client = await populated()
    old = await memory.get_context(pid)

    class Broken(FakeClient):
        async def summarize(self, messages):
            raise ModelUnavailable("Временный сбой")

    with pytest.raises(ModelUnavailable):
        await memory.compress_context(pid, str(uuid4()), 1, Broken())
    client.summary.decisions = ["Длинный текст " * 150] * 20
    with pytest.raises(memory.MemoryError, match="не уменьшило"):
        await memory.compress_context(pid, str(uuid4()), 1, client)
    assert await memory.get_context(pid) == old
    assert await memory.list_versions(pid) == []


async def test_concurrent_edit_rejects_stale_compression_without_orphan_snapshots():
    pid, _ = await populated()

    class Concurrent(FakeClient):
        async def summarize(self, messages):
            await memory.update_project(
                pid,
                UpdateProject(
                    base_revision=1,
                    state=ProjectState(goal="Обновлённая цель", available_minutes=120),
                ),
            )
            return self.summary

    with pytest.raises(memory.MemoryError, match="Проект изменился"):
        await memory.compress_context(pid, str(uuid4()), 1, Concurrent())
    assert await memory.list_versions(pid) == []
    assert (await memory.get_project(pid))["state"]["goal"] == "Обновлённая цель"


async def test_turn_idempotency_and_stale_request():
    p = await project()
    client = FakeClient()
    request = turn(0)
    await memory.send_message(p["id"], request, client)
    assert (await memory.send_message(p["id"], request, client))["replayed"]
    assert len(client.calls) == 1
    with pytest.raises(memory.MemoryError, match="уже использован"):
        await memory.send_message(p["id"], turn(1, "Иное сообщение", request.request_id), client)
    with pytest.raises(memory.MemoryError, match="Проект изменился"):
        await memory.send_message(p["id"], turn(0), client)
    assert len(await memory.list_messages(p["id"])) == 2


async def test_restore_cannot_read_another_projects_version():
    pid, client = await populated()
    compressed = await memory.compress_context(pid, str(uuid4()), 1, client)
    other = await project()
    with pytest.raises(memory.MemoryError) as error:
        await memory.restore_context(other["id"], compressed["context"]["id"], str(uuid4()), 0)
    assert error.value.status == 404


async def test_budget_blocks_generation_but_keeps_dialogue(monkeypatch):
    p = await project()
    client = FakeClient()
    monkeypatch.setattr(settings, "context_budget_tokens", 1000)
    with pytest.raises(memory.MemoryError) as error:
        await memory.send_message(p["id"], turn(0), client)
    assert error.value.status == 413
    assert client.calls == []
    assert await memory.list_messages(p["id"]) == []


async def test_compress_command_is_not_stored_as_dialogue():
    pid, client = await populated()
    result = await memory.send_message(pid, turn(1, "/compress"), client)
    assert result["context"]["kind"] == "compressed"
    assert len(await memory.list_messages(pid)) == 2


async def test_api_validation_errors_metrics_and_provider_failures(monkeypatch):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as api:
        response = await api.post(
            "/api/projects",
            json={
                "title": "Защита",
                "state": {"goal": "Показать проект", "available_minutes": 360},
            },
        )
        assert response.status_code == 201
        pid = response.json()["id"]
        context = await api.get(f"/api/projects/{pid}/context")
        assert context.json()["usage"]["count_method"] == "estimated_utf8"
        response = await api.patch(
            f"/api/projects/{pid}", json={"base_revision": 9, "state": {"goal": "Иная цель"}}
        )
        assert response.status_code == 409
        response = await api.post(
            f"/api/projects/{pid}/messages",
            json={"request_id": str(uuid4()), "base_revision": 0, "text": "   "},
        )
        assert response.status_code == 422
        monkeypatch.setattr(settings, "yandex_api_key", "")
        response = await api.post(
            f"/api/projects/{pid}/messages",
            json={"request_id": str(uuid4()), "base_revision": 0, "text": "Привет"},
        )
        assert response.status_code == 503
        assert (await api.get(f"/api/projects/{pid}/messages")).json() == []
        missing = await api.get(f"/api/projects/{uuid4()}")
        assert missing.status_code == 404


def test_deadline_requires_timezone():
    with pytest.raises(ValidationError):
        ProjectState(goal="Цель", deadline="2026-11-01T12:00:00")
    with pytest.raises(ValidationError):
        ProjectState(goal="Цель", timezone="Invalid/Zone")


async def test_second_compression_uses_previous_summary_and_new_objections():
    pid, client = await populated()
    first = await memory.compress_context(pid, str(uuid4()), 1, client)
    await memory.send_message(pid, turn(2, "Ещё одно обязательное условие. " * 100), client)

    class Observer(FakeClient):
        async def summarize(self, messages):
            assert "Краткая память" in messages[1]["text"]
            assert "Анимации обязательны" in messages[1]["text"]
            assert "Ещё одно обязательное условие" in messages[2]["text"]
            return self.summary

    second = await memory.compress_context(pid, str(uuid4()), 3, Observer())
    assert second["context"]["id"] != first["context"]["id"]
    assert len(await memory.list_messages(pid)) == 4
    assert len(await memory.list_versions(pid)) == 4


async def test_concurrent_edit_does_not_commit_stale_agent_response():
    p = await project()

    class Concurrent(FakeClient):
        async def complete(self, messages, *, json_mode=False):
            await memory.update_project(
                p["id"], UpdateProject(base_revision=0, state=ProjectState(goal="Новая цель"))
            )
            return "Устаревший ответ", {}

    with pytest.raises(memory.MemoryError, match="Проект изменился"):
        await memory.send_message(p["id"], turn(0), Concurrent())
    assert await memory.list_messages(p["id"]) == []
