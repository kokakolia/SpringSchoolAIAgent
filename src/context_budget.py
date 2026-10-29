"""Exact request budgets and compression staged atomically with the model's reply."""

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from src import memory
from src.config import settings
from src.llm import YandexClient
from src.memory_models import ContextVersion, Project, new_id
from src.memory_schemas import MemorySummary


@dataclass
class ContextGuard:
    context: list[dict]
    last_id: int
    summary: dict | None
    cutoff: int
    project_id: str
    state: dict
    parent: str | None
    request_id: str
    client: YandexClient
    versions: list[ContextVersion] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)

    @classmethod
    async def load(
        cls, session: AsyncSession, project: Project, request_id: str, client: YandexClient
    ) -> "ContextGuard":
        context, last_id, summary, cutoff = await memory.load_context(session, project)
        return cls(
            context,
            last_id,
            summary,
            cutoff,
            project.id,
            project.state,
            project.current_context_id,
            request_id,
            client,
        )

    async def prepare(self, messages: list[dict]) -> list[dict]:
        count = await self.client.count_tokens(messages)
        before = memory.metrics(messages, count)
        check = {"before": before, "compressed": False}
        self.checks.append(check)
        if before["compression_recommended"] and self.last_id > self.cutoff and not self.versions:
            summary = MemorySummary.model_validate(
                await self.client.summarize(self.context)
            ).model_dump()
            compact = memory.assemble(self.state, summary, [])
            # Preserve the augmented system instruction and all request-specific inputs.
            candidate = [messages[0]] + compact[1:] + messages[len(self.context) :]
            after_count = await self.client.count_tokens(candidate)
            if after_count >= count:
                raise memory.MemoryError(
                    "Сжатие не уменьшило контекст. Текущая версия сохранена", 422
                )
            before_id, after_id = new_id(), new_id()
            self.versions = [
                ContextVersion(
                    id=before_id,
                    project_id=self.project_id,
                    request_id=self.request_id,
                    parent_id=self.parent,
                    kind="before_compression",
                    messages=self.context,
                    summary=self.summary,
                    covered_message_id=self.cutoff,
                    estimated_tokens=memory.estimate_tokens(self.context),
                ),
                ContextVersion(
                    id=after_id,
                    project_id=self.project_id,
                    request_id=self.request_id,
                    parent_id=before_id,
                    kind="compressed",
                    messages=compact,
                    summary=summary,
                    covered_message_id=self.last_id,
                    estimated_tokens=memory.estimate_tokens(compact),
                ),
            ]
            messages = candidate
            count = after_count
            check.update(compressed=True, after=memory.metrics(messages, count))
        if count + settings.response_reserve_tokens > settings.context_budget_tokens:
            raise memory.MemoryError(
                "Запрос не помещается в бюджет даже после сжатия. "
                "Сократите описание или данные проекта",
                413,
            )
        return messages

    def persist(self, session: AsyncSession) -> dict:
        session.add_all(self.versions)
        return {"current_context_id": self.versions[-1].id} if self.versions else {}
