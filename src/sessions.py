"""Opaque, expiring sessions; a password change invalidates existing cookies."""

import hashlib
import hmac
import secrets
from datetime import timedelta, timezone
from functools import lru_cache
from html import escape

from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from starlette.responses import HTMLResponse, Response

from src import database
from src.config import settings
from src.memory_models import AuthCredentials, AuthSession, utcnow


@lru_cache(maxsize=4)
def credential_verifier(username, password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", (username + "\0" + password).encode(), bytes.fromhex(salt), 200000
    ).hex()


class StaleCredentialsError(Exception):
    """A worker cannot promote its own configuration to the database's authority."""


def matches_credentials(row):
    return hmac.compare_digest(
        row.verifier,
        credential_verifier(
            settings.app_username, settings.app_password.get_secret_value(), row.salt
        ),
    )


async def initialize_credentials():
    # Bootstrap only. Starting an old worker must never roll the central state back.
    async with database._get_session_maker()() as session:
        if await session.get(AuthCredentials, 1) is not None:
            return
        salt = secrets.token_hex(32)
        session.add(
            AuthCredentials(
                id=1,
                salt=salt,
                generation=1,
                verifier=credential_verifier(
                    settings.app_username, settings.app_password.get_secret_value(), salt
                ),
            )
        )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            if await session.get(AuthCredentials, 1) is None:
                raise


async def credentials_current():
    async with database._get_session_maker()() as session:
        row = await session.get(AuthCredentials, 1)
        # No row exists only before bootstrap; session validation still fails closed.
        return row is None or matches_credentials(row)


async def rotate_credentials(expected_generation):
    """Explicit administrative CAS, never called by request or startup paths."""
    async with database._get_session_maker()() as session:
        row = await session.get(AuthCredentials, 1)
        if row is None or row.generation != expected_generation:
            raise StaleCredentialsError("Версия учётных данных изменилась. Проверьте auth-status")
        generation = row.generation + 1
        salt = secrets.token_hex(32)
        changed = await session.execute(
            update(AuthCredentials)
            .where(AuthCredentials.id == 1, AuthCredentials.generation == expected_generation)
            .values(
                generation=generation,
                salt=salt,
                verifier=credential_verifier(
                    settings.app_username, settings.app_password.get_secret_value(), salt
                ),
            )
        )
        if not changed.rowcount:
            raise StaleCredentialsError("Версия учётных данных изменилась. Проверьте auth-status")
        await session.execute(delete(AuthSession))
        await session.commit()
        return generation


def cookie_name(request):
    return "__Host-deadline_session" if request.url.scheme == "https" else "deadline_session"


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def tag(token, verifier):
    return hmac.new(verifier.encode(), token.encode(), hashlib.sha256).hexdigest()


async def valid_session(token):
    if not token or len(token) != 43:
        return False
    async with database._get_session_maker()() as session:
        credentials = await session.get(AuthCredentials, 1)
        if credentials is None:
            return False
        row = await session.get(AuthSession, digest(token))
        if row is None or row.credential_generation != credentials.generation:
            return False
        expires = (
            row.expires_at.replace(tzinfo=timezone.utc)
            if row.expires_at.tzinfo is None
            else row.expires_at
        )
        return expires > utcnow() and hmac.compare_digest(
            row.auth_tag, tag(token, credentials.verifier)
        )


async def issue_session():
    await initialize_credentials()
    token = secrets.token_urlsafe(32)
    async with database._get_session_maker()() as session:
        credentials = await session.get(AuthCredentials, 1)
        if credentials is None or not matches_credentials(credentials):
            raise StaleCredentialsError("Настройки входа процесса устарели")
        await session.execute(delete(AuthSession).where(AuthSession.expires_at <= utcnow()))
        session.add(
            AuthSession(
                token_hash=digest(token),
                auth_tag=tag(token, credentials.verifier),
                expires_at=utcnow() + timedelta(seconds=settings.auth_session_seconds),
                credential_generation=credentials.generation,
            )
        )
        await session.commit()
    return token


async def revoke_session(token):
    async with database._get_session_maker()() as session:
        await session.execute(delete(AuthSession).where(AuthSession.token_hash == digest(token)))
        await session.commit()


def session_headers(request, token):
    response = Response()
    response.set_cookie(
        cookie_name(request),
        token,
        max_age=settings.auth_session_seconds,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/",
    )
    return [header for header in response.raw_headers if header[0] == b"set-cookie"]


def login_page(message="", status=200):
    return HTMLResponse(
        f"""<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Войти — Мой план лучше</title>
<style>body{{background:#f7f6f2;color:#292e27;font:16px system-ui;display:grid;place-items:center;
min-height:95vh}}
form{{width:min(320px,85vw);display:grid;gap:16px}}
h1{{font-size:30px}}
label{{display:grid;gap:8px}}
input,button{{font:inherit;padding:12px;border:1px solid #d7decd;border-radius:8px}}
button{{background:#ea784b;color:white;cursor:pointer}}
p{{color:#99492e}}</style>
<form method="post" action="/auth/login"><h1>мой план лучше</h1>
<label>Имя пользователя<input name="username"
 autocomplete="username" required maxlength="200"></label>
<label>Пароль<input name="password" type="password"
 autocomplete="current-password" required maxlength="1024"></label>
<p role="alert">{escape(message)}</p><button>Войти</button></form></html>""",
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )
