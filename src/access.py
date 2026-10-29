"""Loopback-only by default; password-protected single-owner access over HTTPS."""

import base64
import binascii
import ipaddress
import math
import secrets
import time
from collections import OrderedDict
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse

from src.config import settings


class AuthThrottle:
    """Bounded per-process counters; keys come from the trusted peer address, never usernames."""

    def __init__(self):
        self.clear()

    def clear(self):
        self.peers = OrderedDict()
        self.global_count = 0
        self.global_until = 0.0

    def retry_after(self, peer):
        now = time.monotonic()
        if now >= self.global_until:
            self.global_count = 0
            self.global_until = now + 60
        count, until = self.peers.get(peer, (0, now + 60))
        blocked_until = max(
            self.global_until
            if self.global_count >= settings.auth_global_attempts_per_minute
            else 0,
            until if count >= settings.auth_attempts_per_minute and now < until else 0,
        )
        return max(0, math.ceil(blocked_until - now))

    def failed(self, peer):
        now = time.monotonic()
        self.global_count += 1
        count, until = self.peers.pop(peer, (0, now + 60))
        if now >= until:
            count, until = 0, now + 60
        self.peers[peer] = count + 1, until
        while len(self.peers) > 2048:
            self.peers.popitem(last=False)

    def succeeded(self, peer):
        self.peers.pop(peer, None)


auth_throttle = AuthThrottle()


def loopback(host):
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or bool(
            getattr(ip, "ipv4_mapped", None) and ip.ipv4_mapped.is_loopback
        )
    except ValueError:
        return False


def origin_key(value):
    try:
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            return None
        if url.path not in {"", "/"} or url.query or url.fragment:
            return None
        return url.scheme, url.hostname.lower(), url.port or (443 if url.scheme == "https" else 80)
    except ValueError:
        return None


class AccessMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)

        async def safe_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"x-frame-options", b"DENY"),
                        (b"content-security-policy", b"frame-ancestors 'none'"),
                        (b"x-content-type-options", b"nosniff"),
                    ]
                )
                if request.url.path.startswith("/api/"):
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        if request.url.path == "/health":
            return await self.app(scope, receive, safe_send)
        origin = request.headers.get("origin")
        expected = origin_key(f"{request.url.scheme}://{request.headers.get('host', '')}")
        error = None
        if request.headers.get("sec-fetch-site") == "cross-site" or (
            origin is not None and (origin_key(origin) is None or origin_key(origin) != expected)
        ):
            error = JSONResponse({"detail": "Запрос с другого сайта запрещён"}, status_code=403)
        else:
            password = settings.app_password.get_secret_value()
            local = bool(request.client and loopback(request.client.host))
            if not password:
                if not local or request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                    error = JSONResponse(
                        {"detail": "Внешний доступ отключён. Настройте APP_PASSWORD и HTTPS"},
                        status_code=403,
                    )
            elif not local and request.url.scheme != "https" and not settings.app_allow_http:
                error = JSONResponse(
                    {"detail": "Для внешнего доступа требуется HTTPS"}, status_code=403
                )
            else:
                peer = request.client.host if request.client else "unknown"
                retry_after = auth_throttle.retry_after(peer)
                if retry_after:
                    return await JSONResponse(
                        {"detail": "Слишком много попыток входа. Попробуйте через минуту"},
                        status_code=429,
                        headers={"Retry-After": str(retry_after)},
                    )(scope, receive, safe_send)
                username, supplied = "", ""
                header = request.headers.get("authorization", "")
                if header.lower().startswith("basic "):
                    try:
                        username, supplied = (
                            base64.b64decode(header[6:], validate=True)
                            .decode("utf-8")
                            .split(":", 1)
                        )
                    except (binascii.Error, UnicodeDecodeError, ValueError):
                        pass
                valid_user = secrets.compare_digest(
                    username.encode(), settings.app_username.encode()
                )
                valid_password = secrets.compare_digest(supplied.encode(), password.encode())
                if not (valid_user and valid_password):
                    auth_throttle.failed(peer)
                    error = JSONResponse(
                        {"detail": "Требуется вход"},
                        status_code=401,
                        headers={"WWW-Authenticate": 'Basic realm="Deadline", charset="UTF-8"'},
                    )
                else:
                    auth_throttle.succeeded(peer)
        if error is not None:
            return await error(scope, receive, safe_send)
        if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            # Check actual bytes as well as Content-Length, including chunked uploads.
            chunks, size = [], 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body = message.get("body", b"")
                size += len(body)
                if size > settings.max_request_bytes:
                    return await JSONResponse(
                        {"detail": "Запрос слишком большой. Сократите текст проекта"},
                        status_code=413,
                    )(scope, receive, safe_send)
                chunks.append(body)
                if not message.get("more_body", False):
                    break

            delivered = False

            async def body_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
                return await receive()

            return await self.app(scope, body_receive, safe_send)
        return await self.app(scope, receive, safe_send)
