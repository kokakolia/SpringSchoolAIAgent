"""Synthetic token counts and replies: these tests cannot consume cloud credits."""

import json
from uuid import uuid4

import pytest

from src import memory, plans
from src.config import settings
from src.llm import ModelUnavailable, YandexClient
from src.memory_schemas import (
    CreateProject,
    MemorySummary,
    ProjectState,
    SendMessage,
    UpdateProject,
)
from src.plan_schemas import ProposePlan


class Client(YandexClient):
    def __init__(self):
        self.counts = []
        self.answers = []
        self.summaries = 0
        self.fail_reply = False

    async def count_tokens(self, messages):
        self.counts.append(messages)
        return len(json.dumps(messages, ensure_ascii=False)) // 4

    async def summarize(self, messages):
        self.summaries += 1
        return MemorySummary(goal="Ship", user_objections=["Keep animation"])

    async def complete(self, messages, *, json_mode=False):
        self.answers.append(messages)
        if self.fail_reply:
            raise ModelUnavailable("Synthetic failure")
        if json_mode:
            return json.dumps(
                {
                    "summary": "Plan",
                    "questions": ["Confirm deadline"],
                    "tasks": [
                        {
                            "id": "ship",
                            "title": "Ship",
                            "estimate_minutes": 30,
                            "priority": "must",
                            "decision": "keep",
                            "done_when": "Works",
                            "reason": "Finish",
                        }
                    ],
                    "buffer_minutes": 0,
                }
            ), {}
        return "Keep animation", {}


def turn(revision, text):
    return SendMessage(request_id=uuid4(), base_revision=revision, text=text)


async def prepared(monkeypatch):
    monkeypatch.setattr(settings, "context_budget_tokens", 50000)
    monkeypatch.setattr(settings, "response_reserve_tokens", 100)
    p = await memory.create_project(CreateProject(title="Ship", state=ProjectState(goal="Ship")))
    client = Client()
    await memory.send_message(p["id"], turn(0, "Keep animation. " * 600), client)
    context = (await memory.get_context(p["id"]))["messages"]
    size = await client.count_tokens(context + [{"role": "user", "text": "Continue"}])
    monkeypatch.setattr(settings, "context_budget_tokens", int((size + 100) / 0.8))
    return p["id"], client


async def test_threshold_compresses_before_reply_and_commits_one_revision(monkeypatch):
    pid, client = await prepared(monkeypatch)
    body = turn(1, "Continue")
    result = await memory.send_message(pid, body, client)
    assert result["revision"] == 2
    assert result["usage"]["context_checks"][0]["compressed"]
    assert result["usage"]["context_checks"][0]["after"]["budget_fraction"] < 0.75
    assert client.answers[-1][-1]["text"] == "Continue"
    assert "Keep animation" in client.answers[-1][1]["text"]
    versions = await memory.list_versions(pid)
    assert len(versions) == 2
    before = next(v for v in versions if v["kind"] == "before_compression")
    snapshot = await memory.get_version(pid, before["id"])
    assert snapshot["messages"][1]["text"] == ("Keep animation. " * 600).strip()
    assert len(await memory.list_messages(pid)) == 4
    replay = await memory.send_message(pid, body, client)
    assert replay["replayed"] and client.summaries == 1
    await memory.send_message(pid, turn(2, "One more"), client)
    assert client.summaries == 1


async def test_failure_rolls_back_staged_compression(monkeypatch):
    pid, client = await prepared(monkeypatch)
    client.fail_reply = True
    with pytest.raises(ModelUnavailable):
        await memory.send_message(pid, turn(1, "Continue"), client)
    assert (await memory.get_project(pid))["revision"] == 1
    assert await memory.list_versions(pid) == []
    assert len(await memory.list_messages(pid)) == 2


async def test_concurrent_edit_wins_over_automatic_compression(monkeypatch):
    pid, client = await prepared(monkeypatch)
    summarize = client.summarize

    async def concurrent(messages):
        await memory.update_project(
            pid, UpdateProject(base_revision=1, state=ProjectState(goal="Changed by user"))
        )
        return await summarize(messages)

    client.summarize = concurrent
    with pytest.raises(memory.MemoryError, match="Проект изменился"):
        await memory.send_message(pid, turn(1, "Continue"), client)
    assert (await memory.get_project(pid))["state"]["goal"] == "Changed by user"
    assert await memory.list_versions(pid) == []
    assert len(await memory.list_messages(pid)) == 2


async def test_full_planning_request_keeps_schema_evidence_and_user_input(monkeypatch):
    pid, client = await prepared(monkeypatch)
    result = await plans.propose(
        pid,
        ProposePlan(request_id=uuid4(), base_revision=1, text="Preserve the mandatory animation"),
        client,
    )
    assert result["revision"] == 2
    assert client.summaries == 1
    sent = client.answers[-1]
    assert "constraint_refs" in sent[0]["text"]
    assert "numbered_constraints" in sent[-2]["text"]
    assert sent[-1]["text"] == "Preserve the mandatory animation"
    assert len(await memory.list_versions(pid)) == 2
    assert result["plan"]["base_revision"] == 1


async def test_oversized_fixed_inputs_block_generation_without_truncation(monkeypatch):
    monkeypatch.setattr(settings, "context_budget_tokens", 1000)
    monkeypatch.setattr(settings, "response_reserve_tokens", 100)
    p = await memory.create_project(CreateProject(title="Ship", state=ProjectState(goal="Ship")))
    client = Client()
    with pytest.raises(memory.MemoryError) as error:
        await memory.send_message(p["id"], turn(0, "x" * 10000), client)
    assert error.value.status == 413
    assert client.answers == [] and client.summaries == 0
    assert await memory.list_messages(p["id"]) == []
