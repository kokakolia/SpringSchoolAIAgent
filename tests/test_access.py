import httpx
import pytest
from pydantic import SecretStr

from src.config import settings
from src.server import app


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/api/projects", None),
        ("POST", "/api/process", {"text": "Do work"}),
        ("DELETE", "/api/projects/00000000-0000-0000-0000-000000000001", {"base_revision": 0}),
    ],
)
async def test_untrusted_origin_cannot_read_write_or_call_legacy_agent(method, path, body):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as api:
        response = await api.request(
            method, path, json=body, headers={"Origin": "https://untrusted.example"}
        )
        assert response.status_code == 403
        assert "access-control-allow-origin" not in response.headers
        preflight = await api.options(
            path,
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": method,
            },
        )
        assert preflight.status_code == 403


@pytest.mark.parametrize(
    "host,client",
    [
        ("https://public.example", ("203.0.113.1", 123)),
        ("http://localhost", ("203.0.113.1", 123)),
        ("http://rebound.example", ("127.0.0.1", 123)),
    ],
)
async def test_unconfigured_external_access_and_host_rebinding_are_denied(host, client):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=client), base_url=host
    ) as api:
        assert (await api.get("/api/projects")).status_code == 403


async def test_local_same_origin_api_remains_available():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as api:
        response = await api.get("/api/projects", headers={"Origin": "http://localhost:80"})
        assert response.status_code == 200
        assert response.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["cache-control"] == "no-store"
        assert (await api.get("/api/projects", headers={"Origin": "null"})).status_code == 403
        assert (
            await api.get("/api/projects", headers={"Sec-Fetch-Site": "cross-site"})
        ).status_code == 403


async def test_configured_owner_password_and_https_are_required(monkeypatch):
    monkeypatch.setattr(settings, "app_password", SecretStr("a-strong-test-password"))
    transport = httpx.ASGITransport(app=app, client=("203.0.113.1", 123))
    async with httpx.AsyncClient(transport=transport, base_url="https://app.example") as api:
        denied = await api.get("/api/projects")
        assert denied.status_code == 401 and "Basic" in denied.headers["www-authenticate"]
        assert (await api.get("/api/projects", auth=("owner", "wrong"))).status_code == 401
        assert (
            await api.get("/api/projects", auth=("owner", "a-strong-test-password"))
        ).status_code == 200
        assert (
            await api.get(
                "/api/projects",
                auth=("owner", "a-strong-test-password"),
                headers={"Origin": "https://evil.example"},
            )
        ).status_code == 403
    async with httpx.AsyncClient(transport=transport, base_url="http://app.example") as api:
        assert (
            await api.get("/api/projects", auth=("owner", "a-strong-test-password"))
        ).status_code == 403


async def test_explicit_local_docker_http_still_requires_password(monkeypatch):
    monkeypatch.setattr(settings, "app_allow_http", True)
    transport = httpx.ASGITransport(app=app, client=("172.18.0.1", 123))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as api:
        assert (await api.get("/api/projects")).status_code == 403
        monkeypatch.setattr(settings, "app_password", SecretStr("a-strong-test-password"))
        assert (await api.get("/api/projects")).status_code == 401
        assert (
            await api.get("/api/projects", auth=("owner", "a-strong-test-password"))
        ).status_code == 200


async def test_authentication_attempts_are_limited_and_recover_after_window(monkeypatch):
    from types import SimpleNamespace

    from src import access

    clock = [100.0]
    monkeypatch.setattr(access, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(settings, "app_password", SecretStr("a-strong-test-password"))
    monkeypatch.setattr(settings, "auth_attempts_per_minute", 3)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.8", 123)),
        base_url="https://app.example",
    ) as api:
        for _ in range(3):
            assert (await api.get("/api/projects", auth=("owner", "wrong"))).status_code == 401
        limited = await api.get("/api/projects", auth=("owner", "a-strong-test-password"))
        assert limited.status_code == 429
        assert 1 <= int(limited.headers["retry-after"]) <= 60
        clock[0] += 61
        assert (
            await api.get("/api/projects", auth=("owner", "a-strong-test-password"))
        ).status_code == 200


def test_distributed_authentication_failures_have_global_limit_and_bounded_storage(monkeypatch):
    from src.access import AuthThrottle

    throttle = AuthThrottle()
    monkeypatch.setattr(settings, "auth_global_attempts_per_minute", 4)
    for peer in ("one", "two", "three", "four"):
        assert throttle.retry_after(peer) == 0
        throttle.failed(peer)
    assert throttle.retry_after("five") > 0
    for i in range(2100):
        throttle.failed(str(i))
    assert len(throttle.peers) <= 2048


async def test_request_size_limit_applies_to_streamed_uploads_without_content_length(monkeypatch):
    monkeypatch.setattr(settings, "max_request_bytes", 65536)

    async def stream():
        for _ in range(4):
            yield b"x" * 20000

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as api:
        assert (await api.post("/api/projects", content=stream())).status_code == 413
        assert (await api.get("/api/projects")).json() == []
        assert (await api.post("/api/process", content=b"x" * 70000)).status_code == 413
