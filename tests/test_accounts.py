"""Exercise real authentication and owner isolation, without the legacy test override."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlmodel import select
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.auth import COOKIE, current_user, token_hash
from app.core.config import settings
from app.core.database import async_session_maker
from app.core.storage import get_storage
from app.main import app
from app.models.account import Account, AccountSession
from app.models.job import Job, JobStatus

PASSWORD = "A unique test passphrase 2026!"


@pytest_asyncio.fixture
async def auth_client(reset_test_environment):
    app.dependency_overrides.pop(current_user, None)
    async with AsyncClient(
        transport=ASGITransport(app=app, client=(uuid4().hex, 123)),
        base_url="http://test",
        headers={"X-DistribuQueue": "1"},
    ) as client:
        yield client


async def register(client):
    username = "learner_" + uuid4().hex[:12]
    response = await client.post(
        "/api/v1/account/register", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    return username


async def upload(client, pdf_bytes, key=None):
    response = await client.post(
        "/api/v1/jobs/upload",
        files={"file": ("private.pdf", pdf_bytes, "application/pdf")},
        data={"idempotency_key": key} if key else {},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_signup_hash_cookie_and_logout_revocation(auth_client):
    username = await register(auth_client)
    raw = auth_client.cookies.get(COOKIE)
    async with async_session_maker() as session:
        account = (await session.exec(select(Account))).one()
        login = (await session.exec(select(AccountSession))).one()
        assert account.password_hash.startswith("pbkdf2_sha256$600000$")
        assert PASSWORD not in account.password_hash
        assert login.token_hash == token_hash(raw) and raw != login.token_hash
    me = await auth_client.get("/api/v1/account/me")
    assert me.json() == {"username": username}
    assert me.headers["cache-control"] == "no-store"
    assert (await auth_client.post("/api/v1/account/logout")).status_code == 200
    auth_client.cookies.set(COOKIE, raw)
    assert (await auth_client.get("/api/v1/jobs")).status_code == 401


@pytest.mark.asyncio
async def test_cookie_flags_and_normalized_username(auth_client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_COOKIE_SECURE", True)
    data = {
        "username": "  Mixed_" + uuid4().hex[:8].upper() + "  ",
        "password": PASSWORD,
    }
    result = await auth_client.post("/api/v1/account/register", json=data)
    assert result.status_code == 201
    cookie = result.headers["set-cookie"].lower()
    assert (
        "httponly" in cookie
        and "samesite=lax" in cookie
        and "secure" in cookie
        and "max-age=604800" in cookie
    )
    assert result.json()["username"] == data["username"].strip().lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["", "/stats", "/dlq", "/1", "/1/result"])
async def test_anonymous_reads_rejected(auth_client, path):
    assert (await auth_client.get("/api/v1/jobs" + path)).status_code == 401


@pytest.mark.asyncio
async def test_anonymous_mutations_and_socket_rejected(auth_client, pdf_bytes):
    assert (
        await auth_client.post(
            "/api/v1/jobs/upload", files={"file": ("test.pdf", pdf_bytes)}
        )
    ).status_code == 401
    assert (await auth_client.delete("/api/v1/jobs/1")).status_code == 401
    assert (await auth_client.post("/api/v1/jobs/1/requeue")).status_code == 401
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/api/v1/jobs/1/ws"):
                pass
        assert exc.value.code == 1008


@pytest.mark.asyncio
async def test_other_account_cannot_read_download_retry_delete_or_subscribe(
    auth_client, pdf_bytes
):
    first_user = await register(auth_client)
    job = await upload(auth_client, pdf_bytes)
    first_cookie = auth_client.cookies.get(COOKIE)
    await register(auth_client)
    for method, suffix in [
        ("GET", ""),
        ("GET", "/result"),
        ("POST", "/requeue"),
        ("DELETE", ""),
    ]:
        response = await auth_client.request(
            method, f"/api/v1/jobs/{job['id']}" + suffix
        )
        assert response.status_code == 404
    assert (await auth_client.get("/api/v1/jobs")).json()["total"] == 0
    assert (await auth_client.get("/api/v1/jobs/dlq")).json() == []
    stats = (await auth_client.get("/api/v1/jobs/stats")).json()
    assert stats["total_jobs"] == 0 and stats["queue_counts"] is None
    assert sum(row["completed"] for row in stats["completed_per_minute"]) == 0
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                f"/api/v1/jobs/{job['id']}/ws",
                headers={"Cookie": f"{COOKIE}={auth_client.cookies.get(COOKIE)}"},
            ):
                pass
        assert exc.value.code == 1008
    auth_client.cookies.clear()
    login = await auth_client.post(
        "/api/v1/account/login", json={"username": first_user, "password": PASSWORD}
    )
    assert login.status_code == 200
    assert (await auth_client.get(f"/api/v1/jobs/{job['id']}")).status_code == 200
    # A prior cookie replaced during registration is revoked.
    assert first_cookie != auth_client.cookies.get(COOKIE)


@pytest.mark.asyncio
async def test_owner_end_to_end_render_download_live_snapshot_delete(
    auth_client, pdf_bytes
):
    await register(auth_client)
    job = await upload(auth_client, pdf_bytes)
    from app.worker import settings as worker_settings

    await worker_settings["functions"][0]({}, payload={"id": job["id"]})
    result = await auth_client.get(f"/api/v1/jobs/{job['id']}/result")
    assert result.status_code == 200 and result.content.startswith(b"PK")
    with TestClient(app) as client:
        with client.websocket_connect(
            f"/api/v1/jobs/{job['id']}/ws",
            headers={
                "Cookie": f"{COOKIE}={auth_client.cookies.get(COOKIE)}",
                "Origin": "http://testserver",
            },
        ) as socket:
            assert socket.receive_json()["status"] == "COMPLETED"
    stats = (await auth_client.get("/api/v1/jobs/stats")).json()
    assert stats["total_jobs"] == 1 and stats["status_counts"]["COMPLETED"] == 1
    assert sum(row["completed"] for row in stats["completed_per_minute"]) == 1
    assert (await auth_client.delete(f"/api/v1/jobs/{job['id']}")).status_code == 200
    assert (await auth_client.get("/api/v1/jobs")).json()["total"] == 0


@pytest.mark.asyncio
async def test_owner_can_retry_failed_job(auth_client, pdf_bytes):
    await register(auth_client)
    job = await upload(auth_client, pdf_bytes)
    async with async_session_maker() as session:
        saved = await session.get(Job, job["id"])
        saved.status = JobStatus.FAILED
        await session.commit()
    assert len((await auth_client.get("/api/v1/jobs/dlq")).json()) == 1
    response = await auth_client.post(f"/api/v1/jobs/{job['id']}/requeue")
    assert response.status_code == 200 and response.json()["status"] == "PENDING"


@pytest.mark.asyncio
async def test_idempotency_is_scoped_to_owner_and_concurrent_safe(
    auth_client, pdf_bytes
):
    await register(auth_client)
    jobs = await asyncio.gather(
        upload(auth_client, pdf_bytes, "same-key"),
        upload(auth_client, pdf_bytes, "same-key"),
    )
    assert jobs[0]["id"] == jobs[1]["id"]
    await register(auth_client)
    other = await upload(auth_client, pdf_bytes, "same-key")
    assert other["id"] != jobs[0]["id"]
    assert (await auth_client.get("/api/v1/jobs")).json()["total"] == 1


@pytest.mark.asyncio
async def test_legacy_jobs_remain_unowned_and_inaccessible(auth_client):
    async with async_session_maker() as session:
        old = Job(filename="legacy-private.pdf", status=JobStatus.FAILED)
        session.add(old)
        await session.commit()
        old_id = old.id
    await register(auth_client)
    assert (await auth_client.get("/api/v1/jobs")).json()["total"] == 0
    assert (await auth_client.get(f"/api/v1/jobs/{old_id}")).status_code == 404
    async with async_session_maker() as session:
        assert (await session.get(Job, old_id)).owner_id is None


@pytest.mark.asyncio
async def test_logout_all_revokes_other_device(auth_client):
    username = await register(auth_client)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-DistribuQueue": "1"},
    ) as other:
        assert (
            await other.post(
                "/api/v1/account/login",
                json={"username": username, "password": PASSWORD},
            )
        ).status_code == 200
        assert (await other.get("/api/v1/jobs")).status_code == 200
        assert (await auth_client.post("/api/v1/account/logout-all")).status_code == 200
        assert (await other.get("/api/v1/jobs")).status_code == 401
        assert (await auth_client.get("/api/v1/jobs")).status_code == 401


@pytest.mark.asyncio
async def test_expired_and_invalid_tokens_rejected(auth_client):
    await register(auth_client)
    async with async_session_maker() as session:
        login = (await session.exec(select(AccountSession))).one()
        login.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
    assert (await auth_client.get("/api/v1/account/me")).status_code == 401
    auth_client.cookies.clear()
    auth_client.cookies.set(COOKIE, "not-a-real-token")
    assert (await auth_client.get("/api/v1/jobs")).status_code == 401


@pytest.mark.asyncio
async def test_origin_and_csrf_protection(auth_client, pdf_bytes):
    await register(auth_client)
    response = await auth_client.post(
        "/api/v1/account/logout", headers={"Origin": "http://evil.example"}
    )
    assert response.status_code == 403
    auth_client.headers.pop("X-DistribuQueue")
    assert (await auth_client.post("/api/v1/account/logout")).status_code == 403
    assert (
        await auth_client.post(
            "/api/v1/jobs/upload", files={"file": ("test.pdf", pdf_bytes)}
        )
    ).status_code == 403
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                "/api/v1/jobs/1/ws",
                headers={
                    "Cookie": f"{COOKIE}={auth_client.cookies.get(COOKIE)}",
                    "Origin": "http://evil.example",
                },
            ):
                pass
        assert exc.value.code == 1008


@pytest.mark.asyncio
async def test_password_validation_never_echoes_secrets(auth_client):
    secret = "shortSECRET"
    response = await auth_client.post(
        "/api/v1/account/register", json={"username": "bad", "password": secret}
    )
    assert response.status_code == 422 and secret not in response.text
    assert "password_hash" not in (await auth_client.get("/openapi.json")).text


@pytest.mark.asyncio
async def test_generic_login_failure_and_username_uniqueness(auth_client):
    username = await register(auth_client)
    wrong = await auth_client.post(
        "/api/v1/account/login",
        json={"username": username, "password": "Incorrect test password"},
    )
    missing = await auth_client.post(
        "/api/v1/account/login",
        json={"username": "missing_" + uuid4().hex[:8], "password": PASSWORD},
    )
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json() == missing.json()
    duplicate = await auth_client.post(
        "/api/v1/account/register",
        json={"username": username.upper(), "password": PASSWORD},
    )
    assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_auth_rate_limit_is_atomic_and_fails_closed(auth_client, monkeypatch):
    from app.auth import rate_limit
    from starlette.requests import Request
    from fastapi import HTTPException

    request = Request({"type": "http", "headers": [], "client": (uuid4().hex, 123)})
    username = "rate_" + uuid4().hex
    outcomes = await asyncio.gather(
        *(rate_limit(request, username) for _ in range(20)), return_exceptions=True
    )
    assert sum(result is None for result in outcomes) == 10
    assert (
        sum(
            isinstance(result, HTTPException) and result.status_code == 429
            for result in outcomes
        )
        == 10
    )
    monkeypatch.setattr(settings, "REDIS_PORT", 1)
    with pytest.raises(HTTPException) as exc:
        await rate_limit(request, username)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_operator_assignment_is_explicit_and_preserves_legacy_jobs(auth_client):
    from scripts.assign_legacy_jobs import assign

    async with async_session_maker() as session:
        legacy = Job(filename="legacy.pdf", idempotency_key="legacy-key")
        session.add(legacy)
        await session.commit()
        job_id = legacy.id
    username = await register(auth_client)
    assert await assign(username) == 1
    assert (await auth_client.get(f"/api/v1/jobs/{job_id}")).status_code == 404
    assert await assign(username, apply=True) == 1
    assert (await auth_client.get(f"/api/v1/jobs/{job_id}")).status_code == 200


@pytest.mark.asyncio
async def test_logout_closes_existing_live_socket(auth_client, pdf_bytes, monkeypatch):
    from types import SimpleNamespace
    from itertools import count

    clock = count(0, 20)
    monkeypatch.setattr(
        "app.api.v1.jobs.time", SimpleNamespace(monotonic=lambda: next(clock))
    )
    await register(auth_client)
    job = await upload(auth_client, pdf_bytes)
    with TestClient(app) as client:
        with client.websocket_connect(
            f"/api/v1/jobs/{job['id']}/ws",
            headers={"Cookie": f"{COOKIE}={auth_client.cookies.get(COOKIE)}"},
        ) as socket:
            assert socket.receive_json()["job_id"] == job["id"]
            assert (await auth_client.post("/api/v1/account/logout")).status_code == 200
            with pytest.raises(WebSocketDisconnect) as exc:
                socket.receive_json()
            assert exc.value.code == 1008
