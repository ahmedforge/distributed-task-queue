import pytest
import pytest_asyncio
from app.core.config import settings
from app.core.storage import input_path
from httpx import ASGITransport, AsyncClient
from sqlmodel import SQLModel

from app.core.database import engine
from app.core.queue import queue
from app.main import app


@pytest_asyncio.fixture(autouse=True)
async def reset_test_environment():
    # 1. Setup DB Schema
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    yield

    # 2. Teardown DB Schema & Connection Pool
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    await engine.dispose()

    # 3. Teardown SAQ / Redis Connection Pool
    if hasattr(queue, "disconnect"):
        await queue.disconnect()
    elif hasattr(queue, "redis") and queue.redis:
        await queue.redis.aclose()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path / "jobs"))


def make_pdf(page_count=1):
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f'<< /Type /Pages /Kids [3 0 R {"6 0 R" if page_count == 2 else ""}] /Count {page_count} >>'.encode(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    content = b"BT /F1 20 Tf 30 100 Td (Queue rendering test) Tj ET"
    objects.append(
        b"<< /Length "
        + str(len(content)).encode()
        + b" >>\nstream\n"
        + content
        + b"\nendstream"
    )
    if page_count == 2:
        objects.append(objects[2])
    data = b"%PDF-1.4\n"
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += str(index).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref = len(data)
    data += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets[1:]:
        data += f"{offset:010d} 00000 n \n".encode()
    data += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return data


@pytest.fixture
def pdf_bytes():
    return make_pdf()


@pytest.fixture
def two_page_pdf():
    return make_pdf(2)


@pytest.fixture
def store_pdf(pdf_bytes):
    def store(job_id, corrupted=False):
        source = input_path(job_id)
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"%PDF-1.4\nbroken" if corrupted else pdf_bytes)
        return source

    return store
