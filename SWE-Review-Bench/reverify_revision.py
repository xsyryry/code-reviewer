#!/usr/bin/env python3
"""Re-verify revision patches using SWE-bench evaluation harness.

Re-runs SWE-bench evaluation on revision patches to ensure resolve status
is computed from the actual test suite results.

This script:
1. Finds all revision trial directories with solution.patch files
2. Groups trials into batches (one patch per instance_id per batch)
3. Runs SWE-bench evaluation harness on each batch
4. Updates each trial's result.json with the correct verifier_result

When multiple trials exist for the same instance_id (with different patches),
the script runs multiple evaluation batches to evaluate each patch separately.

Usage:
    python SWE-Review-Bench/reverify_revision.py \
        --results-dir outputs/benchmark/claude_opus_47/glm5_500 \
        --benchmark-split glm5_500

    # Specific instances only
    python SWE-Review-Bench/reverify_revision.py \
        --results-dir outputs/benchmark/claude_opus_47/glm5_500 \
        --benchmark-split glm5_500 \
        --instances django__django-10554 django__django-11087

    # Dry run (show what would be evaluated, don't run)
    python SWE-Review-Bench/reverify_revision.py \
        --results-dir outputs/benchmark/claude_opus_47/glm5_500 \
        --benchmark-split glm5_500 \
        --dry-run

    # All 3 splits at once
    for s in glm5_500 qwen3_coder_30b_500 qwen3_30b_instruct2507_500; do
        python SWE-Review-Bench/reverify_revision.py \
            --results-dir outputs/benchmark/claude_opus_47/$s \
            --benchmark-split $s \
            --max-workers 8
    done
"""

import argparse
import json
import os
import platform
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Add SWE-bench to path
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SWEBENCH_DIR = REPO_ROOT / "SWE-bench"
sys.path.insert(0, str(SWEBENCH_DIR))
sys.path.insert(0, str(SCRIPT_DIR))

from utils import _find_trial_dirs


def find_revision_trials(results_dir: Path) -> list[dict]:
    """Find all revision trial directories with solution.patch files.

    Returns list of dicts with:
        - trial_dir: Path to trial directory
        - trial_name: Name of the trial directory
        - instance_id: SWE-bench instance ID
        - patch_path: Path to solution.patch
        - patch_content: Content of solution.patch
    """
    revision_dir = results_dir / "revision"
    if not revision_dir.exists():
        print(f"Error: revision directory not found: {revision_dir}")
        return []

    trial_dirs = _find_trial_dirs(revision_dir)
    trials = []

    for trial_dir in trial_dirs:
        result_path = trial_dir / "result.json"
        if not result_path.exists():
            continue

        try:
            with open(result_path) as f:
                result_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

        instance_id = result_data.get("task_name", "")
        if not instance_id:
            continue

        patch_path = trial_dir / "agent" / "solution.patch"
        if not patch_path.exists():
            continue

        patch_content = patch_path.read_text().strip()

        trials.append({
            "trial_dir": trial_dir,
            "trial_name": trial_dir.name,
            "instance_id": instance_id,
            "patch_path": patch_path,
            "patch_content": patch_content,
        })

    return trials


def group_into_batches(trials: list[dict]) -> list[list[dict]]:
    """Group trials into batches where each batch has at most one trial per instance_id.

    SWE-bench evaluation keys by instance_id, so we cannot evaluate two different
    patches for the same instance_id in one run. This function partitions trials
    into the minimum number of batches needed.
    """
    # Group by instance_id
    by_id = defaultdict(list)
    for trial in trials:
        by_id[trial["instance_id"]].append(trial)

    # Find max number of trials per instance (= number of batches needed)
    max_trials = max(len(v) for v in by_id.values()) if by_id else 0

    batches = [[] for _ in range(max_trials)]
    for instance_id, instance_trials in by_id.items():
        for i, trial in enumerate(instance_trials):
            batches[i].append(trial)

    return [b for b in batches if b]


def load_swebench_dataset_instances(instance_ids: set[str]) -> list[dict]:
    """Load SWE-bench Verified instances for the given IDs."""
    from swebench.harness.utils import load_swebench_dataset

    dataset = load_swebench_dataset(
        "princeton-nlp/SWE-bench_Verified",
        "test",
        list(instance_ids),
    )
    return dataset


def run_swebench_evaluation_batch(
    trials: list[dict],
    dataset: list[dict],
    run_id: str,
    max_workers: int = 4,
    timeout: int = 1800,
    cache_level: str = "instance",
) -> dict:
    """Run SWE-bench evaluation for a single batch of trials.

    Each trial in the batch must have a unique instance_id.

    Returns: {trial_name: {"resolved": bool, "completed": bool}}
    """
    from swebench.harness.run_evaluation import run_instances
    from swebench.harness.constants import RUN_EVALUATION_LOG_DIR, LOG_REPORT

    dataset_map = {item["instance_id"]: item for item in dataset}

    # Build predictions (one per trial, keyed by instance_id)
    predictions_dict = {}
    trial_map = {}  # instance_id -> trial for this batch
    for trial in trials:
        iid = trial["instance_id"]
        if iid not in dataset_map:
            continue
        predictions_dict[iid] = {
            "instance_id": iid,
            "model_patch": trial["patch_content"],
            "model_name_or_path": "revision_reverify",
        }
        trial_map[iid] = trial

    if not predictions_dict:
        return {}

    # Filter dataset to only instances with predictions
    eval_dataset = [
        item for item in dataset if item["instance_id"] in predictions_dict
    ]

    # Run evaluation
    run_instances(
        predictions=predictions_dict,
        instances=eval_dataset,
        cache_level=cache_level,
        clean=False,
        force_rebuild=False,
        max_workers=max_workers,
        run_id=run_id,
        timeout=timeout,
        namespace="swebench",
    )

    # Collect results from log directory
    results = {}
    log_base = RUN_EVALUATION_LOG_DIR / run_id / "revision_reverify"
    for iid, trial in trial_map.items():
        report_path = log_base / iid / LOG_REPORT
        if report_path.exists():
            try:
                with open(report_path) as f:
                    report = json.load(f)
                resolved = report.get(iid, {}).get("resolved", False)
                results[trial["trial_name"]] = {
                    "instance_id": iid,
                    "resolved": resolved,
                    "completed": True,
                }
            except (json.JSONDecodeError, IOError):
                results[trial["trial_name"]] = {
                    "instance_id": iid,
                    "resolved": False,
                    "completed": False,
                }
        else:
            results[trial["trial_name"]] = {
                "instance_id": iid,
                "resolved": False,
                "completed": False,
            }

    return results


def update_trial_results(
    trials: list[dict],
    eval_results: dict,
    dry_run: bool = False,
) -> dict:
    """Update trial result.json files with correct verifier results.

    Args:
        trials: List of trial dicts
        eval_results: {trial_name: {"instance_id": str, "resolved": bool, "completed": bool}}

    Returns summary statistics.
    """
    stats = {
        "total": len(trials),
        "resolved": 0,
        "unresolved": 0,
        "eval_failed": 0,
        "empty_patch": 0,
        "updated": 0,
    }

    for trial in trials:
        trial_dir = trial["trial_dir"]
        trial_name = trial["trial_name"]

        if not trial["patch_content"]:
            stats["empty_patch"] += 1
            continue

        result = eval_results.get(trial_name)
        if result is None or not result["completed"]:
            stats["eval_failed"] += 1
            continue

        if result["resolved"]:
            stats["resolved"] += 1
            reward = 1.0
        else:
            stats["unresolved"] += 1
            reward = 0.0

        # Update result.json
        result_path = trial_dir / "result.json"
        if not dry_run:
            try:
                with open(result_path) as f:
                    result_data = json.load(f)

                # Update verifier_result
                result_data["verifier_result"] = {
                    "rewards": {"reward": reward},
                    "reverified": True,
                    "reverified_at": datetime.now().isoformat(),
                }

                with open(result_path, "w") as f:
                    json.dump(result_data, f, indent=4)

                stats["updated"] += 1
            except (json.JSONDecodeError, IOError) as e:
                print(f"  Error updating {result_path}: {e}")
        else:
            stats["updated"] += 1

        # Also update verifier/reward.txt
        reward_path = trial_dir / "verifier" / "reward.txt"
        if not dry_run and reward_path.parent.exists():
            try:
                reward_path.write_text(str(int(reward)))
            except IOError:
                pass

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Re-verify revision patches using SWE-bench evaluation harness"
    )
    parser.add_argument(
        "--results-dir",
        required=True,
        help="Path to split results dir (e.g., outputs/benchmark/claude_opus_47/glm5_500)",
    )
    parser.add_argument(
        "--benchmark-split",
        required=True,
        help="Benchmark split name (glm5_500, qwen3_coder_30b_500, qwen3_30b_instruct2507_500)",
    )
    parser.add_argument(
        "--instances",
        nargs="+",
        default=None,
        help="Specific instance IDs to re-verify (default: all)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="Max parallel workers for SWE-bench evaluation (default: 4)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Timeout per instance in seconds (default: 1800)",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Run ID for SWE-bench logs (default: auto-generated)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be evaluated without running",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path to save reverification results JSON",
    )
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    if not results_dir.exists():
        print(f"Error: results directory not found: {results_dir}")
        sys.exit(1)

    # Generate run ID
    run_id = args.run_id or (
        f"reverify_{args.benchmark_split}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )

    print("=" * 60)
    print("Re-verify Revision Patches")
    print("=" * 60)
    print(f"  Results dir: {results_dir}")
    print(f"  Split:       {args.benchmark_split}")
    print(f"  Run ID:      {run_id}")
    print(f"  Workers:     {args.max_workers}")
    print(f"  Timeout:     {args.timeout}s")
    if args.dry_run:
        print("  Mode:        DRY RUN")
    print()

    # Find revision trials
    print("Finding revision trials...")
    trials = find_revision_trials(results_dir)
    print(f"  Found {len(trials)} revision trials with patches")

    if not trials:
        print("No revision trials found. Nothing to do.")
        sys.exit(0)

    # Filter to specific instances if requested
    if args.instances:
        target_ids = set(args.instances)
        trials = [t for t in trials if t["instance_id"] in target_ids]
        print(f"  Filtered to {len(trials)} trials matching --instances")

    # Count patches
    non_empty = [t for t in trials if t["patch_content"]]
    empty = [t for t in trials if not t["patch_content"]]
    print(f"  Non-empty patches: {len(non_empty)}")
    print(f"  Empty patches:     {len(empty)}")

    # Show instance IDs and batching info
    instance_ids = sorted(set(t["instance_id"] for t in trials))
    print(f"\n  Unique instance IDs: {len(instance_ids)}")

    batches = group_into_batches(non_empty)
    if len(batches) > 1:
        print(f"  Evaluation batches: {len(batches)} (multiple trials per instance)")
        for i, batch in enumerate(batches):
            print(f"    Batch {i + 1}: {len(batch)} trials")
    elif batches:
        print(f"  Evaluation batches: 1 ({len(batches[0])} trials)")

    if len(instance_ids) <= 10:
        for iid in instance_ids:
            print(f"    - {iid}")
    else:
        for iid in instance_ids[:5]:
            print(f"    - {iid}")
        print(f"    ... and {len(instance_ids) - 5} more")

    if args.dry_run:
        print("\n[DRY RUN] Would evaluate the above patches. Exiting.")
        sys.exit(0)

    # Set open file limit on Linux
    if platform.system() == "Linux":
        import resource
        resource.setrlimit(resource.RLIMIT_NOFILE, (4096, 4096))

    # Load dataset once for all batches
    all_instance_ids = set(t["instance_id"] for t in non_empty)
    print("\nLoading SWE-bench Verified dataset...")
    dataset = load_swebench_dataset_instances(all_instance_ids)
    dataset_map = {item["instance_id"]: item for item in dataset}

    missing = all_instance_ids - set(dataset_map.keys())
    if missing:
        print(f"  Warning: {len(missing)} instance IDs not found in dataset")
    print(f"  Loaded {len(dataset)} instances")

    # Run evaluation for each batch
    all_eval_results = {}
    for batch_idx, batch in enumerate(batches):
        batch_run_id = run_id if len(batches) == 1 else f"{run_id}_b{batch_idx + 1}"
        batch_ids = {t["instance_id"] for t in batch}

        print(f"\n{'='*40}")
        if len(batches) > 1:
            print(f"Batch {batch_idx + 1}/{len(batches)}: {len(batch)} trials")
        print(f"Running SWE-bench evaluation "
              f"(workers={args.max_workers}, timeout={args.timeout}s)...")

        batch_results = run_swebench_evaluation_batch(
            trials=batch,
            dataset=dataset,
            run_id=batch_run_id,
            max_workers=args.max_workers,
            timeout=args.timeout,
        )
        all_eval_results.update(batch_results)

    # Update trial results
    print("\nUpdating trial results...")
    stats = update_trial_results(trials, all_eval_results)

    # Summary
    print()
    print("=" * 60)
    print("Reverification Summary")
    print("=" * 60)
    print(f"  Total trials:    {stats['total']}")
    print(f"  Resolved:        {stats['resolved']}")
    print(f"  Unresolved:      {stats['unresolved']}")
    print(f"  Eval failed:     {stats['eval_failed']}")
    print(f"  Empty patch:     {stats['empty_patch']}")
    print(f"  Results updated: {stats['updated']}")

    if stats["total"] > 0:
        evaluated = stats["resolved"] + stats["unresolved"]
        if evaluated > 0:
            resolve_rate = stats["resolved"] / evaluated * 100
            print(
                f"\n  Resolve rate: "
                f"{stats['resolved']}/{evaluated} ({resolve_rate:.1f}%)"
            )

    # Save detailed results
    output_path = (
        args.output or str(results_dir / "revision" / "reverify_results.json")
    )
    output_data = {
        "run_id": run_id,
        "split": args.benchmark_split,
        "timestamp": datetime.now().isoformat(),
        "stats": stats,
        "results": {
            trial_name: {
                "instance_id": r["instance_id"],
                "resolved": r["resolved"],
                "completed": r["completed"],
            }
            for trial_name, r in all_eval_results.items()
        },
    }
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output_data, f, indent=2)
    print(f"\n  Detailed results: {output_path}")

    # Remind about RRR recomputation
    print(f"\n  To recompute RRR with corrected results:")
    print(f"    python SWE-Review-Bench/compute_rrr.py \\")
    print(f"        --results-dir {results_dir} \\")
    print(f"        --benchmark-split {args.benchmark_split} \\")
    print(f"        --output {results_dir}/rrr_metrics.json")


if __name__ == "__main__":
    main()
