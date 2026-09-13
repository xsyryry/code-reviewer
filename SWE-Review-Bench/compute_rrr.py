"""Compute Resolve Rate after Revision (RRR) for SWE-Review evaluation.

RRR measures the final resolve rate after the review-then-revise loop:
- Approved patches are kept unchanged
- Rejected patches are revised based on review feedback
- Model failures fall back to the original patch

RRR = (approved_resolved + revised_resolved + fallback_resolved) / total
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import load_review_results, extract_decision


def compute_rrr(
    review_results: list[dict],
    revision_results: dict[str, bool],
    benchmark: list[dict],
) -> dict:
    """Compute Resolve Rate after Revision.

    Args:
        review_results: Review results with decisions
        revision_results: {instance_id: resolved} for revised patches
        benchmark: Benchmark data with candidate_resolved

    Returns:
        Dictionary with RRR and breakdown
    """
    gt = {item["instance_id"]: item["candidate_resolved"] for item in benchmark}

    total = 0
    approved_resolved = 0
    revised_resolved = 0
    fallback_resolved = 0
    approved_count = 0
    revised_count = 0
    fallback_count = 0

    for result in review_results:
        iid = result["instance_id"]
        if iid not in gt:
            continue

        total += 1
        original_resolved = gt[iid]
        decision = extract_decision(result.get("report"))

        if decision == "approve":
            approved_count += 1
            if original_resolved:
                approved_resolved += 1
        elif decision == "request_changes":
            revised_count += 1
            if iid in revision_results:
                if revision_results[iid]:
                    revised_resolved += 1
            else:
                # No revision result, fall back to original
                fallback_count += 1
                if original_resolved:
                    fallback_resolved += 1
        else:
            # Model failure — fall back to original patch
            fallback_count += 1
            if original_resolved:
                fallback_resolved += 1

    total_resolved = approved_resolved + revised_resolved + fallback_resolved
    rrr = total_resolved / total if total > 0 else 0

    # Baseline: no-review resolve rate
    baseline_resolved = sum(1 for item in benchmark if item["candidate_resolved"])
    baseline_rr = baseline_resolved / len(benchmark) if benchmark else 0

    return {
        "RRR": round(rrr * 100, 1),
        "baseline_RR": round(baseline_rr * 100, 1),
        "delta": round((rrr - baseline_rr) * 100, 1),
        "total": total,
        "total_resolved": total_resolved,
        "approved": approved_count,
        "approved_resolved": approved_resolved,
        "revised": revised_count,
        "revised_resolved": revised_resolved,
        "fallback": fallback_count,
        "fallback_resolved": fallback_resolved,
    }


def main():
    parser = argparse.ArgumentParser(description="Compute Resolve Rate after Revision")
    parser.add_argument("--results-dir", required=True, help="Directory with review+revision results")
    parser.add_argument("--review-dir", default=None, help="Review results directory (if separate)")
    parser.add_argument("--revision-dir", default=None, help="Revision results directory (if separate)")
    parser.add_argument("--benchmark-split", default=None)
    parser.add_argument("--benchmark-file", default=None)
    parser.add_argument("--rounds", type=int, default=None, help="For iterative: number of rounds")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    # Load benchmark
    if args.benchmark_file:
        with open(args.benchmark_file) as f:
            benchmark = json.load(f)
    elif args.benchmark_split:
        from utils import load_benchmark_split
        benchmark = load_benchmark_split(args.benchmark_split)
    else:
        raise ValueError("Provide --benchmark-split or --benchmark-file")

    # Load review results
    review_dir = args.review_dir or args.results_dir
    review_results = load_review_results(review_dir)

    # Load revision results
    revision_results = {}
    revision_dir = args.revision_dir or Path(args.results_dir) / "revision"
    if Path(revision_dir).exists():
        # Use the same trial-finding logic as review results
        from utils import _find_trial_dirs
        trial_dirs = _find_trial_dirs(Path(revision_dir))
        for trial_dir in trial_dirs:
            result_path = trial_dir / "result.json"
            if not result_path.exists():
                continue
            try:
                with open(result_path) as f:
                    result_data = json.load(f)
            except (json.JSONDecodeError, IOError):
                continue
            iid = result_data.get("task_name", "")
            if not iid:
                continue
            # Check verifier result for resolved status
            vr = result_data.get("verifier_result") or {}
            rewards = vr.get("rewards") or {}
            resolved = rewards.get("reward", 0.0) == 1.0
            revision_results[iid] = resolved

    # Compute metrics
    metrics = compute_rrr(review_results, revision_results, benchmark)

    print(f"\n{'='*50}")
    print(f"Resolve Rate after Revision (RRR)")
    print(f"{'='*50}")
    print(f"  Total instances:     {metrics['total']}")
    print(f"  Baseline RR:         {metrics['baseline_RR']}%")
    print(f"  RRR:                 {metrics['RRR']}%")
    print(f"  Delta:               {metrics['delta']:+.1f}pp")
    print(f"{'='*50}")
    print(f"  Approved:  {metrics['approved']} ({metrics['approved_resolved']} resolved)")
    print(f"  Revised:   {metrics['revised']} ({metrics['revised_resolved']} resolved)")
    print(f"  Fallback:  {metrics['fallback']} ({metrics['fallback_resolved']} resolved)")
    print()

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(metrics, f, indent=2)
        print(f"Saved to {args.output}")


if __name__ == "__main__":
    main()
