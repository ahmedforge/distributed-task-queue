"""Submit synthetic PDFs and record real KEDA scale-up and scale-down."""

import argparse
import asyncio
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import re
import time
from uuid import uuid4
from zipfile import ZipFile

import httpx
from benchmark import ROOT, pdf_bytes


async def verify(args):
    base = [
        args.kubectl,
        "--kubeconfig",
        str(args.kubeconfig),
        "--context",
        "kind-dtq-v2",
        "-n",
        "dtq",
    ]

    async def k(*command):
        p = await asyncio.create_subprocess_exec(
            *base,
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await p.communicate()
        if p.returncode:
            raise RuntimeError(err.decode()[-1500:])
        return out.decode()

    async def runtime_worker_count():
        # A force-deleted pod can leave a container temporarily absent from the API.
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            "dtq-v2-control-plane",
            "crictl",
            "ps",
            "--name",
            "worker",
            "-o",
            "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode:
            raise RuntimeError(err.decode()[-1000:])
        return sum(
            c.get("labels", {}).get("io.kubernetes.pod.namespace") == "dtq"
            for c in json.loads(out)["containers"]
        )

    report = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "jobs": args.jobs,
        "pages_per_pdf": 20,
        "samples": [],
        "limits": "Single kind node on a shared laptop; not production capacity or multi-node HA.",
    }
    forward = None
    drain_task = None
    try:
        scaled = json.loads(
            await k("get", "scaledobject", "worker-queue", "-o", "json")
        )
        assert any(
            c["type"] == "Ready" and c["status"] == "True"
            for c in scaled["status"]["conditions"]
        )
        nodes = json.loads(await k("get", "nodes", "-o", "json"))
        report["kubernetes_versions"] = [
            n["status"]["nodeInfo"]["kubeletVersion"] for n in nodes["items"]
        ]
        report["scaledobject_spec"] = scaled["spec"]
        dep = json.loads(await k("get", "deployment", "worker", "-o", "json"))
        assert (
            dep["spec"]["replicas"] == dep["status"].get("readyReplicas", 0) == 1
        ), "Start with one ready worker"
        report["initial_runtime_worker_containers"] = await runtime_worker_count()
        assert (
            report["initial_runtime_worker_containers"] == 1
        ), "Old worker containers remain; start with a clean runtime"
        forward = await asyncio.create_subprocess_exec(
            *base,
            "port-forward",
            "--address",
            "127.0.0.1",
            "service/frontend",
            ":8080",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        async with asyncio.timeout(60):
            while True:
                line = await forward.stdout.readline()
                if not line:
                    raise RuntimeError("Frontend port-forward exited")
                match = re.search(rb"Forwarding from 127.0.0.1:(\d+)", line)
                if match:
                    url = "http://127.0.0.1:" + match.group(1).decode()
                    break

        async def drain_forward_output():
            async for _ in forward.stdout:
                pass

        drain_task = asyncio.create_task(drain_forward_output())
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(url + "/")
            response.raise_for_status()
            assert "<html" in response.text.lower()
            prefix = uuid4().hex
            semaphore = asyncio.Semaphore(4)

            async def upload(i):
                async with semaphore:
                    result = await client.post(
                        url + "/api/v1/jobs/upload",
                        files={
                            "file": (f"keda-{i}.pdf", pdf_bytes(20), "application/pdf")
                        },
                        data={"idempotency_key": f"{prefix}-{i}"},
                    )
                    result.raise_for_status()
                    return result.json()["id"]

            started = time.monotonic()
            tasks = [asyncio.create_task(upload(i)) for i in range(args.jobs)]
            ids = []
            seen_scale_up = False
            verified_outputs = False
            try:
                async with asyncio.timeout(720):
                    while True:
                        dep = json.loads(
                            await k("get", "deployment", "worker", "-o", "json")
                        )
                        pods = json.loads(
                            await k("get", "pods", "-l", "app=worker", "-o", "json")
                        )
                        queued = int(
                            (
                                await k(
                                    "exec",
                                    "deployment/redis",
                                    "--",
                                    "redis-cli",
                                    "LLEN",
                                    "saq:tasks:queued",
                                )
                            ).strip()
                        )
                        live = [
                            p
                            for p in pods["items"]
                            if not p["metadata"].get("deletionTimestamp")
                        ]
                        sample = {
                            "elapsed_seconds": round(time.monotonic() - started, 2),
                            "queued": queued,
                            "desired_workers": dep["spec"]["replicas"],
                            "ready_workers": dep["status"].get("readyReplicas", 0),
                            "nonterminating_worker_pods": len(live),
                            "total_worker_pods": len(pods["items"]),
                        }
                        report["samples"].append(sample)
                        print(json.dumps(sample), flush=True)
                        if sample["ready_workers"] >= 2:
                            seen_scale_up = True
                        if all(t.done() for t in tasks):
                            ids = await asyncio.gather(*tasks)
                            jobs = []
                            for job_id in ids:
                                response = await client.get(
                                    url + f"/api/v1/jobs/{job_id}"
                                )
                                response.raise_for_status()
                                jobs.append(response.json())
                            assert not any(
                                j["status"] == "FAILED" for j in jobs
                            ), "A workload job failed"
                            if (
                                all(j["status"] == "COMPLETED" for j in jobs)
                                and not verified_outputs
                            ):
                                for job_id in ids:
                                    response = await client.get(
                                        url + f"/api/v1/jobs/{job_id}/result"
                                    )
                                    response.raise_for_status()
                                    with ZipFile(BytesIO(response.content)) as archive:
                                        assert (
                                            len(archive.namelist()) == 20
                                            and archive.testzip() is None
                                        )
                                        assert all(
                                            archive.read(n).startswith(
                                                b"\x89PNG\r\n\x1a\n"
                                            )
                                            for n in archive.namelist()
                                        )
                                from websockets.asyncio.client import connect

                                ws_url = (
                                    url.replace("http://", "ws://", 1)
                                    + f"/api/v1/jobs/{ids[0]}/ws"
                                )
                                async with connect(
                                    ws_url, open_timeout=20
                                ) as websocket:
                                    snapshot = json.loads(
                                        await asyncio.wait_for(
                                            websocket.recv(), timeout=20
                                        )
                                    )
                                    assert (
                                        snapshot["job_id"] == ids[0]
                                        and snapshot["status"] == "COMPLETED"
                                    )
                                report["websocket_snapshot_verified"] = True
                                verified_outputs = True
                                report["all_completed_seconds"] = round(
                                    time.monotonic() - started, 2
                                )
                                print(
                                    "All workload ZIPs verified; waiting for KEDA scale-down.",
                                    flush=True,
                                )
                        if (
                            verified_outputs
                            and seen_scale_up
                            and sample["desired_workers"]
                            == sample["ready_workers"]
                            == 1
                            and sample["total_worker_pods"] == 1
                        ):
                            break
                        await asyncio.sleep(3)
            finally:
                await asyncio.gather(*tasks, return_exceptions=True)
            assert seen_scale_up and verified_outputs
            report["final_runtime_worker_containers"] = await runtime_worker_count()
            assert report["final_runtime_worker_containers"] == 1
            report.update(
                {
                    "passed": True,
                    "job_ids": ids,
                    "verified_zip_archives": len(ids),
                    "peak_ready_workers": max(
                        s["ready_workers"] for s in report["samples"]
                    ),
                    "returned_to_one_worker": True,
                }
            )
    except BaseException as exc:
        report.update({"passed": False, "error": f"{type(exc).__name__}: {exc}"})
        raise
    finally:
        if forward and forward.returncode is None:
            forward.terminate()
            await forward.wait()
        if drain_task:
            await drain_task
        report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--kubectl", default="kubectl")
    p.add_argument("--kubeconfig", type=Path, default=ROOT / ".local-k8s/kubeconfig")
    p.add_argument("--jobs", type=int, default=16)
    p.add_argument("--output", type=Path, default=ROOT / "docs/benchmarks/keda.json")
    args = p.parse_args()
    if args.jobs < 8:
        p.error("use at least eight jobs to create a measurable backlog")
    asyncio.run(verify(args))


if __name__ == "__main__":
    main()
