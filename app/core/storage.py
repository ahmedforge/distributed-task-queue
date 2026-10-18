"""Blocking storage operations; callers run these outside the event loop."""

import shutil
import tempfile
from pathlib import Path
from typing import Protocol, Literal

from app.core.config import settings

Artifact = Literal["input.pdf", "pages.zip"]


def job_directory(job_id: int) -> Path:
    return Path(settings.STORAGE_DIR).resolve() / str(int(job_id))


def input_path(job_id: int) -> Path:
    return job_directory(job_id) / "input.pdf"


def result_path(job_id: int) -> Path:
    return job_directory(job_id) / "pages.zip"


class Storage(Protocol):
    def put(self, job_id: int, name: Artifact, source: Path) -> None: ...
    def fetch(self, job_id: int, name: Artifact, destination: Path) -> None: ...
    def delete_job(self, job_id: int) -> None: ...


class LocalStorage:
    def put(self, job_id: int, name: Artifact, source: Path) -> None:
        destination = job_directory(job_id) / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Stage beside the destination for an atomic publish on the same filesystem.
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as f:
            staged = Path(f.name)
        try:
            shutil.copyfile(source, staged)
            staged.replace(destination)
        finally:
            staged.unlink(missing_ok=True)

    def fetch(self, job_id: int, name: Artifact, destination: Path) -> None:
        shutil.copyfile(job_directory(job_id) / name, destination)

    def delete_job(self, job_id: int) -> None:
        directory = job_directory(job_id)
        try:
            shutil.rmtree(directory)
        except FileNotFoundError:
            if directory.exists():
                raise


class S3Storage:
    def __init__(self):
        import boto3
        from botocore.config import Config

        self.client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            region_name=settings.S3_REGION,
            config=Config(
                s3={"addressing_style": "path"},
                connect_timeout=5,
                read_timeout=30,
                retries={"mode": "standard", "max_attempts": 3},
            ),
        )
        self.bucket = settings.S3_BUCKET

    def put(self, job_id: int, name: Artifact, source: Path) -> None:
        # Single PUT avoids abandoned multipart uploads when workers are killed.
        with source.open("rb") as body:
            self.client.put_object(
                Bucket=self.bucket, Key=f"{int(job_id)}/{name}", Body=body
            )

    def fetch(self, job_id: int, name: Artifact, destination: Path) -> None:
        from botocore.exceptions import ClientError

        try:
            self.client.download_file(
                self.bucket, f"{int(job_id)}/{name}", str(destination)
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                raise FileNotFoundError(name) from exc
            raise

    def delete_job(self, job_id: int) -> None:
        # These are the only persistent objects. Individual deletes surface errors
        # and are idempotent, so the durable deletion marker can retry safely.
        for name in ("input.pdf", "pages.zip"):
            self.client.delete_object(Bucket=self.bucket, Key=f"{int(job_id)}/{name}")


def get_storage() -> Storage:
    return S3Storage() if settings.STORAGE_BACKEND == "s3" else LocalStorage()


def initialize_bucket() -> None:
    """Provision the local demo bucket; application requests never create buckets."""
    from botocore.exceptions import ClientError

    storage = S3Storage()
    try:
        storage.client.head_bucket(Bucket=storage.bucket)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("404", "NoSuchBucket", "NotFound"):
            raise
        storage.client.create_bucket(Bucket=storage.bucket)
