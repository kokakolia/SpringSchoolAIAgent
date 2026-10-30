"""Indexed checks span current and archived work without loading all historical plans."""

from sqlalchemy import and_, delete, or_, select, tuple_
from sqlalchemy.orm import aliased

from src.memory_models import ArchivedTask, RequirementProof, TaskDependency, TaskProgress
from src.requirements import key


def task_ids(*proposals):
    return {
        value
        for proposal in proposals
        if proposal
        for task in proposal.get("tasks", [])
        for value in [task["id"], *task.get("depends_on", [])]
    }


async def progress_map(session, project_id, ids):
    if not ids:
        return {}
    rows = (
        await session.scalars(
            select(TaskProgress).where(
                TaskProgress.project_id == project_id, TaskProgress.task_id.in_(ids)
            )
        )
    ).all()
    result = {
        r.task_id: {
            "done": r.done,
            "pinned": r.pinned,
            "spent_minutes": r.spent_minutes,
            "completed_requirements": [],
        }
        for r in rows
    }
    for row in (
        await session.scalars(
            select(RequirementProof).where(
                RequirementProof.project_id == project_id, RequirementProof.task_id.in_(ids)
            )
        )
    ).all():
        result.setdefault(
            row.task_id,
            {"done": False, "pinned": False, "spent_minutes": 0, "completed_requirements": []},
        )["completed_requirements"].append({"id": row.requirement_id, "version": row.version})
    for row in (
        await session.scalars(
            select(ArchivedTask).where(
                ArchivedTask.project_id == project_id, ArchivedTask.task_id.in_(ids)
            )
        )
    ).all():
        result.setdefault(
            row.task_id,
            {"done": False, "pinned": False, "spent_minutes": 0, "completed_requirements": []},
        ).update(archived=True, archived_task=row.task)
    return result


async def confirmed_requirements(session, project_id, catalogue):
    if not catalogue:
        return []
    query = (
        select(RequirementProof.requirement_id, RequirementProof.version)
        .join(
            TaskProgress,
            and_(
                TaskProgress.project_id == RequirementProof.project_id,
                TaskProgress.task_id == RequirementProof.task_id,
            ),
        )
        .where(
            RequirementProof.project_id == project_id,
            TaskProgress.done.is_(True),
            tuple_(RequirementProof.requirement_id, RequirementProof.version).in_(
                [key(item) for item in catalogue]
            ),
        )
        .distinct()
    )
    return [
        {"id": rid, "version": version} for rid, version in (await session.execute(query)).all()
    ]


async def broken_completed_dependencies(session, project_id):
    child, parent = aliased(TaskProgress), aliased(TaskProgress)
    query = (
        select(TaskDependency.task_id)
        .join(
            child,
            and_(
                child.project_id == TaskDependency.project_id,
                child.task_id == TaskDependency.task_id,
            ),
        )
        .outerjoin(
            parent,
            and_(
                parent.project_id == TaskDependency.project_id,
                parent.task_id == TaskDependency.dependency_id,
            ),
        )
        .where(
            TaskDependency.project_id == project_id,
            child.done.is_(True),
            or_(parent.done.is_(False), parent.task_id.is_(None)),
        )
        .limit(1)
    )
    return await session.scalar(query) is not None


async def unfinished_ancestors(session, project_id, task_id):
    ancestors = (
        select(TaskDependency.dependency_id)
        .where(TaskDependency.project_id == project_id, TaskDependency.task_id == task_id)
        .cte("ancestors", recursive=True)
    )
    ancestors = ancestors.union(
        select(TaskDependency.dependency_id)
        .join(ancestors, TaskDependency.task_id == ancestors.c.dependency_id)
        .where(TaskDependency.project_id == project_id)
    )
    query = (
        select(ancestors.c.dependency_id)
        .outerjoin(
            TaskProgress,
            and_(
                TaskProgress.project_id == project_id,
                TaskProgress.task_id == ancestors.c.dependency_id,
            ),
        )
        .where(or_(TaskProgress.done.is_(False), TaskProgress.task_id.is_(None)))
        .limit(1)
    )
    return await session.scalar(query) is not None


async def completed_dependents(session, project_id, task_id):
    query = (
        select(TaskDependency.task_id)
        .join(
            TaskProgress,
            and_(
                TaskProgress.project_id == TaskDependency.project_id,
                TaskProgress.task_id == TaskDependency.task_id,
            ),
        )
        .where(
            TaskDependency.project_id == project_id,
            TaskDependency.dependency_id == task_id,
            TaskProgress.done.is_(True),
        )
        .limit(1)
    )
    return await session.scalar(query) is not None


async def replace_dependencies(session, project_id, proposal):
    ids = [task["id"] for task in proposal["tasks"]]
    if ids:
        await session.execute(
            delete(TaskDependency).where(
                TaskDependency.project_id == project_id, TaskDependency.task_id.in_(ids)
            )
        )
    for task in proposal["tasks"]:
        session.add_all(
            [
                TaskDependency(project_id=project_id, task_id=task["id"], dependency_id=dep)
                for dep in task.get("depends_on", [])
            ]
        )
