"""
Shared utility functions for the data pipeline.

Contains functions for:
- Extracting patches from Harbor trial directories
- Extracting review reports from Harbor trial directories
- Collecting trial directories from Harbor job outputs
"""

import json
import re
from pathlib import Path


# ============================================================
# Patch Extraction (from Harbor patchgen jobs)
# ============================================================


def strip_symlink_diff(patch: str) -> str:
    """Remove symlink-related diff hunks that aren't real code changes."""
    lines = patch.split("\n")
    result = []
    skip = False
    for line in lines:
        if line.startswith("diff --git") and "120000" in line:
            skip = True
        elif line.startswith("diff --git"):
            skip = False
        if not skip:
            result.append(line)
    return "\n".join(result)


def extract_trajectory_summary(traj_path: str) -> str:
    """Extract a brief summary from an OpenHands trajectory file."""
    path = Path(traj_path)
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text())
        steps = data.get("steps", [])
        return f"{len(steps)} steps"
    except (json.JSONDecodeError, IOError):
        return ""


def process_trial_patchgen(trial_dir: str) -> dict:
    """Process a single patchgen trial directory, extracting patch and metadata.

    Patch source: agent/solution.patch (git diff from Docker container).
    """
    trial_path = Path(trial_dir)
    result = {
        "instance_id": None,
        "model_patch": "",
        "resolved": None,
        "reward": None,
        "test_report": {},
        "tokens": {},
        "trajectory_summary": "",
        "error": None,
    }

    # Read result.json
    result_path = trial_path / "result.json"
    if not result_path.exists():
        result["error"] = "no result.json"
        return result

    try:
        with open(result_path) as f:
            result_data = json.load(f)
    except json.JSONDecodeError:
        result["error"] = "invalid result.json"
        return result

    result["instance_id"] = result_data.get("task_name", "")

    # Token statistics
    agent_result = result_data.get("agent_result", {}) or {}
    result["tokens"] = {
        "n_input_tokens": agent_result.get("n_input_tokens", 0),
        "n_cache_tokens": agent_result.get("n_cache_tokens", 0),
        "n_output_tokens": agent_result.get("n_output_tokens", 0),
        "cost_usd": agent_result.get("cost_usd"),
    }

    # Reward
    verifier_result = result_data.get("verifier_result", {}) or {}
    rewards = verifier_result.get("rewards", {}) or {}
    result["reward"] = rewards.get("reward")

    # Timing
    result["started_at"] = result_data.get("started_at", "")
    result["finished_at"] = result_data.get("finished_at", "")

    # Exception info
    if result_data.get("exception_info"):
        result["error"] = str(result_data["exception_info"])

    # Read verifier report.json
    report_path = trial_path / "verifier" / "report.json"
    if report_path.exists():
        try:
            with open(report_path) as f:
                report_data = json.load(f)
            instance_id = result["instance_id"]
            instance_report = None
            if instance_id and instance_id in report_data:
                instance_report = report_data[instance_id]
            elif instance_id:
                id_lower = instance_id.lower()
                for k, v in report_data.items():
                    if k.lower() == id_lower:
                        instance_report = v
                        break
            if instance_report is not None:
                result["resolved"] = instance_report.get("resolved", False)
                result["test_report"] = {
                    "resolved": instance_report.get("resolved", False),
                    "patch_is_None": instance_report.get("patch_is_None", False),
                    "patch_exists": instance_report.get("patch_exists", True),
                    "patch_successfully_applied": instance_report.get(
                        "patch_successfully_applied", True
                    ),
                    "tests_status": instance_report.get("tests_status", {}),
                }
        except json.JSONDecodeError:
            pass

    # Fallback: test-stdout.txt
    if result["resolved"] is None:
        stdout_path = trial_path / "verifier" / "test-stdout.txt"
        if stdout_path.exists():
            try:
                stdout_text = stdout_path.read_text(errors="replace")
                if "SWE-rebench results starts here\nPASSED" in stdout_text:
                    result["resolved"] = True
                    result["test_report"] = {"resolved": True, "source": "test-stdout.txt"}
                elif "SWE-rebench results starts here\nFAILED" in stdout_text:
                    result["resolved"] = False
                    result["test_report"] = {"resolved": False, "source": "test-stdout.txt"}
            except OSError:
                pass

    # Extract patch
    solution_patch_path = trial_path / "agent" / "solution.patch"
    fallback_patch_path = trial_path / "verifier" / "solution.patch"
    for patch_path in (solution_patch_path, fallback_patch_path):
        if patch_path.exists():
            raw_patch = patch_path.read_text()
            if raw_patch.strip():
                result["model_patch"] = strip_symlink_diff(raw_patch)
                break

    # Trajectory summary
    traj_path = trial_path / "agent" / "openhands.trajectory.json"
    result["trajectory_summary"] = extract_trajectory_summary(str(traj_path))

    return result


# ============================================================
# Review Report Extraction (from Harbor review jobs)
# ============================================================


def extract_json_from_text(text: str) -> dict | None:
    """Try to extract a JSON object from text (handles markdown code blocks)."""
    # Try direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    if not text:
        return None
    # Try extracting from markdown code block
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # Try finding first { ... } block
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    return None


def is_valid_review_report(report: dict) -> bool:
    """Check if a parsed report has the expected review structure."""
    if not isinstance(report, dict):
        return False
    if "recommendation" in report:
        return report["recommendation"] in ("approve", "request_changes")
    decision = report.get("decision", {})
    if isinstance(decision, dict) and "recommendation" in decision:
        return decision["recommendation"] in ("approve", "request_changes")
    if "bug_fixed" in report and isinstance(report["bug_fixed"], bool):
        return True
    return False


def process_trial_review(trial_dir: Path) -> dict:
    """Process a single review trial directory, extracting the review report.

    Extraction strategy (4-layer fallback):
    1. cat review_report command output in trajectory
    2. edit/create action writing review_report.json
    3. finish message JSON
    4. Content field scan for JSON with recommendation
    """
    result = {
        "instance_id": None,
        "review_report": None,
        "source": None,
        "error": None,
    }

    result_path = trial_dir / "result.json"
    if not result_path.exists():
        result["error"] = "no result.json"
        return result

    try:
        with open(result_path) as f:
            result_data = json.load(f)
    except json.JSONDecodeError:
        result["error"] = "invalid result.json"
        return result

    result["instance_id"] = result_data.get("task_name", "")

    # Try to extract review report from trajectory
    traj_path = trial_dir / "agent" / "openhands.trajectory.json"
    if not traj_path.exists():
        traj_path = trial_dir / "agent" / "trajectory.json"
    if not traj_path.exists():
        result["error"] = "no trajectory file"
        return result

    try:
        traj_data = json.loads(traj_path.read_text())
    except (json.JSONDecodeError, IOError) as e:
        result["error"] = f"trajectory parse error: {e}"
        return result

    steps = traj_data.get("steps", [])
    if not steps and isinstance(traj_data, list):
        steps = traj_data

    report = _extract_report_from_steps(steps)
    if report:
        result["review_report"] = report[0]
        result["source"] = report[1]
    else:
        result["error"] = "could not extract review report"

    return result


def _extract_report_from_steps(steps: list) -> tuple[dict, str] | None:
    """Extract review report from trajectory steps using multi-layer fallback."""
    # Strategy 1: Look for cat review_report.json output
    for step in steps:
        obs = step.get("observation", step.get("content", ""))
        if isinstance(obs, dict):
            obs = obs.get("content", "")
        if not isinstance(obs, str):
            continue
        if "review_report" in obs and "{" in obs:
            parsed = extract_json_from_text(obs)
            if parsed and is_valid_review_report(parsed):
                return (parsed, "cat_output")

    # Strategy 2: Look for write/create action with review_report content
    for step in steps:
        action = step.get("action", step.get("content", ""))
        if isinstance(action, dict):
            content = action.get("content", "")
            path = action.get("path", "")
        elif isinstance(action, str):
            content = action
            path = ""
        else:
            continue
        if "review_report" in str(path) and content:
            parsed = extract_json_from_text(content)
            if parsed and is_valid_review_report(parsed):
                return (parsed, "write_action")

    # Strategy 3: Look for finish message with JSON
    for step in reversed(steps):
        action = step.get("action", {})
        if isinstance(action, dict) and action.get("action") == "finish":
            msg = action.get("message", action.get("content", ""))
            if msg:
                parsed = extract_json_from_text(msg)
                if parsed and is_valid_review_report(parsed):
                    return (parsed, "finish_message")

    # Strategy 4: Scan all assistant content for valid report JSON
    for step in reversed(steps):
        content = ""
        if isinstance(step, dict):
            action = step.get("action", {})
            if isinstance(action, dict):
                content = action.get("content", "")
            elif isinstance(action, str):
                content = action
        if not content or not isinstance(content, str):
            continue
        if "recommendation" in content or "bug_fixed" in content:
            parsed = extract_json_from_text(content)
            if parsed and is_valid_review_report(parsed):
                return (parsed, "content_scan")

    return None


# ============================================================
# Trial Directory Collection
# ============================================================


def _is_trial_dir(path: Path) -> bool:
    """Check if a directory is a valid trial directory."""
    return path.is_dir() and (path / "result.json").exists()


def collect_all_trials(job_dir: Path) -> list[Path]:
    """Find all trial directories under a Harbor job directory.

    Supports both flat structure (trials directly in job_dir) and
    timestamp-based structure (trials under timestamp subdirectories).
    """
    job_dir = Path(job_dir)
    trials = []

    # Check if direct children are trial dirs
    direct_trials = [d for d in sorted(job_dir.iterdir()) if _is_trial_dir(d)]
    if direct_trials:
        return direct_trials

    # Otherwise look for timestamp subdirectories
    for subdir in sorted(job_dir.iterdir()):
        if not subdir.is_dir():
            continue
        if subdir.name.startswith(".") or subdir.name.startswith("_"):
            continue
        sub_trials = [d for d in sorted(subdir.iterdir()) if _is_trial_dir(d)]
        trials.extend(sub_trials)

    return trials
