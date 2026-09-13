"""Aggregate evaluation results across multiple splits."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Aggregate results across splits")
    parser.add_argument("--results-dirs", nargs="+", required=True)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    all_results = {}
    for results_dir in args.results_dirs:
        p = Path(results_dir)
        combined = {}

        # Load DA metrics
        da_path = p / "da_metrics.json"
        if da_path.exists():
            with open(da_path) as f:
                combined.update(json.load(f))

        # Load RRR metrics
        rrr_path = p / "rrr_metrics.json"
        if rrr_path.exists():
            with open(rrr_path) as f:
                combined.update(json.load(f))

        # Fallback: try metrics.json (legacy)
        if not combined:
            metrics_path = p / "metrics.json"
            if metrics_path.exists():
                with open(metrics_path) as f:
                    combined = json.load(f)

        if combined:
            all_results[p.name] = combined
        else:
            print(f"Warning: No metrics files in {results_dir}")

    if not all_results:
        print("No results found.")
        return

    # Print table
    print(f"\n{'Split':<35} {'DA%':>6} {'DA50%':>6} {'CR%':>5} {'FAR%':>5} {'FRR%':>5} {'RRR%':>6}")
    print("-" * 75)
    for split, metrics in sorted(all_results.items()):
        da = metrics.get("DA", "—")
        da50 = metrics.get("DA_total_50", "—")
        cr = metrics.get("CR", "—")
        far = metrics.get("FAR", "—")
        frr = metrics.get("FRR", "—")
        rrr = metrics.get("RRR", "—")
        print(f"{split:<35} {da:>6} {da50:>6} {cr:>5} {far:>5} {frr:>5} {rrr:>6}")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(all_results, f, indent=2)
        print(f"\nSaved to {args.output}")


if __name__ == "__main__":
    main()
