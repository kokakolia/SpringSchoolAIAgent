from datetime import datetime
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NonEmpty = Annotated[str, Field(min_length=1, max_length=2000)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectState(StrictModel):
    goal: NonEmpty
    success_criteria: list[NonEmpty] = Field(default_factory=list, max_length=50)
    constraints: list[NonEmpty] = Field(default_factory=list, max_length=50)
    available_minutes: int | None = Field(default=None, ge=0, le=525600)
    deadline: datetime | None = None
    timezone: str = "Europe/Istanbul"
    work_windows: list[dict] = Field(default_factory=list, max_length=100)

    @field_validator("work_windows")
    @classmethod
    def validate_work_windows(cls, value):
        # Local import avoids a schema import cycle.
        from src.plan_schemas import WorkWindow

        windows = sorted([WorkWindow.model_validate(w) for w in value], key=lambda w: w.start)
        if any(a.end > b.start for a, b in zip(windows, windows[1:])):
            raise ValueError("Рабочие интервалы не должны пересекаться")
        return [w.model_dump(mode="json") for w in windows]

    @field_validator("deadline")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("Укажите часовой пояс дедлайна")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Неизвестный часовой пояс") from None
        return value


class CreateProject(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    state: ProjectState


class UpdateProject(StrictModel):
    base_revision: int = Field(ge=0)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    state: ProjectState | None = None

    @model_validator(mode="after")
    def changes_required(self):
        if self.title is None and self.state is None:
            raise ValueError("Укажите название или условия проекта")
        return self


class DeleteProject(StrictModel):
    base_revision: int = Field(ge=0)


class ContextOperation(StrictModel):
    request_id: UUID
    base_revision: int = Field(ge=0)


class SendMessage(ContextOperation):
    text: str = Field(min_length=1, max_length=16000)


class MemorySummary(StrictModel):
    goal: str = Field(max_length=2000)
    confirmed_constraints: list[NonEmpty] = Field(default_factory=list, max_length=50)
    decisions: list[NonEmpty] = Field(default_factory=list, max_length=50)
    user_objections: list[NonEmpty] = Field(default_factory=list, max_length=50)
    open_questions: list[NonEmpty] = Field(default_factory=list, max_length=50)
    assumptions: list[NonEmpty] = Field(default_factory=list, max_length=50)
