"""Compute Decision Accuracy (DA) for SWE-Review evaluation.

DA measures whether the reviewer makes the correct approve/request_changes
decision compared to the patch's true resolve status.

Metrics:
- DA: correct / produced (reviews that produced a parseable decision)
- DA_total_50: (correct + 0.5 * model_fail) / evaluable
- CR: completion rate (produced / evaluable)
- FAR: false approve rate
- FRR: false reject rate
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import load_review_results, extract_decision


def compute_da(results: list[dict], benchmark: list[dict]) -> dict:
    """Compute Decision Accuracy metrics.

    Args:
        results: Review results with instance_id and report
        benchmark: Benchmark data with instance_id and candidate_resolved

    Returns:
        Dictionary with DA, DA_total_50, CR, FAR, FRR, and counts
    """
    # Build ground truth lookup
    gt = {item["instance_id"]: item["candidate_resolved"] for item in benchmark}

    total = 0
    correct = 0
    model_fail = 0
    tp = fp = tn = fn = 0

    for result in results:
        iid = result["instance_id"]
        if iid not in gt:
            continue

        total += 1
        resolved = gt[iid]
        decision = extract_decision(result.get("report"))

        if decision is None:
            model_fail += 1
            continue

        is_approve = decision == "approve"

        if is_approve and resolved:
            tp += 1
            correct += 1
        elif is_approve and not resolved:
            fp += 1  # False approve
        elif not is_approve and not resolved:
            tn += 1
            correct += 1
        elif not is_approve and resolved:
            fn += 1  # False reject

    produced = total - model_fail
    evaluable = total

    da = correct / produced if produced > 0 else 0
    da_total_50 = (correct + 0.5 * model_fail) / evaluable if evaluable > 0 else 0
    cr = produced / evaluable if evaluable > 0 else 0
    far = fp / (fp + tn) if (fp + tn) > 0 else 0
    frr = fn / (fn + tp) if (fn + tp) > 0 else 0

    return {
        "DA": round(da * 100, 1),
        "DA_total_50": round(da_total_50 * 100, 1),
        "CR": round(cr * 100, 1),
        "FAR": round(far * 100, 1),
        "FRR": round(frr * 100, 1),
        "total": total,
        "correct": correct,
        "model_fail": model_fail,
        "produced": produced,
        "TP": tp,
        "FP": fp,
        "TN": tn,
        "FN": fn,
    }


def main():
    parser = argparse.ArgumentParser(description="Compute Decision Accuracy")
    parser.add_argument("--results-dir", required=True, help="Directory with review results")
    parser.add_argument("--benchmark-split", default=None, help="Benchmark split name")
    parser.add_argument("--benchmark-file", default=None, help="Path to benchmark JSON")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    # Load results
    results = load_review_results(args.results_dir)

    # Load benchmark
    if args.benchmark_file:
        with open(args.benchmark_file) as f:
            benchmark = json.load(f)
    elif args.benchmark_split:
        from utils import load_benchmark_split
        benchmark = load_benchmark_split(args.benchmark_split)
    else:
        raise ValueError("Provide --benchmark-split or --benchmark-file")

    # Compute metrics
    metrics = compute_da(results, benchmark)

    # Print results
    print(f"\n{'='*50}")
    print(f"Decision Accuracy Results")
    print(f"{'='*50}")
    print(f"  Evaluable instances: {metrics['total']}")
    print(f"  Produced reviews:    {metrics['produced']} (CR: {metrics['CR']}%)")
    print(f"  Model failures:      {metrics['model_fail']}")
    print(f"{'='*50}")
    print(f"  DA:          {metrics['DA']}%")
    print(f"  DA(total,50): {metrics['DA_total_50']}%")
    print(f"  FAR:         {metrics['FAR']}%")
    print(f"  FRR:         {metrics['FRR']}%")
    print(f"{'='*50}")
    print(f"  TP={metrics['TP']} FP={metrics['FP']} TN={metrics['TN']} FN={metrics['FN']}")
    print()

    # Save if requested
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(metrics, f, indent=2)
        print(f"Saved to {args.output}")


if __name__ == "__main__":
    main()
