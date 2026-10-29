import httpx
from sqlalchemy import func, select

from src import database
from src.server import app


async def test_retired_agent_rejects_large_and_repeated_input_without_side_effects():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as api:
        for _ in range(2):
            response = await api.post("/api/process", json={"text": "x" * 200000})
            assert response.status_code == 410
        # No request body parsing, model transport or legacy tool execution is reachable.
        assert (await api.post("/api/process", content=b"invalid JSON")).status_code == 410
    async with database._get_session_maker()() as session:
        assert await session.scalar(select(func.count()).select_from(database.Task)) == 0


async def test_legacy_matrix_is_not_part_of_the_application_api():
    paths = app.openapi()["paths"]
    assert not any(path.startswith(("/api/matrix", "/api/tasks")) for path in paths)
    assert "/api/export" not in paths
