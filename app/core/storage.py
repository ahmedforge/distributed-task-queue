from pathlib import Path

from app.core.config import settings


def job_directory(job_id: int) -> Path:
    return Path(settings.STORAGE_DIR).resolve() / str(int(job_id))


def input_path(job_id: int) -> Path:
    return job_directory(job_id) / "input.pdf"


def result_path(job_id: int) -> Path:
    return job_directory(job_id) / "pages.zip"
