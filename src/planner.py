"""Deterministic feasibility checks and schedules. No model-generated arithmetic is trusted."""

from datetime import datetime, timedelta, timezone

from src.plan_schemas import PlanProposal, WorkWindow
from src.requirements import bind, fallback, key


def validate_plan(
    proposal: PlanProposal,
    state: dict,
    progress: dict,
    previous: dict | None = None,
    *,
    now: datetime | None = None,
    requirements: list | None = None,
    confirmed: list | None = None,
    broken_dependencies: bool = False,
) -> dict:
    now = now or datetime.now(timezone.utc)
    requirements = (
        requirements if requirements is not None else fallback(state.get("constraints", []))
    )
    proposal = bind(proposal.model_copy(deep=True), requirements, progress)
    errors = []

    def error(code, message, task_id=None):
        errors.append({"code": code, "message": message, "task_id": task_id})

    tasks = {t.id: t for t in proposal.tasks}
    if len(tasks) != len(proposal.tasks):
        error("duplicate_id", "У задач должны быть уникальные идентификаторы")
    completed = state.get("completed", False)
    if not tasks and not completed:
        error("empty_plan", "В предложении пока нет задач")
    if proposal.questions:
        error("questions", "Нужно уточнить: " + "; ".join(proposal.questions))
    capacity = state.get("available_minutes")
    if capacity is None:
        error("capacity_unknown", "Укажите доступное рабочее время")
        capacity = 0
    if not state.get("success_criteria"):
        error("success_unknown", "Уточните, что будет считаться готовым результатом")
    deadline = datetime.fromisoformat(state["deadline"]) if state.get("deadline") else None
    if deadline is None:
        error("deadline_unknown", "Укажите дедлайн проекта")
    if deadline and not completed:
        capacity = min(capacity, max(0, int((deadline - now).total_seconds() // 60)))
        if deadline <= now:
            error("deadline_passed", "Дедлайн уже прошёл: задайте новый срок")
    windows = []
    for raw in state.get("work_windows", []):
        window = WorkWindow.model_validate(raw)
        start, end = max(window.start, now), min(window.end, deadline or window.end)
        if start < end:
            windows.append((start, end))
    windows.sort()
    if state.get("work_windows"):
        capacity = min(
            capacity, sum(int((end - start).total_seconds() // 60) for start, end in windows)
        )
    done = {key for key, value in progress.items() if value.get("done")}
    active = {t.id: t for t in proposal.tasks if t.decision != "defer" and t.id not in done}
    if not active and not completed:
        error(
            "no_active_work",
            "Нет работы для достижения цели. Подтвердите завершение проекта или пересоберите план",
        )
    if active and completed:
        error("completed_project", "Проект завершён. Верните его в работу перед добавлением задач")
    old_tasks = {task["id"]: task for task in (previous or {}).get("tasks", [])}
    archive = set(proposal.archive_task_ids)
    if len(archive) != len(proposal.archive_task_ids):
        error("duplicate_archive", "Задача указана в архиве несколько раз")
    for task_id in archive:
        old = old_tasks.get(task_id)
        flags = progress.get(task_id, {})
        if (
            old is None
            or task_id in tasks
            or flags.get("pinned")
            or (not flags.get("done") and old["decision"] != "defer")
        ):
            error(
                "bad_archive",
                "В архив можно убрать только завершённую или уже отложенную незакреплённую задачу",
                task_id,
            )
    for task in proposal.tasks:
        if progress.get(task.id, {}).get("archived"):
            error(
                "archived_id", "Идентификатор архивной задачи нельзя использовать повторно", task.id
            )
        if task.decision in ("simplify", "defer") and not task.tradeoff:
            error("tradeoff_missing", "Нужно объяснить последствия изменения", task.id)
        if len(set(task.depends_on)) != len(task.depends_on):
            error("duplicate_dependency", "Зависимость указана несколько раз", task.id)
        for dependency in task.depends_on:
            if dependency == task.id or (dependency not in tasks and dependency not in done):
                error("bad_dependency", "Неизвестная зависимость или ссылка на себя", task.id)
            elif task.id in active and dependency not in active and dependency not in done:
                error("deferred_dependency", "Активная задача зависит от отложенной", task.id)
        if not progress.get(task.id, {}).get("done") and any(
            ref < 1 or ref > len(state.get("constraints", [])) for ref in task.constraint_refs
        ):
            error("bad_constraint", "Ссылка на несуществующее требование", task.id)
    required_keys = {key(item) for item in requirements}
    covered = {key(item) for item in (confirmed or [])}
    for task in proposal.tasks:
        refs = task.requirement_refs
        flags = progress.get(task.id, {})
        if flags.get("done"):
            original = flags.get("completed_requirements", refs)
            if {key(ref) for ref in refs} != {key(ref) for ref in original}:
                error(
                    "completed_requirements",
                    "Нельзя менять подтверждённые требования выполненной задачи",
                    task.id,
                )
            refs = original
        elif any(key(ref) not in required_keys for ref in refs):
            error("bad_requirement", "Ссылка на неактуальную версию требования", task.id)
        if task.decision != "defer":
            covered.update(key(ref) for ref in refs)
    for requirement in requirements:
        if key(requirement) not in covered:
            error("uncovered_constraint", "В плане не отражено требование: " + requirement["text"])
    if broken_dependencies:
        error("completed_dependency", "Нарушены зависимости выполненных задач, включая архив")
    for old in (previous or {}).get("tasks", []):
        protected = tasks.get(old["id"])
        flags = progress.get(old["id"], {})
        if protected is None:
            if old["id"] not in archive:
                error(
                    "silent_removal",
                    "Покажите исключение старой задачи явно или предложите архивирование",
                    old["id"],
                )
        elif flags.get("done") or flags.get("pinned"):
            if (
                protected.title != old["title"]
                or protected.done_when != old["done_when"]
                or protected.decision == "defer"
                or protected.decision not in ("keep", old["decision"])
            ):
                error(
                    "protected_task",
                    "Закреплённую или завершённую задачу нельзя менять",
                    protected.id,
                )
            if flags.get("done") and set(protected.depends_on) != set(old.get("depends_on", [])):
                error(
                    "completed_dependencies",
                    "Нельзя менять зависимости выполненной задачи",
                    old["id"],
                )
    # Validate the complete graph, including completed and deferred tasks.
    pending = {key: set(task.depends_on) & tasks.keys() for key, task in tasks.items()}
    while pending:
        roots = {key for key, dependencies in pending.items() if not dependencies}
        if not roots:
            error("dependency_cycle", "В графе задач обнаружен цикл зависимостей")
            break
        pending = {
            key: dependencies - roots for key, dependencies in pending.items() if key not in roots
        }
    order = []
    satisfied = set(done)
    remaining = dict(active)
    while remaining:
        eligible = [t for t in remaining.values() if set(t.depends_on) <= satisfied]
        if not eligible:
            if not any(item["code"] == "dependency_cycle" for item in errors):
                error("dependency_cycle", "Зависимости не позволяют выполнить оставшиеся задачи")
            break
        task = min(eligible, key=lambda t: {"must": 0, "should": 1, "could": 2}[t.priority])
        order.append(task)
        satisfied.add(task.id)
        remaining.pop(task.id)
    effort = sum(t.estimate_minutes for t in active.values())
    required = effort + (0 if completed else proposal.buffer_minutes)
    if required > capacity:
        error("over_capacity", f"План требует {required} мин., доступно {capacity} мин.")
    schedule = []
    offset = 0
    cursor = windows[0][0] if windows else None
    window_index = 0
    for task in order:
        entry: dict = {
            "task_id": task.id,
            "start_minute": offset,
            "end_minute": offset + task.estimate_minutes,
            "segments": [],
        }
        minutes = task.estimate_minutes
        while minutes and cursor is not None and window_index < len(windows):
            start, end = windows[window_index]
            cursor = max(start, cursor)
            amount = min(minutes, max(0, int((end - cursor).total_seconds() // 60)))
            if amount:
                finish = cursor + timedelta(minutes=amount)
                entry["segments"].append({"start": cursor.isoformat(), "end": finish.isoformat()})
                cursor = finish
                minutes -= amount
            if minutes:
                window_index += 1
                cursor = windows[window_index][0] if window_index < len(windows) else None
        offset += task.estimate_minutes
        schedule.append(entry)
    return {
        "valid": not errors,
        "errors": errors,
        "work_minutes": effort,
        "buffer_minutes": proposal.buffer_minutes,
        "required_minutes": required,
        "capacity_minutes": capacity,
        "spare_minutes": capacity - required,
        "schedule": schedule,
        "schedule_kind": "calendar" if windows else "sequence",
        "next_task_id": order[0].id if order else None,
        "checked_at": now.isoformat(),
    }


def same_plan(previous: dict | None, proposal: dict) -> bool:
    """Compare actionable work, not regenerated summaries and explanations."""
    if previous is None:
        return False
    fields = ("id", "title", "estimate_minutes", "priority", "decision", "done_when")

    def signature(plan):
        return (
            plan.get("buffer_minutes", 0),
            tuple(sorted(plan.get("archive_task_ids", []))),
            [
                (
                    *(task.get(key) for key in fields),
                    tuple(sorted(task.get("depends_on", []))),
                    tuple(sorted(task.get("constraint_refs", []))),
                    tuple(sorted(key(ref) for ref in task.get("requirement_refs", []))),
                )
                for task in plan["tasks"]
            ],
        )

    return signature(previous) == signature(proposal)


def plan_diff(previous: dict | None, proposal: dict) -> list[dict]:
    old = {t["id"]: t for t in (previous or {}).get("tasks", [])}
    changes = []
    fields = (
        "title",
        "estimate_minutes",
        "priority",
        "decision",
        "depends_on",
        "done_when",
        "constraint_refs",
        "requirement_refs",
    )
    for task in proposal["tasks"]:
        before = old.pop(task["id"], None)
        action = "added" if before is None else "changed"
        if before and all(before.get(key) == task.get(key) for key in fields):
            action = "unchanged"
        if task["decision"] == "defer" and (not before or before["decision"] != "defer"):
            action = "deferred"
        changes.append({"task_id": task["id"], "action": action, "before": before, "after": task})
    changes.extend(
        {
            "task_id": t["id"],
            "action": "archived" if t["id"] in proposal.get("archive_task_ids", []) else "removed",
            "before": t,
            "after": None,
        }
        for t in old.values()
    )
    return changes
