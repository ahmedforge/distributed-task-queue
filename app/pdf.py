"""Render PDFs with Poppler in bounded subprocesses."""

import asyncio
import os
import re
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from app.core.config import settings
from app.core.storage import input_path, job_directory, result_path


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


def write_archive(directory: Path, destination: Path) -> None:
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for image in sorted(directory.glob("page-*.png")):
            archive.write(image, arcname=image.name)


async def render_pdf(job_id: int, on_progress) -> None:
    source = input_path(job_id)
    if not source.is_file():
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
    with tempfile.TemporaryDirectory(dir=job_directory(job_id)) as scratch:
        directory = Path(scratch)
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
        await asyncio.to_thread(write_archive, directory, archive)
        archive.replace(result_path(job_id))
