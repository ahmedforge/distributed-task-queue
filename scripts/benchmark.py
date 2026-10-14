"""Reproducible, isolated Docker load and SIGKILL checks (no production data)."""

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import platform
import time
from uuid import uuid4
from zipfile import ZipFile

import httpx

ROOT = Path(__file__).resolve().parents[1]


async def command(*args, check=True):
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await process.communicate()
    if check and process.returncode:
        raise RuntimeError(f"{args[:3]} failed: {err.decode()[-1500:]}")
    return out.decode().strip()


def pdf_bytes(pages):
    content = b"BT /F1 20 Tf 30 100 Td (DistribuQueue synthetic benchmark) Tj ET"
    kids = " ".join(f"{index + 5} 0 R" for index in range(pages))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream",
    ] + [
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 3 0 R >> >> /Contents 4 0 R >>"
    ] * pages
    result = b"%PDF-1.4\n"
    offsets = []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(result)
    result += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    result += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    result += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return result


def distribution(values):
    values = sorted(values)

    def percentile(fraction):
        position = (len(values) - 1) * fraction
        lower = math.floor(position)
        upper = math.ceil(position)
        return round(
            values[lower] + (values[upper] - values[lower]) * (position - lower), 4
        )

    return {
        "p50": percentile(0.5),
        "p95": percentile(0.95),
        "max": round(max(values), 4),
    }


class Benchmark:
    def __init__(self, args):
        self.args = args
        self.prefix = f"dtq-benchmark-{uuid4().hex[:8]}"
        self.network = self.prefix + "-network"
        self.volume = self.prefix + "-files"
        self.pg = self.prefix + "-postgres"
        self.redis = self.prefix + "-redis"
        self.api = self.prefix + "-api"
        self.worker = self.prefix + "-worker"
        self.env = [
            "POSTGRES_SERVER=postgres",
            "POSTGRES_PORT=5432",
            "POSTGRES_USER=postgres",
            "POSTGRES_PASSWORD=benchmark-password",
            "POSTGRES_DB=benchmark",
            "REDIS_HOST=redis",
            "REDIS_PORT=6379",
            "STORAGE_DIR=/app/data/jobs",
        ]
        self.report = {"started_at_utc": datetime.now(timezone.utc).isoformat()}

    async def run_app(self, name, *extra):
        env = [argument for value in self.env for argument in ("-e", value)]
        return await command(
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--network",
            self.network,
            "-v",
            self.volume + ":/app/data/jobs",
            *env,
            *extra,
        )

    async def setup(self):
        print("Building benchmark image from the current working tree...", flush=True)
        await command("docker", "build", "-t", self.args.image, str(ROOT))
        await command("docker", "network", "create", self.network)
        await command("docker", "volume", "create", self.volume)
        await command(
            "docker",
            "run",
            "-d",
            "--name",
            self.pg,
            "--network",
            self.network,
            "--network-alias",
            "postgres",
            "--cpus=1",
            "--memory=512m",
            "-e",
            "POSTGRES_PASSWORD=benchmark-password",
            "-e",
            "POSTGRES_DB=benchmark",
            "postgres:16-alpine",
        )
        await command(
            "docker",
            "run",
            "-d",
            "--name",
            self.redis,
            "--network",
            self.network,
            "--network-alias",
            "redis",
            "--cpus=0.5",
            "--memory=128m",
            "redis:7-alpine",
        )
        for _ in range(120):
            ready = await command(
                "docker",
                "exec",
                self.pg,
                "sh",
                "-c",
                "pg_isready -U postgres -q && echo ready",
                check=False,
            )
            if ready == "ready":
                break
            await asyncio.sleep(0.5)
        else:
            raise RuntimeError("Disposable PostgreSQL did not become ready")
        await self.run_app(
            self.api,
            "--network-alias",
            "api",
            "-p",
            "127.0.0.1::8000",
            "--cpus=1",
            "--memory=512m",
            self.args.image,
            "sh",
            "-c",
            "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000",
        )
        # PostgreSQL may still be initializing on a cold start. Restart the
        # disposable API if migration startup raced database readiness.
        state = json.loads(await command("docker", "inspect", self.api))[0]
        port = state["NetworkSettings"]["Ports"]["8000/tcp"][0]["HostPort"]
        self.url = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(timeout=2) as client:
            for _ in range(120):
                try:
                    response = await client.get(self.url + "/health")
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                running = await command(
                    "docker", "inspect", "--format", "{{.State.Running}}", self.api
                )
                if running == "false":
                    await command("docker", "start", self.api)
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError("Benchmark API did not become ready")
        await self.run_app(
            self.worker,
            "--cpus=2",
            "--memory=1g",
            self.args.image,
            "saq",
            "app.worker.settings",
        )
        source = hashlib.sha256()
        for path in sorted((ROOT / "app").rglob("*.py")):
            source.update(str(path.relative_to(ROOT)).encode())
            source.update(path.read_bytes())
        cpu = next(
            (
                line.split(":", 1)[1].strip()
                for line in Path("/proc/cpuinfo").read_text().splitlines()
                if line.startswith("model name")
            ),
            "unknown",
        )
        self.report["environment"] = {
            "source_commit": await command("git", "-C", str(ROOT), "rev-parse", "HEAD"),
            "app_source_sha256": source.hexdigest(),
            "image_id": await command(
                "docker", "image", "inspect", "--format", "{{.Id}}", self.args.image
            ),
            "platform": platform.platform(),
            "cpu_model": cpu,
            "host_logical_cpus": os.cpu_count(),
            "host_memory_kib": int(
                next(
                    line.split()[1]
                    for line in Path("/proc/meminfo").read_text().splitlines()
                    if line.startswith("MemTotal:")
                )
            ),
            "docker_version": await command(
                "docker", "version", "--format", "{{.Server.Version}}"
            ),
            "limits": {
                "worker": "2 CPUs / 1 GiB",
                "api": "1 CPU / 512 MiB",
                "postgres": "1 CPU / 512 MiB",
                "redis": "0.5 CPU / 128 MiB",
            },
            "worker_concurrency": 10,
            "upload_concurrency": self.args.concurrency,
            "other_host_services": "Existing user services remained running; this is not a dedicated benchmark host.",
        }
        print("Isolated API and worker ready.", flush=True)

    async def list_jobs(self, client):
        jobs = {}
        page = 1
        while True:
            response = await client.get(
                self.url + f"/api/v1/jobs?page={page}&page_size=100"
            )
            response.raise_for_status()
            body = response.json()
            jobs.update({job["id"]: job for job in body["items"]})
            if page * 100 >= body["total"]:
                return jobs
            page += 1

    async def submit(self, client, label, count, pages):
        semaphore = asyncio.Semaphore(self.args.concurrency)
        pdf = pdf_bytes(pages)
        records = {}

        async def upload(index):
            async with semaphore:
                started = time.perf_counter()
                response = await client.post(
                    self.url + "/api/v1/jobs/upload",
                    files={
                        "file": (
                            f"benchmark-{label}-{index}.pdf",
                            pdf,
                            "application/pdf",
                        )
                    },
                    data={
                        "idempotency_key": f"{self.prefix}-{label}-{index}",
                        "max_retries": "3",
                    },
                )
                response.raise_for_status()
                job = response.json()
                records[job["id"]] = {
                    "upload_started": started,
                    "accepted": time.perf_counter(),
                }

        tasks = [asyncio.create_task(upload(index)) for index in range(count)]
        return records, tasks

    async def complete(self, client, records, tasks, deadline_seconds=240):
        deadline = time.perf_counter() + deadline_seconds
        while True:
            jobs = await self.list_jobs(client)
            observed = time.perf_counter()
            for job_id, record in records.items():
                job = jobs.get(job_id)
                if job and job["status"] in ("COMPLETED", "FAILED"):
                    record.setdefault("observed_finished", observed)
                    record["status"] = job["status"]
                    record["retry_count"] = job["retry_count"]
            if all(task.done() for task in tasks):
                await asyncio.gather(*tasks)
                if records and all("status" in record for record in records.values()):
                    break
            if time.perf_counter() > deadline:
                raise RuntimeError("Timed out waiting for benchmark jobs")
            await asyncio.sleep(0.2)
        failures = [
            job_id
            for job_id, record in records.items()
            if record["status"] != "COMPLETED"
        ]
        if failures:
            raise RuntimeError(f"Benchmark jobs failed: {failures}")

    async def verify_archives(self, client, records, pages):
        for job_id in records:
            response = await client.get(self.url + f"/api/v1/jobs/{job_id}/result")
            response.raise_for_status()
            with ZipFile(BytesIO(response.content)) as archive:
                assert len(archive.namelist()) == pages
                assert archive.testzip() is None
                assert all(
                    archive.read(name).startswith(b"\x89PNG\r\n\x1a\n")
                    for name in archive.namelist()
                )

    async def load(self, client, label, count):
        started = time.perf_counter()
        records, tasks = await self.submit(client, label, count, 2)
        try:
            await self.complete(client, records, tasks)
        finally:
            await asyncio.gather(*tasks, return_exceptions=True)
        assert len(records) == count, "Accepted uploads did not have distinct job IDs"
        elapsed = time.perf_counter() - started
        await self.verify_archives(client, records, 2)
        result = {
            "jobs": count,
            "pages_per_pdf": 2,
            "input_bytes": len(pdf_bytes(2)),
            "duration_seconds": round(elapsed, 4),
            "completed_jobs_per_second": round(count / elapsed, 4),
            "completed": len(records),
            "failed": 0,
            "verified_zip_archives": len(records),
            "submission_latency_seconds": distribution(
                [r["accepted"] - r["upload_started"] for r in records.values()]
            ),
            "observed_end_to_end_seconds": distribution(
                [r["observed_finished"] - r["upload_started"] for r in records.values()]
            ),
            "jobs_detail": {
                str(key): {
                    "status": r["status"],
                    "retry_count": r["retry_count"],
                    "observed_latency_seconds": round(
                        r["observed_finished"] - r["upload_started"], 4
                    ),
                }
                for key, r in records.items()
            },
        }
        print(
            f"{label}: {count} completed, {count / elapsed:.2f} jobs/s, {elapsed:.2f}s; ZIPs verified.",
            flush=True,
        )
        return result

    async def crash(self, client):
        await command("docker", "stop", "-t", "10", self.worker)
        records, tasks = await self.submit(client, "crash", 4, 10)
        await asyncio.gather(*tasks)
        assert len(records) == 4, "Crash batch did not have four distinct job IDs"
        # Set a short timeout ONLY on these queued benchmark jobs before they
        # run. SAQ's normal worker implementation and 60-second sweeper remain.
        configure = """import asyncio, json, sys
from app.core.queue import queue
from saq.job import Status
async def main():
    ids = set(json.loads(sys.argv[1]))
    count = 0
    async for job in queue.iter_jobs([Status.QUEUED]):
        if job.kwargs.get("payload", {}).get("id") in ids:
            job.timeout = 30
            await job.update()
            count += 1
    await queue.disconnect()
    assert count == len(ids), (count, len(ids))
    print(count)
asyncio.run(main())"""
        await command(
            "docker",
            "exec",
            self.api,
            "python",
            "-c",
            configure,
            json.dumps(list(records)),
        )
        await command("docker", "start", self.worker)
        async with asyncio.timeout(30):
            while True:
                jobs = await self.list_jobs(client)
                if any(jobs[job_id]["status"] == "PROCESSING" for job_id in records):
                    break
                await asyncio.sleep(0.02)
        killed_at = time.perf_counter()
        await command("docker", "kill", "--signal", "KILL", self.worker)
        exit_code = int(
            await command(
                "docker", "inspect", "--format", "{{.State.ExitCode}}", self.worker
            )
        )
        assert exit_code == 137
        snapshot = await self.list_jobs(client)
        interrupted = [
            job_id for job_id in records if snapshot[job_id]["status"] == "PROCESSING"
        ]
        assert (
            interrupted
        ), "Jobs finished before the worker was killed; crash check is inconclusive"
        print(
            f"SIGKILL confirmed (exit 137); {len(interrupted)} jobs interrupted. Restarting worker and waiting for normal SAQ sweep/retry...",
            flush=True,
        )
        await command("docker", "start", self.worker)
        await self.complete(client, records, [])
        recovery_seconds = time.perf_counter() - killed_at
        await self.verify_archives(client, records, 10)
        assert any(records[job_id]["retry_count"] >= 1 for job_id in interrupted)
        result = {
            "accepted_jobs": 4,
            "pages_per_pdf": 10,
            "signal": "SIGKILL",
            "worker_exit_code": exit_code,
            "interrupted_job_ids": interrupted,
            "completed_jobs": len(records),
            "failed_jobs": 0,
            "unaccounted_jobs": 0,
            "verified_zip_archives": len(records),
            "recovery_to_all_completed_seconds": round(recovery_seconds, 4),
            "test_task_timeout_seconds": 30,
            "application_default_timeout_seconds": 1800,
            "sweep_interval_seconds": 60,
            "queue_data_retained": True,
            "retry_counts": {
                str(key): value["retry_count"] for key, value in records.items()
            },
        }
        print(
            f"Crash check: all 4 jobs completed with valid ZIPs after {recovery_seconds:.2f}s; no accepted jobs unaccounted for in this run.",
            flush=True,
        )
        return result

    async def cleanup(self):
        for name in (self.worker, self.api, self.redis, self.pg):
            await command("docker", "rm", "-f", "-v", name, check=False)
        await command("docker", "volume", "rm", self.volume, check=False)
        await command("docker", "network", "rm", self.network, check=False)

    async def run(self):
        try:
            await self.setup()
            async with httpx.AsyncClient(timeout=30) as client:
                self.report["warmup"] = await self.load(client, "warmup", 3)
                self.report["load"] = await self.load(client, "load", self.args.jobs)
                self.report["crash"] = await self.crash(client)
            self.report["passed"] = True
        except BaseException as exc:
            self.report["passed"] = False
            self.report["error"] = f"{type(exc).__name__}: {exc}"
            for label, name in (("api", self.api), ("worker", self.worker)):
                self.report[label + "_failure_logs"] = await command(
                    "docker", "logs", "--tail", "60", name, check=False
                )
            raise
        finally:
            self.report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
            self.args.output.parent.mkdir(parents=True, exist_ok=True)
            self.args.output.write_text(json.dumps(self.report, indent=2) + "\n")
            await self.cleanup()
            print(
                f"Report: {self.args.output}; disposable containers/volumes/network removed.",
                flush=True,
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=40)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--image", default="dtq-benchmark:local")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "docs/benchmarks/latest.json"
    )
    args = parser.parse_args()
    if args.jobs < 1 or args.concurrency < 1:
        parser.error("jobs and concurrency must be positive")
    asyncio.run(Benchmark(args).run())


if __name__ == "__main__":
    main()
