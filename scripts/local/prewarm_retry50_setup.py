#!/usr/bin/env python3
"""Setup-only validation for official100-new retry50 tasks."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RETRY_ROOT = PROJECT_ROOT / "outputs/official100-new/retry50"
PREWARM_ROOT = RETRY_ROOT / "prewarm"
STATUS_PATH = PREWARM_ROOT / "status.jsonl"
SUMMARY_PATH = RETRY_ROOT / "prewarm_summary.json"
REPORT_PATH = RETRY_ROOT / "prewarm_report.md"

COMPOSE_BASE = PROJECT_ROOT / "harbor/src/harbor/environments/docker/docker-compose-base.yaml"
COMPOSE_BUILD = PROJECT_ROOT / "harbor/src/harbor/environments/docker/docker-compose-build.yaml"
COMPOSE_UV_CACHE = PROJECT_ROOT / "harbor/src/harbor/environments/docker/docker-compose-uv-cache.yaml"
COMPOSE_UV_BIN = PROJECT_ROOT / "harbor/src/harbor/environments/docker/docker-compose-uv-bin.yaml"

UV_BIN = PROJECT_ROOT / "outputs/official100-new/runtime-cache/bin/uv"
SWE_BENCH_PLATFORM = "linux/amd64"

OPENHANDS_SETUP = (
    "set -euo pipefail; "
    "uv --version && "
    "mkdir -p /opt/openhands-sdk-venv && chown root:root /opt/openhands-sdk-venv && "
    "uv python install 3.12 && "
    "uv venv /opt/openhands-sdk-venv --python 3.12 && "
    "source /opt/openhands-sdk-venv/bin/activate && "
    "export PIP_DEFAULT_TIMEOUT=120 && "
    "uv pip install openhands-sdk==1.16.1 openhands-tools==1.16.1 'litellm<=1.82.2' && "
    'cd /tmp && /opt/openhands-sdk-venv/bin/python -c "import importlib.metadata; import openhands.sdk; import openhands.tools.file_editor; print(importlib.metadata.version(\\\"litellm\\\"))"'
)


def sanitize(name: str, *, image: bool = False) -> str:
    name = name.lower()
    if not re.match(r"^[a-z0-9]", name):
        name = "0" + name
    pattern = r"[^a-z0-9._-]" if image else r"[^a-z0-9_-]"
    return re.sub(pattern, "-", name)


def run_cmd(cmd: list[str], *, env: dict[str, str] | None = None, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )


def task_config(task_dir: Path) -> tuple[int, str, int]:
    data = tomllib.loads((task_dir / "task.toml").read_text())
    env = data.get("environment", {})
    cpus = int(env.get("cpus", 1))
    memory = str(env.get("memory", "4G")).upper()
    if memory.endswith("G"):
        memory_mb = int(float(memory[:-1]) * 1024)
    elif memory.endswith("M"):
        memory_mb = int(float(memory[:-1]))
    else:
        memory_mb = int(float(memory))
    build_timeout = int(float(env.get("build_timeout_sec", 1800.0)))
    return cpus, f"{memory_mb}M", build_timeout


def base_image(task_dir: Path) -> str:
    for line in (task_dir / "environment/Dockerfile").read_text().splitlines():
        line = line.strip()
        if line.startswith("FROM "):
            return line.split()[1]
    raise ValueError(f"No FROM line in {task_dir}")


def compose_env(task_dir: Path, trial_dir: Path, instance_id: str) -> dict[str, str]:
    cpus, memory, _ = task_config(task_dir)
    env = os.environ.copy()
    env.update(
        {
            "DOCKER_DEFAULT_PLATFORM": SWE_BENCH_PLATFORM,
            "MAIN_IMAGE_NAME": sanitize(f"hb__{instance_id}", image=True),
            "CONTEXT_DIR": str((task_dir / "environment").resolve()),
            "HOST_VERIFIER_LOGS_PATH": str((trial_dir / "verifier").resolve()),
            "HOST_AGENT_LOGS_PATH": str((trial_dir / "agent").resolve()),
            "HOST_ARTIFACTS_PATH": str((trial_dir / "artifacts").resolve()),
            "ENV_VERIFIER_LOGS_PATH": "/logs/verifier",
            "ENV_AGENT_LOGS_PATH": "/logs/agent",
            "ENV_ARTIFACTS_PATH": "/artifacts",
            "HOST_UV_BIN_PATH": str(UV_BIN.resolve()),
            "CPUS": str(cpus),
            "MEMORY": memory,
        }
    )
    uv_cache = Path.home() / ".cache/uv"
    if uv_cache.is_dir():
        env["HOST_UV_CACHE_PATH"] = str(uv_cache.resolve())
    return env


def compose_cmd(project_name: str, task_dir: Path) -> list[str]:
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
    uv_cache = Path.home() / ".cache/uv"
    if uv_cache.is_dir():
        cmd += ["-f", str(COMPOSE_UV_CACHE.resolve())]
    cmd += ["-f", str(COMPOSE_UV_BIN.resolve())]
    return cmd


def classify(text: str) -> str:
    low = text.lower()
    if "astral.sh/uv/install.sh" in low:
        return "unexpected astral installer path used"
    if "connection refused" in low or "unexpected eof" in low or "timeout" in low:
        return "network/setup failure"
    if "no space left" in low:
        return "docker no space left"
    return "unknown"


def append_status(record: dict) -> None:
    PREWARM_ROOT.mkdir(parents=True, exist_ok=True)
    with STATUS_PATH.open("a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def load_latest() -> dict[str, dict]:
    if not STATUS_PATH.exists():
        return {}
    latest = {}
    for line in STATUS_PATH.read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            latest[rec["instance_id"]] = rec
    return latest


def prewarm(item: dict) -> dict:
    instance_id = item["instance_id"]
    task_dir = (PROJECT_ROOT / item["source_task_path"]).resolve()
    trial_dir = PREWARM_ROOT / "trials" / instance_id
    if trial_dir.exists():
        shutil.rmtree(trial_dir)
    for sub in ["agent", "verifier", "artifacts"]:
        (trial_dir / sub).mkdir(parents=True, exist_ok=True)

    base = base_image(task_dir)
    project_name = sanitize(f"retry50_setup__{instance_id}")
    env = compose_env(task_dir, trial_dir, instance_id)
    _, _, build_timeout = task_config(task_dir)
    compose = compose_cmd(project_name, task_dir)
    started = time.time()
    rec = {
        "instance_id": instance_id,
        "docker_base_image": base,
        "pull_status": "pending",
        "build_status": "pending",
        "container_start_status": "pending",
        "uv_status": "pending",
        "openhands_setup_status": "pending",
        "duration_seconds": None,
        "failure_reason": None,
        "ready_for_review": False,
        "astral_installer_called": False,
    }
    try:
        pull = run_cmd(["docker", "pull", "--platform", SWE_BENCH_PLATFORM, base], timeout=build_timeout)
        (trial_dir / "docker_pull.log").write_text(pull.stdout or "")
        if pull.returncode != 0:
            rec.update({"pull_status": "failed", "build_status": "skipped", "container_start_status": "skipped", "uv_status": "skipped", "openhands_setup_status": "skipped", "failure_reason": classify(pull.stdout or "")})
            return rec
        rec["pull_status"] = "ready"

        build = run_cmd(compose + ["build"], env=env, timeout=build_timeout)
        (trial_dir / "docker_build.log").write_text(build.stdout or "")
        if build.returncode != 0:
            rec.update({"build_status": "failed", "container_start_status": "skipped", "uv_status": "skipped", "openhands_setup_status": "skipped", "failure_reason": classify(build.stdout or "")})
            return rec
        rec["build_status"] = "ready"

        run_cmd(compose + ["down", "--remove-orphans"], env=env, timeout=120)
        up = run_cmd(compose + ["up", "--detach", "--wait"], env=env, timeout=300)
        (trial_dir / "docker_up.log").write_text(up.stdout or "")
        if up.returncode != 0:
            rec.update({"container_start_status": "failed", "uv_status": "skipped", "openhands_setup_status": "skipped", "failure_reason": classify(up.stdout or "")})
            return rec
        rec["container_start_status"] = "ready"

        uv = run_cmd(compose + ["exec", "main", "bash", "-lc", "command -v uv && uv --version"], env=env, timeout=120)
        (trial_dir / "uv_version.log").write_text(uv.stdout or "")
        if uv.returncode != 0:
            rec.update({"uv_status": "failed", "openhands_setup_status": "skipped", "failure_reason": classify(uv.stdout or "")})
            return rec
        rec["uv_status"] = "ready"

        setup = run_cmd(compose + ["exec", "main", "bash", "-lc", OPENHANDS_SETUP], env=env, timeout=1080)
        (trial_dir / "openhands_setup.log").write_text(setup.stdout or "")
        rec["astral_installer_called"] = "astral.sh/uv/install.sh" in (setup.stdout or "")
        if setup.returncode != 0:
            rec.update({"openhands_setup_status": "failed", "failure_reason": classify(setup.stdout or "")})
            return rec
        rec["openhands_setup_status"] = "ready"
        rec["ready_for_review"] = True
        return rec
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        rec["failure_reason"] = f"timeout: {classify(output)}"
        if rec["pull_status"] == "pending":
            rec["pull_status"] = "failed"
        elif rec["build_status"] == "pending":
            rec["build_status"] = "failed"
        elif rec["container_start_status"] == "pending":
            rec["container_start_status"] = "failed"
        elif rec["uv_status"] == "pending":
            rec["uv_status"] = "failed"
        else:
            rec["openhands_setup_status"] = "failed"
        return rec
    finally:
        rec["duration_seconds"] = round(time.time() - started, 3)
        try:
            run_cmd(compose + ["down"], env=env, timeout=180)
        except Exception:
            pass


def write_outputs(records: list[dict], concurrency: int) -> None:
    latest = {r["instance_id"]: r for r in records}
    ready = [r for r in latest.values() if r.get("ready_for_review")]
    failed = [r for r in latest.values() if not r.get("ready_for_review")]
    summary = {
        "total": len(latest),
        "ready": len(ready),
        "failed": len(failed),
        "docker_pull_failures": sum(r.get("pull_status") == "failed" for r in failed),
        "docker_build_failures": sum(r.get("build_status") == "failed" for r in failed),
        "container_start_failures": sum(r.get("container_start_status") == "failed" for r in failed),
        "uv_failures": sum(r.get("uv_status") == "failed" for r in failed),
        "openhands_setup_failures": sum(r.get("openhands_setup_status") == "failed" for r in failed),
        "astral_installer_calls": sum(bool(r.get("astral_installer_called")) for r in latest.values()),
        "prewarm_concurrency": concurrency,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2) + "\n")
    lines = [
        "# Retry50 Setup-Only Prewarm Report",
        "",
        f"- READY: `{summary['ready']}`",
        f"- FAILED: `{summary['failed']}`",
        f"- Docker pull failures: `{summary['docker_pull_failures']}`",
        f"- Docker build failures: `{summary['docker_build_failures']}`",
        f"- Container start failures: `{summary['container_start_failures']}`",
        f"- uv failures: `{summary['uv_failures']}`",
        f"- OpenHands setup failures: `{summary['openhands_setup_failures']}`",
        f"- astral.sh installer calls: `{summary['astral_installer_calls']}`",
        f"- Prewarm concurrency: `{concurrency}`",
        "",
        "## Failed Tasks",
    ]
    for rec in sorted(failed, key=lambda r: r["instance_id"]):
        lines.append(f"- `{rec['instance_id']}`: {rec.get('failure_reason')}")
    REPORT_PATH.write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    if args.concurrency < 1 or args.concurrency > 4:
        raise SystemExit("--concurrency must be between 1 and 4")
    if not UV_BIN.is_file():
        raise SystemExit(f"Missing uv binary: {UV_BIN}")
    manifest = json.loads((RETRY_ROOT / "manifest.json").read_text())
    tasks = manifest["tasks"][: args.limit] if args.limit else manifest["tasks"]
    latest = load_latest()
    pending = [item for item in tasks if not latest.get(item["instance_id"], {}).get("ready_for_review")]
    records = list(latest.values())
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(prewarm, item) for item in pending]
        for fut in concurrent.futures.as_completed(futures):
            rec = fut.result()
            append_status(rec)
            latest[rec["instance_id"]] = rec
            records = list(latest.values())
            print(json.dumps(rec, sort_keys=True), flush=True)
    status_records = [latest[k] for k in sorted(latest)]
    STATUS_PATH.write_text("\n".join(json.dumps(r, sort_keys=True) for r in status_records) + ("\n" if status_records else ""))
    write_outputs(status_records, args.concurrency)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
