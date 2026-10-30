from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from src.memory_schemas import ContextOperation, NonEmpty, StrictModel


class WorkWindow(StrictModel):
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def valid_interval(self):
        if self.start.utcoffset() is None or self.end.utcoffset() is None:
            raise ValueError("Укажите часовой пояс рабочих интервалов")
        if self.end <= self.start:
            raise ValueError("Конец рабочего интервала должен быть позже начала")
        return self


class PlanTask(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,47}$")
    title: str = Field(min_length=1, max_length=200)
    estimate_minutes: int = Field(ge=1, le=525600)
    priority: Literal["must", "should", "could"]
    decision: Literal["keep", "simplify", "defer"]
    depends_on: list[str] = Field(default_factory=list, max_length=30)
    done_when: NonEmpty
    reason: NonEmpty
    tradeoff: str = Field(default="", max_length=2000)
    constraint_refs: list[int] = Field(default_factory=list, max_length=50)
    requirement_refs: list[dict] = Field(
        default_factory=list,
        max_length=50,
        description="Объекты {id: string, version: integer}; у выполненной задачи версия исходная",
    )

    @field_validator("requirement_refs")
    @classmethod
    def valid_requirement_refs(cls, refs):
        for ref in refs:
            if (
                set(ref) != {"id", "version"}
                or not isinstance(ref["id"], str)
                or not 1 <= len(ref["id"]) <= 36
                or type(ref["version"]) is not int
                or ref["version"] < 1
            ):
                raise ValueError("Ссылка на требование должна содержать id и положительную version")
        return refs


class PlanProposal(StrictModel):
    summary: NonEmpty
    tasks: list[PlanTask] = Field(default_factory=list, max_length=30)
    archive_task_ids: list[str] = Field(default_factory=list, max_length=30)
    buffer_minutes: int = Field(ge=0, le=525600)
    assumptions: list[NonEmpty] = Field(default_factory=list, max_length=30)
    questions: list[NonEmpty] = Field(default_factory=list, max_length=20)
    risks: list[NonEmpty] = Field(default_factory=list, max_length=30)


class ProposePlan(ContextOperation):
    text: str = Field(min_length=1, max_length=8000)


class LoseTime(ContextOperation):
    minutes: int = Field(ge=1, le=525600)


class ChangeTask(ContextOperation):
    done: bool | None = None
    pinned: bool | None = None
    actual_minutes: int | None = Field(default=None, ge=0, le=525600)

    @model_validator(mode="after")
    def valid_operation(self):
        if self.done is None and self.pinned is None:
            raise ValueError("Укажите done или pinned")
        if self.actual_minutes is not None and self.done is not True:
            raise ValueError("Фактическое время указывается при завершении")
        return self
