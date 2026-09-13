#!/usr/bin/env python3
"""
Single-turn review baseline for SWE-Review-Bench.

Sends the issue + patch (and optionally context) to a reviewer model in a single
API call, without agentic exploration. Used as a baseline comparison.

Usage:
    python SWE-Review-Bench/run_single_turn_review.py \
        --split glm5_500 \
        --setting diff_only \
        --model hosted_vllm/SWE-Review-8B \
        --api-url http://172.17.0.1:8000/v1 \
        --api-key dummy-key \
        --output outputs/benchmark/glm5_500_single_turn
"""

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent
PROMPTS_DIR = REPO_ROOT / "prompts"

sys.path.insert(0, str(SCRIPT_DIR))
from utils import load_benchmark_split


def load_prompt(setting: str) -> str:
    """Load the system prompt for the given setting."""
    if setting == "diff_only":
        path = PROMPTS_DIR / "single_turn_review_diff_only.md"
    elif setting == "diff_with_context":
        path = PROMPTS_DIR / "single_turn_review_with_context.md"
    else:
        raise ValueError(f"Unknown setting: {setting}. Use diff_only or diff_with_context")
    return path.read_text()


def build_user_message(instance: dict, setting: str) -> str:
    """Build the user message from benchmark instance data."""
    parts = []
    parts.append(f"## Issue\n\n{instance.get('problem_statement', 'N/A')}")
    parts.append(f"## Candidate Patch\n\n```diff\n{instance.get('model_patch', '')}\n```")
    if instance.get("pr_title"):
        parts.append(f"## PR Title\n\n{instance['pr_title']}")
    if instance.get("pr_body"):
        parts.append(f"## PR Description\n\n{instance['pr_body']}")
    return "\n\n".join(parts)


def call_api(
    system_prompt: str,
    user_message: str,
    model: str,
    api_url: str,
    api_key: str,
) -> dict | None:
    """Call the LLM API for a single review."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model.split("/")[-1] if "/" in model else model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.0,
        "max_tokens": 4096,
    }
    try:
        resp = requests.post(
            f"{api_url}/chat/completions", headers=headers, json=payload, timeout=120
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        # Try to parse JSON from response
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Try extracting JSON from markdown block
            import re
            match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            return None
    except Exception as e:
        print(f"  API error: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Single-turn review baseline")
    parser.add_argument("--split", required=True, help="Benchmark split name")
    parser.add_argument(
        "--setting", required=True, choices=["diff_only", "diff_with_context"],
        help="Review setting"
    )
    parser.add_argument("--model", required=True, help="Model name")
    parser.add_argument("--api-url", default=os.environ.get("LLM_BASE_URL", ""))
    parser.add_argument("--api-key", default=os.environ.get("LLM_API_KEY", "dummy-key"))
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument("--concurrency", type=int, default=16)
    args = parser.parse_args()

    if not args.api_url:
        print("Error: --api-url or LLM_BASE_URL env var required")
        sys.exit(1)

    # Load benchmark and prompt
    benchmark = load_benchmark_split(args.split)
    system_prompt = load_prompt(args.setting)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Single-turn review: {args.split} ({args.setting})")
    print(f"  Instances: {len(benchmark)}")
    print(f"  Model: {args.model}")
    print(f"  Output: {output_dir}")

    results = []

    def process_instance(instance):
        iid = instance["instance_id"]
        user_msg = build_user_message(instance, args.setting)
        report = call_api(system_prompt, user_msg, args.model, args.api_url, args.api_key)
        return {"instance_id": iid, "report": report}

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {executor.submit(process_instance, inst): inst for inst in benchmark}
        for i, future in enumerate(as_completed(futures)):
            result = future.result()
            results.append(result)
            if (i + 1) % 50 == 0:
                print(f"  Progress: {i + 1}/{len(benchmark)}")

    # Save results
    output_file = output_dir / "review_results.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved {len(results)} results to {output_file}")


if __name__ == "__main__":
    main()
