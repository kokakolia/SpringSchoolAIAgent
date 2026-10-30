"""Stable requirement identities; text edits create a new version, never new completion credit."""

import hashlib
from uuid import uuid4


def reconcile(old, texts):
    unused = list(old)
    result = []
    for index, text in enumerate(texts):
        match = next((item for item in unused if item["text"] == text), None)
        if match is None:
            match = next((item for item in unused if old.index(item) == index), None)
            result.append(
                {
                    "id": match["id"] if match else str(uuid4()),
                    "version": match["version"] + 1 if match else 1,
                    "text": text,
                }
            )
        else:
            result.append(dict(match))
        if match:
            unused.remove(match)
    return result


def reference(item):
    return {"id": item["id"], "version": item["version"]}


def key(item):
    return item["id"], item["version"]


def fallback(texts):
    # Pure validator callers have no database catalogue; keep content-sensitive identities.
    return [
        {"id": hashlib.sha256(f"{i}:{text}".encode()).hexdigest()[:36], "version": 1, "text": text}
        for i, text in enumerate(texts)
    ]


def bind(proposal, requirements, progress=None):
    for task in proposal.tasks:
        if not task.requirement_refs:
            flags = (progress or {}).get(task.id, {})
            if flags.get("done") and "completed_requirements" in flags:
                # Unknown legacy credit stays unknown; never bind it to today's text.
                task.requirement_refs = list(flags["completed_requirements"])
                continue
            task.requirement_refs = [
                reference(requirements[index - 1])
                for index in task.constraint_refs
                if 1 <= index <= len(requirements)
            ]
    return proposal
