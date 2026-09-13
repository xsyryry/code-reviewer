#!/usr/bin/env python3
"""Prewarm official100-new retry92 Docker environments without running reviewers."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RETRY_ROOT = PROJECT_ROOT / "outputs/official100-new/retry92"
PREWARM_ROOT = PROJECT_ROOT / "outputs/official100-new/prewarm"
STATUS_PATH = PREWARM_ROOT / "status.jsonl"
SUMMARY_PATH = PREWARM_ROOT / "summary.json"
REPORT_PATH = PREWARM_ROOT / "report.md"

COMPOSE_BASE = PROJECT_ROOT / "harbor/src/harbor/environments/docker/docker-compose-base.yaml"
COMPOSE_BUILD = PROJECT_ROOT / "harbor/src/harbor/environments/docker/docker-compose-build.yaml"
COMPOSE_PREBUILT = PROJECT_ROOT / "harbor/src/harbor/environments/docker/docker-compose-prebuilt.yaml"
COMPOSE_UV_CACHE = PROJECT_ROOT / "harbor/src/harbor/environments/docker/docker-compose-uv-cache.yaml"
SWE_BENCH_PLATFORM = "linux/amd64"

OPENHANDS_INSTALL = (
    "set -euo pipefail; "
    '[ -f /opt/openhands-sdk-venv/bin/python ] && '
    '/opt/openhands-sdk-venv/bin/python -c "import openhands.sdk; import openhands.tools.file_editor" '
    "2>/dev/null || ("
    "mkdir -p /opt/openhands-sdk-venv && chown root:root /opt/openhands-sdk-venv && "
    "curl -LsSf https://astral.sh/uv/install.sh | sh && "
    'if [ -f "$HOME/.local/bin/env" ]; then source "$HOME/.local/bin/env"; fi && '
    "uv python install 3.12 && "
    "uv venv /opt/openhands-sdk-venv --python 3.12 && "
    "source /opt/openhands-sdk-venv/bin/activate && "
    "export PIP_DEFAULT_TIMEOUT=120 && "
    "uv pip install openhands-sdk==1.16.1 openhands-tools==1.16.1 'litellm<=1.82.2'"
    ")"
)


def sanitize_image_name(name: str) -> str:
    name = name.lower()
    if not re.match(r"^[a-z0-9]", name):
        name = "0" + name
    return re.sub(r"[^a-z0-9._-]", "-", name)


def sanitize_project_name(name: str) -> str:
    name = name.lower()
    if not re.match(r"^[a-z0-9]", name):
        name = "0" + name
    return re.sub(r"[^a-z0-9_-]", "-", name)


def run_cmd(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )


def extract_base_image(task_dir: Path) -> str:
    dockerfile = task_dir / "environment/Dockerfile"
    for line in dockerfile.read_text().splitlines():
        line = line.strip()
        if line.startswith("FROM "):
            return line.split()[1]
    raise ValueError(f"No FROM line in {dockerfile}")


def load_task_config(task_dir: Path) -> tuple[int, str, int]:
    data = tomllib.loads((task_dir / "task.toml").read_text())
    env = data.get("environment", {})
    cpus = int(env.get("cpus", 1))
    memory = str(env.get("memory", "4G"))
    memory_mb = parse_memory_mb(memory)
    build_timeout = int(float(env.get("build_timeout_sec", 1800.0)))
    return cpus, f"{memory_mb}M", build_timeout


def parse_memory_mb(value: str) -> int:
    text = value.strip().upper()
    if text.endswith("G"):
        return int(float(text[:-1]) * 1024)
    if text.endswith("M"):
        return int(float(text[:-1]))
    return int(float(text))


def compose_env(task_dir: Path, trial_dir: Path, instance_id: str) -> dict[str, str]:
    cpus, memory, _ = load_task_config(task_dir)
    env = os.environ.copy()
    env.update(
        {
            "DOCKER_DEFAULT_PLATFORM": SWE_BENCH_PLATFORM,
            "MAIN_IMAGE_NAME": sanitize_image_name(f"hb__{instance_id}"),
            "CONTEXT_DIR": str((task_dir / "environment").resolve()),
            "HOST_VERIFIER_LOGS_PATH": str((trial_dir / "verifier").resolve()),
            "HOST_AGENT_LOGS_PATH": str((trial_dir / "agent").resolve()),
            "HOST_ARTIFACTS_PATH": str((trial_dir / "artifacts").resolve()),
            "ENV_VERIFIER_LOGS_PATH": "/logs/verifier",
            "ENV_AGENT_LOGS_PATH": "/logs/agent",
            "ENV_ARTIFACTS_PATH": "/artifacts",
            "CPUS": str(cpus),
            "MEMORY": memory,
        }
    )
    uv_cache = Path.home() / ".cache/uv"
    if uv_cache.is_dir():
        env["HOST_UV_CACHE_PATH"] = str(uv_cache.resolve())
    return env


def compose_base_cmd(project_name: str, task_dir: Path) -> list[str]:
    cmd = [
        "docker",
        "compose",
        "--project-name",
        project_name,
        "--project-directory",
        str((task_dir / "environment").resolve()),
        "-f",
        str(COMPOSE_BASE.resolve()),
        "-f",
        str(COMPOSE_BUILD.resolve()),
    ]
    if (Path.home() / ".cache/uv").is_dir():
        cmd += ["-f", str(COMPOSE_UV_CACHE.resolve())]
    return cmd


def classify_failure(text: str) -> str:
    low = text.lower()
    if "403 forbidden" in low or "daocloud" in low:
        return "network/proxy: registry mirror 403"
    if "no matching manifest" in low and "linux/arm64" in low:
        return "docker: platform mismatch, expected linux/amd64"
    if "connect: connection refused" in low or "127.0.0.1:7897" in low:
        return "network/proxy: local docker proxy refused connection"
    if "unexpected eof" in low or "short read" in low:
        return "network/proxy: interrupted docker layer download"
    if "proxyconnect" in low or "i/o timeout" in low or "deadlineexceeded" in low:
        return "network/proxy: docker proxy timeout"
    if "no space left" in low:
        return "docker: no space left"
    if "failed commit on ref" in low or "commit failed: rename" in low:
        return "docker: containerd layer commit failed"
    if "connection refused" in low or "could not resolve" in low or "temporary failure" in low:
        return "network: connection/dns failure"
    if "agent setup timed out" in low or "timed out" in low:
        return "timeout"
    if "uv pip install" in low or "openhands-sdk" in low:
        return "openhands dependency setup failed"
    return "unknown"


def load_existing_status() -> dict[str, dict]:
    latest: dict[str, dict] = {}
    if not STATUS_PATH.exists():
        return latest
    for line in STATUS_PATH.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        latest[rec["instance_id"]] = rec
    return latest


def count_attempts() -> dict[str, int]:
    counts: dict[str, int] = {}
    if not STATUS_PATH.exists():
        return counts
    for line in STATUS_PATH.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        counts[rec["instance_id"]] = max(counts.get(rec["instance_id"], 0), int(rec.get("attempt", 0)))
    return counts


def append_status(rec: dict) -> None:
    PREWARM_ROOT.mkdir(parents=True, exist_ok=True)
    with STATUS_PATH.open("a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


def prewarm_one(task_info: dict, attempt: int, timeout_scale: float) -> dict:
    instance_id = task_info["instance_id"]
    task_dir = (PROJECT_ROOT / task_info["task_path"]).resolve()
    base_image = extract_base_image(task_dir)
    trial_dir = PREWARM_ROOT / "trials" / instance_id
    if trial_dir.exists():
        shutil.rmtree(trial_dir)
    for sub in ["agent", "verifier", "artifacts"]:
        (trial_dir / sub).mkdir(parents=True, exist_ok=True)

    project_name = sanitize_project_name(f"prewarm__{instance_id}")
    env = compose_env(task_dir, trial_dir, instance_id)
    _, _, build_timeout = load_task_config(task_dir)
    build_timeout = int(build_timeout * timeout_scale)
    started = time.time()
    rec = {
        "instance_id": instance_id,
        "attempt": attempt,
        "docker_base_image": base_image,
        "pull_status": "pending",
        "build_status": "pending",
        "container_start_status": "pending",
        "openhands_setup_status": "pending",
        "duration_seconds": None,
        "failure_reason": None,
        "ready_for_review": False,
    }

    try:
        pull = run_cmd(["docker", "pull", "--platform", SWE_BENCH_PLATFORM, base_image], timeout=build_timeout)
        (trial_dir / "docker_pull.log").write_text(pull.stdout or "")
        if pull.returncode != 0:
            rec["pull_status"] = "failed"
            rec["build_status"] = "skipped"
            rec["container_start_status"] = "skipped"
            rec["openhands_setup_status"] = "skipped"
            rec["failure_reason"] = classify_failure(pull.stdout or "")
            return rec
        rec["pull_status"] = "ready"

        compose = compose_base_cmd(project_name, task_dir)
        build = run_cmd(compose + ["build"], env=env, timeout=build_timeout)
        (trial_dir / "docker_build.log").write_text(build.stdout or "")
        if build.returncode != 0:
            rec["build_status"] = "failed"
            rec["container_start_status"] = "skipped"
            rec["openhands_setup_status"] = "skipped"
            rec["failure_reason"] = classify_failure(build.stdout or "")
            return rec
        rec["build_status"] = "ready"

        run_cmd(compose + ["down", "--remove-orphans"], env=env, timeout=120)
        up = run_cmd(compose + ["up", "--detach", "--wait"], env=env, timeout=300)
        (trial_dir / "docker_up.log").write_text(up.stdout or "")
        if up.returncode != 0:
            rec["container_start_status"] = "failed"
            rec["openhands_setup_status"] = "skipped"
            rec["failure_reason"] = classify_failure(up.stdout or "")
            return rec
        chmod = run_cmd(compose + ["exec", "main", "bash", "-c", "chmod 777 /logs/agent /logs/verifier && cd /testbed && pwd"], env=env, timeout=120)
        (trial_dir / "container_validate.log").write_text(chmod.stdout or "")
        if chmod.returncode != 0:
            rec["container_start_status"] = "failed"
            rec["openhands_setup_status"] = "skipped"
            rec["failure_reason"] = classify_failure(chmod.stdout or "")
            return rec
        rec["container_start_status"] = "ready"

        setup = run_cmd(compose + ["exec", "main", "bash", "-c", OPENHANDS_INSTALL], env=env, timeout=1080)
        (trial_dir / "openhands_setup.log").write_text(setup.stdout or "")
        if setup.returncode != 0:
            rec["openhands_setup_status"] = "failed"
            rec["failure_reason"] = classify_failure(setup.stdout or "")
            return rec
        rec["openhands_setup_status"] = "ready"
        rec["ready_for_review"] = True
        return rec
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        rec["failure_reason"] = f"timeout: {classify_failure(output)}"
        if rec["pull_status"] == "pending":
            rec["pull_status"] = "failed"
        elif rec["build_status"] == "pending":
            rec["build_status"] = "failed"
        elif rec["container_start_status"] == "pending":
            rec["container_start_status"] = "failed"
        else:
            rec["openhands_setup_status"] = "failed"
        return rec
    finally:
        rec["duration_seconds"] = round(time.time() - started, 3)
        try:
            run_cmd(compose_base_cmd(project_name, task_dir) + ["down"], env=env, timeout=180)
        except Exception:
            pass


def summarize(records: list[dict], disk_before: str | None = None, disk_after: str | None = None) -> dict:
    latest = {r["instance_id"]: r for r in records}
    ready = [r for r in latest.values() if r.get("ready_for_review")]
    failed = [r for r in latest.values() if not r.get("ready_for_review")]
    def count_status(field: str) -> int:
        return sum(1 for r in failed if r.get(field) == "failed")
    network = sum(1 for r in failed if "network" in str(r.get("failure_reason", "")) or "proxy" in str(r.get("failure_reason", "")))
    unknown = sum(1 for r in failed if str(r.get("failure_reason", "")).startswith("unknown") or not r.get("failure_reason"))
    summary = {
        "total": len(latest),
        "ready": len(ready),
        "failed": len(failed),
        "docker_pull_failures": count_status("pull_status"),
        "docker_build_failures": count_status("build_status"),
        "container_start_failures": count_status("container_start_status"),
        "openhands_setup_failures": count_status("openhands_setup_status"),
        "network_proxy_failures": network,
        "unknown_failures": unknown,
        "disk_before": disk_before,
        "disk_after": disk_after,
    }
    return summary


def write_summary(all_records: list[dict], disk_before: str | None, disk_after: str | None, concurrency: int) -> None:
    summary = summarize(all_records, disk_before, disk_after)
    summary["prewarm_concurrency"] = concurrency
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2) + "\n")
    lines = [
        "# Retry92 Prewarm Report",
        "",
        f"- READY: `{summary['ready']}`",
        f"- FAILED: `{summary['failed']}`",
        f"- Docker pull failures: `{summary['docker_pull_failures']}`",
        f"- Docker build failures: `{summary['docker_build_failures']}`",
        f"- Container start failures: `{summary['container_start_failures']}`",
        f"- OpenHands setup failures: `{summary['openhands_setup_failures']}`",
        f"- Network/proxy failures: `{summary['network_proxy_failures']}`",
        f"- Unknown: `{summary['unknown_failures']}`",
        f"- Prewarm concurrency: `{concurrency}`",
        "",
        "## Failed Tasks",
    ]
    latest = {r["instance_id"]: r for r in all_records}
    for r in sorted(latest.values(), key=lambda x: x["instance_id"]):
        if not r.get("ready_for_review"):
            lines.append(f"- `{r['instance_id']}`: {r.get('failure_reason')}")
    REPORT_PATH.write_text("\n".join(lines) + "\n")


def docker_system_df() -> str:
    proc = run_cmd(["docker", "system", "df"], timeout=120)
    return proc.stdout or ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--initial-concurrency", type=int, default=2)
    parser.add_argument("--max-concurrency", type=int, default=4)
    args = parser.parse_args()

    manifest = json.loads((RETRY_ROOT / "manifest.json").read_text())
    tasks = [
        {"instance_id": item["instance_id"], "task_path": item["source_task_path"]}
        for item in manifest["tasks"]
    ]
    if args.limit:
        tasks = tasks[: args.limit]

    PREWARM_ROOT.mkdir(parents=True, exist_ok=True)
    disk_before = docker_system_df()
    (PREWARM_ROOT / "docker_system_df_before.txt").write_text(disk_before)

    existing = load_existing_status()
    attempts = count_attempts()
    pending = []
    for task in tasks:
        iid = task["instance_id"]
        if existing.get(iid, {}).get("ready_for_review"):
            continue
        if attempts.get(iid, 0) >= 2:
            continue
        pending.append(task)

    records = list(existing.values())
    successes_this_run = 0
    concurrency = args.initial_concurrency
    i = 0
    while i < len(pending):
        batch = pending[i : i + concurrency]
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = []
            for task in batch:
                attempt = attempts.get(task["instance_id"], 0) + 1
                attempts[task["instance_id"]] = attempt
                futures.append(pool.submit(prewarm_one, task, attempt, 1.0))
            for fut in concurrent.futures.as_completed(futures):
                rec = fut.result()
                append_status(rec)
                existing[rec["instance_id"]] = rec
                records = list(existing.values())
                print(json.dumps(rec, sort_keys=True), flush=True)
                if rec.get("ready_for_review"):
                    successes_this_run += 1
                else:
                    reason = str(rec.get("failure_reason", ""))
                    if any(s in reason for s in ("403", "timeout", "network", "proxy", "containerd", "no space")):
                        concurrency = max(1, args.initial_concurrency)
        i += len(batch)
        if successes_this_run >= 10:
            concurrency = min(args.max_concurrency, 4)

    disk_after = docker_system_df()
    (PREWARM_ROOT / "docker_system_df_after.txt").write_text(disk_after)
    write_summary(list(existing.values()), disk_before, disk_after, concurrency)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
