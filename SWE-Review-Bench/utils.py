"""Shared utilities for SWE-Review evaluation."""

import json
import os
from pathlib import Path
from typing import Optional


def _is_trial_dir(d: Path) -> bool:
    """Check if a directory is a trial dir (has result.json with task_name)."""
    rj = d / "result.json"
    if not rj.exists():
        return False
    try:
        with open(rj) as f:
            data = json.load(f)
        return bool(data.get("task_name"))
    except (json.JSONDecodeError, IOError):
        return False


def _find_trial_dirs(root: Path) -> list[Path]:
    """Recursively find trial directories up to 3 levels deep.

    Harbor output structure: results/<timestamp>/<trial>/result.json
    The timestamp dir also has a result.json (job-level, no task_name).
    """
    trial_dirs = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if _is_trial_dir(entry):
            trial_dirs.append(entry)
        else:
            # Go one level deeper (timestamp or 'results' subdirs)
            for sub in sorted(entry.iterdir()):
                if not sub.is_dir():
                    continue
                if _is_trial_dir(sub):
                    trial_dirs.append(sub)
                else:
                    # Go one more level (results/<timestamp>/<trial>)
                    for subsub in sorted(sub.iterdir()):
                        if subsub.is_dir() and _is_trial_dir(subsub):
                            trial_dirs.append(subsub)
    return trial_dirs


def load_review_results(results_dir: str) -> list[dict]:
    """Load review results from a Harbor job output directory.

    Supports nested directory structures up to 3 levels deep:
    - results_dir/<trial>/result.json (flat)
    - results_dir/<timestamp>/<trial>/result.json (timestamp-based)
    - results_dir/results/<timestamp>/<trial>/result.json (full split dir)

    Each trial directory contains:
    - result.json with task_name (instance_id)
    - verifier/review_report.json with the review output
    """
    results = []
    results_path = Path(results_dir)

    trial_dirs = _find_trial_dirs(results_path)

    for trial_dir in trial_dirs:
        # Read result.json for instance_id
        result_path = trial_dir / "result.json"
        try:
            with open(result_path) as f:
                result_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

        instance_id = result_data.get("task_name", "")
        if not instance_id:
            continue

        # Read review report (check multiple locations)
        report = None
        report_paths = [
            trial_dir / "verifier" / "review_report.json",
            trial_dir / "review_report.json",
            trial_dir / "report.json",
        ]
        for rp in report_paths:
            if rp.exists():
                try:
                    with open(rp) as f:
                        report = json.load(f)
                    break
                except (json.JSONDecodeError, IOError):
                    continue

        results.append({
            "instance_id": instance_id,
            "trial_dir": str(trial_dir),
            "report": report,
        })

    # Deduplicate: for each instance_id, prefer a trial with a report
    # (successful) over one without (error/model_fail). Among same-status
    # trials, prefer the latest (last in list, since _find_trial_dirs walks
    # timestamp dirs in sorted order).
    seen: dict[str, int] = {}
    for i, r in enumerate(results):
        iid = r["instance_id"]
        if iid not in seen:
            seen[iid] = i
        else:
            prev = results[seen[iid]]
            # Prefer trial with report over trial without
            if prev["report"] is None and r["report"] is not None:
                seen[iid] = i
            elif prev["report"] is not None and r["report"] is None:
                pass  # keep previous (has report)
            else:
                # Same status: take the later one (higher index = newer timestamp)
                seen[iid] = i

    return [results[i] for i in sorted(seen.values())]


def extract_decision(report: Optional[dict]) -> Optional[str]:
    """Extract approve/request_changes decision from a review report."""
    if report is None:
        return None

    # Handle nested report structures
    if "decision" in report:
        decision = report["decision"]
        if isinstance(decision, dict):
            return decision.get("recommendation")
        return decision

    return None


def load_benchmark_split(split_name: str, data_dir: str = "data") -> list[dict]:
    """Load a SWE-Review-Bench split from local data or HuggingFace.

    Returns a list of dicts with at least: instance_id, candidate_resolved.
    """
    # Primary location (download_data.py output)
    local_path = Path(data_dir) / "swebench" / "benchmark" / "splits" / split_name / "patches_filtered.json"

    if local_path.exists():
        with open(local_path) as f:
            raw = json.load(f)
        # Handle both formats: {"instances": {id: {...}}} and flat list
        if isinstance(raw, dict) and "instances" in raw:
            result = []
            for iid, patch_data in raw["instances"].items():
                result.append({
                    "instance_id": iid,
                    "candidate_resolved": patch_data.get("resolved", False),
                    **patch_data,
                })
            return result
        elif isinstance(raw, list):
            return raw
        return []

    # Try HuggingFace directly
    try:
        from datasets import load_dataset
        ds = load_dataset("Lego-X/SWE-Review-Bench", split=split_name)
        return ds.to_list()
    except Exception as e:
        raise FileNotFoundError(
            f"Cannot find benchmark split '{split_name}'. "
            f"Download with: python scripts/data_pipeline/download_data.py --benchmark"
        ) from e
