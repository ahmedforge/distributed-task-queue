"""Render PDFs with Poppler in bounded subprocesses."""

import asyncio
import os
import re
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from app.core.config import settings
from app.core.storage import get_storage


async def run_poppler(*args: str) -> bytes:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "LC_ALL": "C"},
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise ValueError(
            "PDF processing failed: " + stderr.decode(errors="replace")[:500]
        )
    return stdout


class BoundedWriter:
    def __init__(self, stream, limit):
        self.stream, self.limit = stream, limit

    def write(self, data):
        if self.stream.tell() + len(data) > self.limit:
            raise ValueError("Rendered ZIP exceeds the result size limit.")
        return self.stream.write(data)

    def __getattr__(self, name):
        return getattr(self.stream, name)


def write_archive(directory: Path, destination: Path, max_bytes: int) -> int:
    with destination.open("w+b") as stream:
        with ZipFile(BoundedWriter(stream, max_bytes), "w", ZIP_DEFLATED) as archive:
            for image in sorted(directory.glob("page-*.png")):
                archive.write(image, arcname=image.name)
    return destination.stat().st_size


async def render_pdf(
    job_id: int, on_progress, *, max_result_bytes: int | None = None
) -> int:
    # Scratch is private to this attempt; no shared worker filesystem is needed.
    with tempfile.TemporaryDirectory(prefix=f"dtq-{job_id}-") as scratch:
        return await _render_pdf(
            job_id,
            on_progress,
            Path(scratch),
            max_result_bytes or settings.MAX_RESULT_BYTES,
        )


async def _render_pdf(
    job_id: int, on_progress, directory: Path, max_result_bytes: int
) -> int:
    storage = get_storage()
    source = directory / "input.pdf"
    try:
        await asyncio.to_thread(storage.fetch, job_id, "input.pdf", source)
    except FileNotFoundError:
        raise ValueError(
            "PDF input is missing. Submit a file through the upload endpoint."
        )
    info = (await run_poppler("pdfinfo", str(source))).decode(errors="replace")
    if re.search(r"^Encrypted:\s+yes", info, re.MULTILINE):
        raise ValueError("Encrypted PDFs are not supported.")
    match = re.search(r"^Pages:\s+(\d+)", info, re.MULTILINE)
    if not match:
        raise ValueError("Cannot determine the PDF page count.")
    pages = int(match.group(1))
    if not 1 <= pages <= settings.MAX_PDF_PAGES:
        raise ValueError(f"PDF must contain 1 to {settings.MAX_PDF_PAGES} pages.")

    await on_progress(10)
    # Fresh temporary outputs prevent retries from including stale pages.
    for page in range(1, pages + 1):
        prefix = directory / f"page-{page:04d}"
        await run_poppler(
            "pdftoppm",
            "-f",
            str(page),
            "-l",
            str(page),
            "-singlefile",
            "-scale-to",
            "1600",
            "-png",
            str(source),
            str(prefix),
        )
        await on_progress(10 + int(80 * page / pages))
    archive = directory / "pages.zip"
    result_size = await asyncio.to_thread(
        write_archive, directory, archive, max_result_bytes
    )
    await asyncio.to_thread(storage.put, job_id, "pages.zip", archive)
    return result_size
