"""Iterative Review-Revision for Test-Time Scaling on SWE-bench Verified.

Runs a generate → review → revise loop for K rounds.
- Round 0: initial patchgen (generator model)
- Rounds 1–K: review (reviewer model) → revise (generator model)
- Early stopping: approved patches exit the loop

Supports two-model (separate generator + reviewer) and self-critique (same model).

Usage:
    # Two-model: 30B generator + 8B reviewer
    python scripts/iterative_tts.py \
        --generator hosted_vllm/Qwen3-30B-A3B --generator-url http://172.17.0.1:8000/v1 \
        --reviewer hosted_vllm/SWE-Review-8B --reviewer-url http://172.17.0.1:8001/v1 \
        --api-key dummy-key \
        --rounds 4 --output outputs/tts/run1

    # Self-critique: same mixed-6k model
    python scripts/iterative_tts.py \
        --generator hosted_vllm/SWE-Review-Mixed-6K-8B --generator-url http://172.17.0.1:8000/v1 \
        --reviewer hosted_vllm/SWE-Review-Mixed-6K-8B --reviewer-url http://172.17.0.1:8000/v1 \
        --api-key dummy-key \
        --rounds 4 --output outputs/tts/self_critique
"""

import argparse
import json
import os
import subprocess
from pathlib import Path


def run_harbor(model: str, api_url: str, api_key: str, task_dir: str, output_dir: str, concurrency: int = 16):
    """Run harbor with the specified model and API endpoint."""
    env = os.environ.copy()
    env["LLM_API_KEY"] = api_key
    env["LLM_BASE_URL"] = api_url

    cmd = [
        "harbor", "run",
        "-a", "openhands-sdk",
        "-m", model,
        "--ak", "max_iterations=100",
        "-p", task_dir,
        "-n", str(concurrency),
        "--timeout-multiplier", "3",
        "-o", output_dir,
    ]
    print(f"  CMD: {' '.join(cmd)}")
    print(f"  LLM_BASE_URL={api_url}")
    subprocess.run(cmd, env=env, check=True)


def extract_review_decisions(review_dir: str) -> dict:
    """Extract decisions from review results.

    Returns: {instance_id: {"decision": str, "report": dict}}
    """
    decisions = {}
    review_path = Path(review_dir)

    for trial_dir in sorted(review_path.iterdir()):
        if not trial_dir.is_dir():
            continue

        config_path = trial_dir / "config.json"
        if not config_path.exists():
            continue
        with open(config_path) as f:
            config = json.load(f)
        instance_id = config.get("task", {}).get("instance_id", trial_dir.name)

        report = None
        for name in ["report.json", "review_report.json"]:
            p = trial_dir / name
            if p.exists():
                with open(p) as f:
                    report = json.load(f)
                break

        decision = None
        if report and "decision" in report:
            d = report["decision"]
            decision = d.get("recommendation") if isinstance(d, dict) else d

        decisions[instance_id] = {"decision": decision, "report": report}

    return decisions


def generate_revision_tasks(
    review_decisions: dict,
    review_dir: str,
    output_task_dir: str,
    round_num: int,
):
    """Generate revision tasks for rejected instances with review feedback."""
    output_path = Path(output_task_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    rejected = []
    for iid, info in review_decisions.items():
        if info["decision"] == "request_changes":
            rejected.append(iid)

    print(f"  Round {round_num}: {len(rejected)} rejected → generating revision tasks")

    manifest = {
        "round": round_num,
        "rejected_instances": rejected,
        "total": len(review_decisions),
        "approved": sum(1 for v in review_decisions.values() if v["decision"] == "approve"),
        "model_fail": sum(1 for v in review_decisions.values() if v["decision"] is None),
    }

    with open(output_path / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    return rejected


def main():
    parser = argparse.ArgumentParser(description="Iterative Review-Revision (Test-Time Scaling)")
    parser.add_argument("--generator", required=True, help="Generator/reviser model name (e.g., hosted_vllm/Qwen3-30B-A3B)")
    parser.add_argument("--generator-url", required=True, help="Generator API URL (e.g., http://172.17.0.1:8000/v1)")
    parser.add_argument("--reviewer", required=True, help="Reviewer model name (e.g., hosted_vllm/SWE-Review-8B)")
    parser.add_argument("--reviewer-url", required=True, help="Reviewer API URL (e.g., http://172.17.0.1:8001/v1)")
    parser.add_argument("--api-key", default="dummy-key", help="API key (shared for both models)")
    parser.add_argument("--rounds", type=int, default=4, help="Number of review-revision rounds (K)")
    parser.add_argument("--instances", type=int, default=500, help="Number of SWE-bench instances")
    parser.add_argument("--concurrency", type=int, default=16, help="Harbor concurrency (-n)")
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument("--patchgen-tasks", default="harbor/tasks/patchgen", help="Initial patchgen task directory")
    args = parser.parse_args()

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    is_self_critique = (args.generator == args.reviewer and args.generator_url == args.reviewer_url)

    print("=" * 60)
    print("Iterative Review-Revision (Test-Time Scaling)")
    print("=" * 60)
    print(f"  Generator: {args.generator}")
    print(f"       URL:  {args.generator_url}")
    print(f"  Reviewer:  {args.reviewer}")
    print(f"       URL:  {args.reviewer_url}")
    print(f"  Mode:      {'Self-critique' if is_self_critique else 'Two-model'}")
    print(f"  Rounds:    {args.rounds}")
    print(f"  Instances: {args.instances}")
    print(f"  Output:    {args.output}")
    print("=" * 60)

    # Round 0: Initial patchgen (uses generator)
    print(f"\n[Round 0] Generating initial patches...")
    round0_dir = str(output / "round_0_patchgen")
    run_harbor(args.generator, args.generator_url, args.api_key, args.patchgen_tasks, round0_dir, args.concurrency)

    # Rounds 1–K: Review → Revision loop
    active_dir = round0_dir
    for k in range(1, args.rounds + 1):
        # Review phase (uses reviewer)
        print(f"\n[Round {k}] Review phase (reviewer: {args.reviewer})...")
        review_dir = str(output / f"round_{k}_review")
        run_harbor(args.reviewer, args.reviewer_url, args.api_key, active_dir, review_dir, args.concurrency)

        # Extract decisions
        decisions = extract_review_decisions(review_dir)
        approved = sum(1 for v in decisions.values() if v["decision"] == "approve")
        rejected = sum(1 for v in decisions.values() if v["decision"] == "request_changes")
        model_fail = sum(1 for v in decisions.values() if v["decision"] is None)
        print(f"  Results: {approved} approved, {rejected} rejected, {model_fail} model_fail")

        if rejected == 0:
            print(f"  No rejected patches — stopping early at round {k}")
            break

        # Generate revision tasks
        revision_tasks_dir = str(output / f"round_{k}_revision_tasks")
        generate_revision_tasks(decisions, review_dir, revision_tasks_dir, k)

        # Revision phase (uses generator)
        print(f"[Round {k}] Revision phase (generator: {args.generator})...")
        revision_dir = str(output / f"round_{k}_revision")
        run_harbor(args.generator, args.generator_url, args.api_key, revision_tasks_dir, revision_dir, args.concurrency)

        active_dir = revision_dir

    # Final summary
    print("\n" + "=" * 60)
    print("Iterative TTS complete.")
    print(f"Results saved to: {args.output}")
    print("Compute final metrics:")
    print(f"  bash SWE-Review-Bench/compute_metrics.sh {args.output}")
    print("=" * 60)


if __name__ == "__main__":
    main()
