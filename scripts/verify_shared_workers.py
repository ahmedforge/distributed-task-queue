"""Isolated two-worker MinIO load and survivor-only SIGKILL verification."""

import argparse
import asyncio
import json
from pathlib import Path
import re
import time


from benchmark import Benchmark, ROOT, command


class SharedWorkers(Benchmark):
    def __init__(self, args):
        super().__init__(args)
        self.worker_b = self.prefix + "-worker-b"
        self.minio = self.prefix + "-minio"
        self.env.extend(
            [
                "STORAGE_BACKEND=s3",
                "S3_ENDPOINT_URL=http://minio:9000",
                "S3_BUCKET=dtq-jobs",
                "S3_ACCESS_KEY=benchmark-user",
                "S3_SECRET_KEY=benchmark-password",
                "WORKER_CONCURRENCY=1",
            ]
        )

    async def run_app(self, name, *extra):
        if name == self.worker:
            extra = tuple("--cpus=1" if arg == "--cpus=2" else arg for arg in extra)
        if name in (self.worker, self.worker_b):
            extra = (*extra, "--verbose")
        # Deliberately no shared volume or host bind mount on API or workers.
        env = [arg for value in self.env for arg in ("-e", value)]
        return await command(
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--network",
            self.network,
            *env,
            *extra,
        )

    async def setup(self):
        try:
            await command("docker", "image", "inspect", self.args.minio_image)
        except RuntimeError:
            print(
                "Building pinned MinIO source (cold builds take several minutes)...",
                flush=True,
            )
            await command(
                "docker",
                "build",
                "-f",
                str(ROOT / "docker/minio.Dockerfile"),
                "-t",
                self.args.minio_image,
                str(ROOT),
            )
        await super().setup()
        await command(
            "docker",
            "run",
            "-d",
            "--name",
            self.minio,
            "--network",
            self.network,
            "--network-alias",
            "minio",
            "--cpus=1",
            "--memory=1g",
            "-v",
            self.volume + ":/data",
            "-e",
            "MINIO_ROOT_USER=benchmark-user",
            "-e",
            "MINIO_ROOT_PASSWORD=benchmark-password",
            self.args.minio_image,
            "server",
            "/data",
        )
        for _ in range(120):
            ready = await command(
                "docker",
                "exec",
                self.minio,
                "sh",
                "-c",
                "curl -fsS http://localhost:9000/minio/health/live && echo ready",
                check=False,
            )
            if ready == "ready":
                break
            await asyncio.sleep(0.5)
        else:
            raise RuntimeError("MinIO did not become ready")
        await command(
            "docker",
            "exec",
            self.api,
            "python",
            "-c",
            "from app.core.storage import initialize_bucket; initialize_bucket()",
        )
        await self.run_app(
            self.worker_b,
            "--cpus=1",
            "--memory=1g",
            self.args.image,
            "saq",
            "app.worker.settings",
        )
        for name in (self.api, self.worker, self.worker_b):
            mounts = json.loads(
                await command("docker", "inspect", "--format", "{{json .Mounts}}", name)
            )
            assert not mounts, f"Unexpected shared mount on {name}: {mounts}"
        self.report["environment"].update(
            {
                "storage_backend": "MinIO / S3",
                "api_worker_shared_mounts": False,
                "workers": 2,
                "worker_concurrency": 1,
                "minio_image_id": await command(
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{.Id}}",
                    self.args.minio_image,
                ),
                "limits": {
                    "each_worker": "1 CPU / 1 GiB",
                    "minio": "1 CPU / 1 GiB",
                    "api": "1 CPU / 512 MiB",
                    "postgres": "1 CPU / 512 MiB",
                    "redis": "0.5 CPU / 128 MiB",
                },
            }
        )
        print(
            "Two independent workers and MinIO ready; no shared API/worker mounts.",
            flush=True,
        )

    async def load(self, client, label, count):
        result = await super().load(client, label, count)
        if label == "load":
            expected = set(map(int, result["jobs_detail"]))
            participation = {}
            # Fetch each container log with stderr merged, without a shell.
            for role, name in (("worker_a", self.worker), ("worker_b", self.worker_b)):
                proc = await asyncio.create_subprocess_exec(
                    "docker",
                    "logs",
                    name,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                output, _ = await proc.communicate()
                assert proc.returncode == 0
                ids = (
                    set(
                        map(
                            int,
                            re.findall(
                                r"Successfully processed Job ID (\d+)", output.decode()
                            ),
                        )
                    )
                    & expected
                )
                participation[role] = sorted(ids)
                assert ids, f"{role} did not complete any measured jobs"
            assert set().union(*map(set, participation.values())) == expected
            result["completed_by_worker"] = participation
        return result

    async def crash(self, client):
        await command("docker", "stop", "-t", "10", self.worker, self.worker_b)
        records, tasks = await self.submit(client, "crash", 4, 50)
        await asyncio.gather(*tasks)
        assert len(records) == 4
        configure = """import asyncio,json,sys
from app.core.queue import queue
from saq.job import Status
async def main():
    ids=set(json.loads(sys.argv[1])); count=0
    async for job in queue.iter_jobs([Status.QUEUED]):
        if job.kwargs.get('payload',{}).get('id') in ids:
            job.timeout=30
            await job.update(); count+=1
    await queue.disconnect()
    assert count==len(ids)
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
                snapshot = await self.list_jobs(client)
                interrupted = [
                    i for i in records if snapshot[i]["status"] == "PROCESSING"
                ]
                if interrupted:
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
        after_kill = await self.list_jobs(client)
        interrupted = [
            i for i in interrupted if after_kill[i]["status"] == "PROCESSING"
        ]
        assert interrupted, "Crash check inconclusive: job finished before SIGKILL"
        # Remove the killed worker, including its private scratch filesystem.
        await command("docker", "rm", "-v", self.worker)
        await command("docker", "start", self.worker_b)
        print(
            f"Worker A removed after SIGKILL; worker B alone must recover {interrupted}.",
            flush=True,
        )
        await self.complete(client, records, [], deadline_seconds=300)
        elapsed = time.perf_counter() - killed_at
        await self.verify_archives(client, records, 50)
        assert all(records[i]["retry_count"] >= 1 for i in interrupted)
        result = {
            "accepted_jobs": 4,
            "pages_per_pdf": 50,
            "signal": "SIGKILL",
            "worker_exit_code": exit_code,
            "killed_worker_removed": True,
            "recovered_by": "worker_b_only",
            "interrupted_job_ids": interrupted,
            "completed_jobs": len(records),
            "verified_zip_archives": len(records),
            "failed_jobs": 0,
            "unaccounted_jobs": 0,
            "recovery_to_all_completed_seconds": round(elapsed, 4),
            "test_task_timeout_seconds": 30,
            "application_default_timeout_seconds": 1800,
            "sweep_interval_seconds": 60,
            "queue_data_retained": True,
            "object_storage_retained": True,
            "retry_counts": {str(i): r["retry_count"] for i, r in records.items()},
        }
        print(
            f"Survivor-only recovery passed: four valid ZIPs in {elapsed:.2f}s.",
            flush=True,
        )
        return result

    async def cleanup(self):
        for name in (self.worker_b, self.minio):
            await command("docker", "rm", "-f", "-v", name, check=False)
        await super().cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=12)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--image", default="dtq-shared-workers:local")
    parser.add_argument("--minio-image", default="dtq-minio:test")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "docs/benchmarks/shared-workers.json"
    )
    args = parser.parse_args()
    if args.jobs < 4 or args.concurrency < 1:
        parser.error("use at least four jobs and positive upload concurrency")
    asyncio.run(SharedWorkers(args).run())


if __name__ == "__main__":
    main()
