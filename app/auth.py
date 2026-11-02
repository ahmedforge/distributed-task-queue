"""Opaque, revocable login sessions. Workers never use browser credentials."""

import asyncio
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, WebSocketException
from starlette.requests import HTTPConnection
from sqlalchemy import delete
from sqlmodel.ext.asyncio.session import AsyncSession
from redis.exceptions import RedisError
import redis.asyncio as redis

from app.core.config import settings
from app.core.database import async_session_maker
from app.models.account import Account, AccountSession

COOKIE = "dtq_session"
HASH_ROUNDS = 600_000
_hash_slots = asyncio.Semaphore(4)


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), HASH_ROUNDS
    ).hex()
    return f"pbkdf2_sha256${HASH_ROUNDS}${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    _, rounds, salt, expected = encoded.split("$")
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), int(rounds)
    ).hex()
    return hmac.compare_digest(expected, actual)


DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


async def password_work(fn, *args):
    async with _hash_slots:
        return await asyncio.to_thread(fn, *args)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check_origin(connection: HTTPConnection):
    origin = connection.headers.get("origin")
    expected = settings.AUTH_ORIGIN or str(connection.base_url).rstrip("/").replace(
        "ws://", "http://"
    ).replace("wss://", "https://")
    if origin and origin != expected:
        if connection.scope["type"] == "websocket":
            raise WebSocketException(code=1008)
        raise HTTPException(403, "Origin is not allowed.")


def check_mutation(connection: HTTPConnection):
    check_origin(connection)
    if connection.headers.get("x-distribuqueue") != "1":
        raise HTTPException(403, "The X-DistribuQueue: 1 header is required.")


async def current_user(connection: HTTPConnection) -> Account:
    if connection.scope["type"] == "websocket":
        check_origin(connection)
    elif connection.scope["method"] not in ("GET", "HEAD", "OPTIONS"):
        check_mutation(connection)
    account = await authenticated_account(connection.cookies.get(COOKIE, ""))
    if account is None:
        if connection.scope["type"] == "websocket":
            raise WebSocketException(code=1008)
        raise HTTPException(401, "Sign in to continue.")
    return account


async def authenticated_account(token: str):
    async with async_session_maker() as session:
        login = (
            await session.get(AccountSession, token_hash(token))
            if token and len(token) <= 128
            else None
        )
        if not login or login.expires_at <= datetime.now(timezone.utc):
            return None
        return await session.get(Account, login.account_id)


async def rate_limit(connection: HTTPConnection, username: str):
    # A trusted reverse proxy must supply the actual peer address. Never trust
    # arbitrary X-Forwarded-For headers in application code.
    peer = connection.client.host if connection.client else "unknown"
    keys = ["dtq:auth:ip:" + token_hash(peer), "dtq:auth:user:" + token_hash(username)]
    script = """
    local denied = 0
    for i, key in ipairs(KEYS) do
      local n = redis.call('INCR', key)
      if n == 1 then redis.call('EXPIRE', key, 900) end
      if n > tonumber(ARGV[i]) then denied = 1 end
    end
    return denied
    """
    try:
        async with redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2
        ) as client:
            denied = await client.eval(script, 2, *keys, 100, 10)
    except (RedisError, OSError, TimeoutError):
        raise HTTPException(
            503, "Sign-in is temporarily unavailable. Try again shortly."
        )
    if denied:
        raise HTTPException(
            429,
            "Too many account attempts. Try again in 15 minutes.",
            headers={"Retry-After": "900"},
        )


async def issue_login(
    session: AsyncSession, account: Account, response, old_token: str | None = None
):
    if old_token:
        old = await session.get(AccountSession, token_hash(old_token))
        if old:
            await session.delete(old)
    await session.exec(
        delete(AccountSession).where(
            AccountSession.expires_at <= datetime.now(timezone.utc)
        )
    )
    token = secrets.token_urlsafe(32)
    session.add(
        AccountSession(
            token_hash=token_hash(token),
            account_id=account.id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
    )
    await session.commit()
    response.set_cookie(
        COOKIE,
        token,
        max_age=7 * 86400,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
