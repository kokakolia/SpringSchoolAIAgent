"""Read-only recovery of historical matrix rows; excluded from the web API."""

import json
import os
import tempfile
from pathlib import Path

from sqlalchemy import select

from src import database


async def export_tasks(path: Path):
    try:
        await database.init_db()
        async with database._get_session_maker()() as session:
            rows = (await session.scalars(select(database.Task).order_by(database.Task.id))).all()
            data = [
                {
                    "id": row.id,
                    "user_id": row.user_id,
                    "title": row.title,
                    "quadrant": row.quadrant,
                    "reason": row.reason,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ]
        path = Path(path).resolve()
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False
            ) as output:
                temporary = Path(output.name)
                json.dump(data, output, ensure_ascii=False, indent=2)
                output.write("\n")
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    finally:
        await database.close_db()
