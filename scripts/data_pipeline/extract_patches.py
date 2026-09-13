#!/usr/bin/env python3
"""
Extract patches and PR content from a Harbor Patch Generation job.

Extraction strategy (single source, no trajectory fallback):
- Patch: <trial>/agent/solution.patch (exported via git diff HEAD inside Docker container)
- PR content: <trial>/verifier/pr_content.json (persisted by test.sh from inside the container)

Trajectory-based extraction has been deprecated -- reconstructing diffs or parsing JSON
from OpenHands trajectories is highly unreliable.

Outputs two files (compatible with existing pipeline format):
- patches JSON (compatible with predicted_patches_qwen.json)
- PR content JSON (compatible with pr_contents_qwen.json)

Usage:
    python3 scripts/data_pipeline/extract_patches.py \\
        --job-dir jobs/swebench_patch_gen/<timestamp> \\
        --patches-output data/swebench/patch_gen_patches.json \\
        --pr-output data/swebench/patch_gen_pr_contents.json
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from _utils import process_trial_patchgen as process_trial

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "swebench"


def extract_pr_content_from_verifier(trial_dir: Path) -> dict | None:
    """Extract PR content from verifier/pr_content.json (single source of truth).

    Handles cases where the model inserts literal newlines into JSON strings
    (should be \\n but written as actual newline characters).
    """
    pr_path = trial_dir / "verifier" / "pr_content.json"
    if not pr_path.exists():
        return None
    try:
        with open(pr_path) as f:
            data = json.load(f)
        if data.get("pr_title") and data.get("pr_body"):
            return data
    except (json.JSONDecodeError, KeyError):
        pass
    # Fallback 1: use strict=False to allow literal control characters
    try:
        raw = pr_path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw, strict=False)
        if data.get("pr_title") and data.get("pr_body"):
            return data
    except (json.JSONDecodeError, KeyError, ValueError):
        pass
    # Fallback 2: regex extraction (handles unescaped double quotes inside pr_body)
    try:
        import re
        raw = pr_path.read_text(encoding="utf-8", errors="replace")
        # pr_title is near the start of JSON; take first match to avoid body interference
        title_m = re.search(r'"pr_title"\s*:\s*"([^"]{1,300})"', raw)
        body_start = raw.find('"pr_body"')
        if title_m and body_start >= 0 and title_m.start() < body_start:
            title = title_m.group(1).strip()
            body_raw = raw[body_start + len('"pr_body"'):].strip().lstrip(":").strip()
            if body_raw.startswith('"'):
                body_raw = body_raw[1:]
            # Find end of JSON object: last '"' followed by optional whitespace and '}'
            end_m = re.search(r'"\s*\}\s*$', body_raw)
            if end_m:
                body = body_raw[:end_m.start() + 1]  # keep the trailing '"'
            else:
                body = body_raw
            body = body.replace("\n", " ").replace("\r", " ").strip().rstrip('"')
            if title and body:
                return {"pr_title": title, "pr_body": body}
    except Exception:
        pass
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Extract patches and PR content from a Harbor Patch Generation job"
    )
    parser.add_argument(
        "--job-dir",
        type=str,
        required=True,
        help="Harbor job directory",
    )
    parser.add_argument(
        "--patches-output",
        type=str,
        default=str(DATA_DIR / "patch_gen_patches.json"),
        help="Output patches JSON file",
    )
    parser.add_argument(
        "--pr-output",
        type=str,
        default=str(DATA_DIR / "patch_gen_pr_contents.json"),
        help="Output PR content JSON file",
    )
    parser.add_argument(
        "--model-name",
        type=str,
        default="Qwen/Qwen3-Coder-30B-A3B-Instruct",
        help="Model name for metadata",
    )
    args = parser.parse_args()

    job_dir = Path(args.job_dir)
    if not job_dir.exists():
        print(f"Error: Job directory not found: {job_dir}")
        sys.exit(1)

    trial_dirs = sorted([
        d for d in job_dir.iterdir()
        if d.is_dir() and (d / "result.json").exists()
    ])
    print(f"Found {len(trial_dirs)} trial directories in {job_dir}")

    patch_results = {}
    pr_results = {}
    stats = {
        "total": 0,
        "resolved": 0,
        "unresolved": 0,
        "error": 0,
        "has_patch": 0,
        "no_patch": 0,
        "has_pr": 0,
        "no_pr": 0,
    }

    for trial_dir in trial_dirs:
        trial_data = process_trial(str(trial_dir))
        instance_id = trial_data.get("instance_id")
        if not instance_id:
            print(f"  Skipping {trial_dir.name}: no instance_id")
            continue

        stats["total"] += 1

        if trial_data["resolved"]:
            stats["resolved"] += 1
        elif trial_data["resolved"] is False:
            stats["unresolved"] += 1
        else:
            stats["error"] += 1

        if trial_data["model_patch"]:
            stats["has_patch"] += 1
        else:
            stats["no_patch"] += 1

        patch_results[instance_id] = {
            "model_patch": trial_data["model_patch"],
            "resolved": trial_data["resolved"],
            "reward": trial_data["reward"],
            "test_report": trial_data["test_report"],
            "tokens": trial_data["tokens"],
            "trajectory_summary": trial_data["trajectory_summary"],
            "started_at": trial_data.get("started_at", ""),
            "finished_at": trial_data.get("finished_at", ""),
        }

        # Extract PR content -- single source: verifier/pr_content.json
        pr_content = extract_pr_content_from_verifier(trial_dir)

        if pr_content:
            stats["has_pr"] += 1
            pr_results[instance_id] = {
                "pr_title": pr_content["pr_title"],
                "pr_body": pr_content["pr_body"],
                "source": "verifier",
            }
        else:
            stats["no_pr"] += 1
            pr_results[instance_id] = {
                "pr_title": "N/A",
                "pr_body": "N/A",
                "source": "missing",
            }

        resolved_str = (
            "resolved" if trial_data["resolved"]
            else "unresolved" if trial_data["resolved"] is False
            else "error"
        )
        pr_str = "has_pr" if pr_content else "no_pr"
        print(
            f"  [{stats['total']:3d}] {instance_id:<55} "
            f"{resolved_str:<12} {pr_str}"
        )

    # Save patches
    patches_output = Path(args.patches_output)
    patches_output.parent.mkdir(parents=True, exist_ok=True)
    with open(patches_output, "w", encoding="utf-8") as f:
        json.dump(
            {
                "metadata": {
                    "model_name": args.model_name,
                    "job_dir": str(job_dir),
                    "extracted_at": datetime.now().isoformat(),
                    "stats": {
                        k: v for k, v in stats.items()
                        if k not in ("has_pr", "no_pr")
                    },
                },
                "instances": patch_results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    # Save PR contents
    pr_output = Path(args.pr_output)
    pr_output.parent.mkdir(parents=True, exist_ok=True)
    with open(pr_output, "w", encoding="utf-8") as f:
        json.dump(
            {
                "metadata": {
                    "model_name": args.model_name,
                    "job_dir": str(job_dir),
                    "extracted_at": datetime.now().isoformat(),
                    "pr_stats": {
                        "total": stats["total"],
                        "has_pr": stats["has_pr"],
                        "no_pr": stats["no_pr"],
                    },
                },
                "instances": pr_results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"\n{'=' * 60}")
    print("Extraction complete!")
    print(f"{'=' * 60}")
    print(f"  Total:      {stats['total']}")
    print(f"  Resolved:   {stats['resolved']}")
    print(f"  Unresolved: {stats['unresolved']}")
    print(f"  Error:      {stats['error']}")
    print(f"  Has patch:  {stats['has_patch']}")
    print(f"  No patch:   {stats['no_patch']}")
    print(f"  Has PR:     {stats['has_pr']}")
    print(f"  No PR:      {stats['no_pr']}")
    print(f"\n  Patches:    {patches_output}")
    print(f"  PR content: {pr_output}")


if __name__ == "__main__":
    main()
