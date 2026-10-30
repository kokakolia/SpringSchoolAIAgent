"""Loopback-only by default; password-protected single-owner access over HTTPS."""

import asyncio
import base64
import binascii
import ipaddress
import math
import secrets
import time
from collections import OrderedDict
from urllib.parse import parse_qs, urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from src.config import settings
from src.sessions import (
    StaleCredentialsError,
    cookie_name,
    credentials_current,
    issue_session,
    login_page,
    revoke_session,
    session_headers,
    valid_session,
)

AUTH_CHALLENGE = 'Basic realm="Deadline", charset="UTF-8"'


class AuthThrottle:
    """Bounded per-process counters; keys come from the trusted peer address, never usernames."""

    def __init__(self):
        self.clear()

    def clear(self):
        self.peers = OrderedDict()
        self.global_count = 0
        self.global_until = 0.0
        self.pending = {}

    def retry_after(self, peer):
        now = time.monotonic()
        if now >= self.global_until:
            self.global_count = 0
            self.global_until = now + 60
        count, until = self.peers.get(peer, (0, now + 60))
        blocked_until = max(
            self.global_until
            if self.global_count + sum(self.pending.values())
            >= settings.auth_global_attempts_per_minute
            else 0,
            until
            if (count if now < until else 0) + self.pending.get(peer, 0)
            >= settings.auth_attempts_per_minute
            else 0,
        )
        return max(0, math.ceil(blocked_until - now))

    def failed(self, peer):
        now = time.monotonic()
        if now >= self.global_until:
            self.global_count = 0
            self.global_until = now + 60
        self.global_count += 1
        count, until = self.peers.pop(peer, (0, now + 60))
        if now >= until:
            count, until = 0, now + 60
        self.peers[peer] = count + 1, until
        while len(self.peers) > 2048:
            self.peers.popitem(last=False)

    def started(self, peer):
        self.pending[peer] = self.pending.get(peer, 0) + 1

    def finished(self, peer, succeeded):
        count = self.pending[peer] - 1
        if count:
            self.pending[peer] = count
        else:
            self.pending.pop(peer)
        if not succeeded:
            self.failed(peer)


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


def verify_password(username, supplied):
    valid_user = secrets.compare_digest(username.encode(), settings.app_username.encode())
    valid_password = secrets.compare_digest(
        supplied.encode(), settings.app_password.get_secret_value().encode()
    )
    return valid_user and valid_password


class LoginBodyError(Exception):
    def __init__(self, detail, status):
        self.detail, self.status = detail, status


async def login_credentials(header, form_login, receive):
    username, supplied = "", ""
    if form_login:
        raw = bytearray()
        try:
            # One deadline covers the entire body, including a slow stream of small chunks.
            async with asyncio.timeout(settings.auth_login_body_timeout_seconds):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return None, None
                    raw.extend(message.get("body", b""))
                    if len(raw) > 4096:
                        raise LoginBodyError("Слишком большой запрос входа", 413)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            raise LoginBodyError("Время отправки данных входа истекло", 408) from None
        try:
            values = parse_qs(raw.decode("utf-8"), max_num_fields=4)
            username = values.get("username", [""])[0]
            supplied = values.get("password", [""])[0]
        except (UnicodeDecodeError, ValueError):
            pass
    elif header.lower().startswith("basic ") and len(header) <= 4096:
        try:
            username, supplied = (
                base64.b64decode(header[6:], validate=True).decode("utf-8").split(":", 1)
            )
        except (binascii.Error, UnicodeDecodeError, ValueError):
            pass
    return username, supplied


class AccessMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        new_session = None

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
                if new_session is not None:
                    headers.extend(session_headers(request, new_session))
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
            token = request.cookies.get(cookie_name(request))
            authenticated = await valid_session(token)
            if not password and not authenticated:
                if not await credentials_current():
                    error = JSONResponse(
                        {"detail": "Настройки входа процесса устарели"}, status_code=503
                    )
                elif not local or request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                    error = JSONResponse(
                        {"detail": "Внешний доступ отключён. Настройте APP_PASSWORD и HTTPS"},
                        status_code=403,
                    )
            elif not local and request.url.scheme != "https" and not settings.app_allow_http:
                error = JSONResponse(
                    {"detail": "Для внешнего доступа требуется HTTPS"}, status_code=403
                )
            else:
                if request.url.path == "/auth/logout" and request.method == "POST":
                    if authenticated:
                        await revoke_session(token)
                    response = RedirectResponse("/auth/login", status_code=303)
                    response.delete_cookie(
                        cookie_name(request),
                        path="/",
                        secure=request.url.scheme == "https",
                        httponly=True,
                        samesite="strict",
                    )
                    return await response(scope, receive, safe_send)
                if authenticated:
                    if request.url.path == "/auth/login":
                        return await RedirectResponse("/", status_code=303)(
                            scope, receive, safe_send
                        )
                else:
                    if request.url.path == "/auth/login" and request.method == "GET":
                        return await login_page()(scope, receive, safe_send)
                    header = request.headers.get("authorization", "")
                    form_login = request.url.path == "/auth/login" and request.method == "POST"
                    if not header and not form_login:
                        response = (
                            JSONResponse(
                                {"detail": "Требуется вход"},
                                status_code=401,
                                headers={"WWW-Authenticate": AUTH_CHALLENGE},
                            )
                            if request.url.path.startswith("/api/")
                            else RedirectResponse("/auth/login", status_code=303)
                        )
                        return await response(scope, receive, safe_send)
                    peer = request.client.host if request.client else "unknown"
                    if not await credentials_current():
                        return await JSONResponse(
                            {"detail": "Настройки входа процесса устарели"}, status_code=503
                        )(scope, receive, safe_send)
                    retry_after = auth_throttle.retry_after(peer)
                    if retry_after:
                        response = (
                            login_page("Слишком много попыток входа. Повторите через минуту", 429)
                            if form_login
                            else JSONResponse(
                                {"detail": "Слишком много попыток входа"}, status_code=429
                            )
                        )
                        response.headers["Retry-After"] = str(retry_after)
                        return await response(scope, receive, safe_send)
                    if sum(auth_throttle.pending.values()) >= settings.auth_max_concurrent_logins:
                        return await JSONResponse(
                            {"detail": "Вход временно занят. Повторите через несколько секунд"},
                            status_code=503,
                            headers={"Retry-After": "1"},
                        )(scope, receive, safe_send)
                    # Reserve capacity before any await, then release it on every exit path.
                    auth_throttle.started(peer)
                    succeeded = False
                    try:
                        try:
                            username, supplied = await login_credentials(
                                header, form_login, receive
                            )
                        except LoginBodyError as exc:
                            return await JSONResponse(
                                {"detail": exc.detail},
                                status_code=exc.status,
                                headers={"Connection": "close"}
                                if scope.get("http_version") in {"1.0", "1.1"}
                                else {},
                            )(scope, receive, safe_send)
                        if username is None:
                            return
                        if verify_password(username, supplied):
                            succeeded = True
                            try:
                                new_session = await issue_session()
                            except StaleCredentialsError:
                                return await JSONResponse(
                                    {"detail": "Настройки входа процесса устарели"}, status_code=503
                                )(scope, receive, safe_send)
                            if form_login:
                                return await RedirectResponse("/", status_code=303)(
                                    scope, receive, safe_send
                                )
                        else:
                            error = (
                                login_page("Неверное имя пользователя или пароль", 401)
                                if form_login
                                else JSONResponse(
                                    {"detail": "Требуется вход"},
                                    status_code=401,
                                    headers={"WWW-Authenticate": AUTH_CHALLENGE},
                                )
                            )
                    finally:
                        auth_throttle.finished(peer, succeeded)
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
