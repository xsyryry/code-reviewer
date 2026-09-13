"""Download SWE-Review data from HuggingFace.

Usage:
    python data_pipeline/download_data.py --all
    python data_pipeline/download_data.py --benchmark
    python data_pipeline/download_data.py --trajectories
"""

import argparse
import json
from pathlib import Path


def download_benchmark(output_dir: str = "data/swebench/benchmark/splits"):
    """Download SWE-Review-Bench splits from HuggingFace.

    Produces the directory structure expected by generate_review_tasks.py:
      splits/<split_name>/
        ├── patches_filtered.json   — {instances: {id: {model_patch, resolved, ...}}}
        ├── pr_contents.json        — {instances: {id: {pr_title, pr_body}}}
        └── instances.json          — {instances: {id: {image_name, base_commit, ...}}}
    """
    from datasets import load_dataset

    output_path = Path(output_dir)

    splits = ["glm5_500", "qwen3_coder_30b_500", "qwen3_30b_instruct2507_500"]

    for split in splits:
        print(f"Downloading benchmark split: {split}...")
        ds = load_dataset("Lego-X/SWE-Review-Bench", split=split)
        data = ds.to_list()

        split_dir = output_path / split
        split_dir.mkdir(parents=True, exist_ok=True)

        # Split into the three files expected by generate_review_tasks.py
        patches = {}
        pr_contents = {}
        instances_meta = {}

        for item in data:
            iid = item["instance_id"]

            patches[iid] = {
                "model_patch": item.get("model_patch", ""),
                "resolved": item.get("resolved", False),
            }

            pr_contents[iid] = {
                "pr_title": item.get("pr_title", ""),
                "pr_body": item.get("pr_body", ""),
            }

            instances_meta[iid] = {
                "instance_id": iid,
                "image_name": item.get("image_name", ""),
                "base_commit": item.get("base_commit", ""),
                "repo": item.get("repo", ""),
                "version": item.get("version", ""),
                "problem_statement": item.get("problem_statement", ""),
                "test_patch": item.get("test_patch", ""),
            }

        # Write patches_filtered.json
        with open(split_dir / "patches_filtered.json", "w", encoding="utf-8") as f:
            json.dump({"instances": patches}, f, ensure_ascii=False, indent=2)

        # Write pr_contents.json
        with open(split_dir / "pr_contents.json", "w", encoding="utf-8") as f:
            json.dump({"instances": pr_contents}, f, ensure_ascii=False, indent=2)

        # Write instances.json
        with open(split_dir / "instances.json", "w", encoding="utf-8") as f:
            json.dump({"instances": instances_meta}, f, ensure_ascii=False, indent=2)

        print(f"  Saved {len(data)} instances to {split_dir}/")

    print(f"\nBenchmark data saved to {output_dir}/")
    print("Splits available: " + ", ".join(splits))


def download_trajectories(output_dir: str = "data/trajectories"):
    """Download SWE-Review-Traj dataset from HuggingFace."""
    from datasets import load_dataset

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    print("Downloading SWE-Review-Traj...")
    ds = load_dataset("Lego-X/SWE-Review-Traj", split="train")
    data = ds.to_list()
    print(f"  Total trajectories: {len(data)}")

    # Split by decision correctness
    correct = []
    incorrect = []
    for item in data:
        decision = item.get("decision", "")
        patch_resolved = item.get("patch_resolved", False)
        # Decision is correct if: approve + resolved, or request_changes + not resolved
        is_correct = (
            (decision == "approve" and patch_resolved) or
            (decision == "request_changes" and not patch_resolved)
        )
        if is_correct:
            correct.append(item)
        else:
            incorrect.append(item)

    for subset, filename in [
        (correct, "swe_review_traj_decision_correct.json"),
        (incorrect, "swe_review_traj_decision_incorrect.json"),
    ]:
        out_file = output_path / filename
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(subset, f, ensure_ascii=False)
        print(f"  Saved {len(subset)} trajectories to {out_file}")

    print(f"\nTrajectory data saved to {output_dir}/")


def download_sft_data(output_dir: str = "data/sft", tokenizer_name: str = "Qwen/Qwen3-8B"):
    """Download and convert trajectories to LLaMA-Factory SFT format.

    Raw HF trajectories are in OpenAI chat format (with tool_calls, tool role, etc.).
    This function converts them to ShareGPT format via tokenizer.apply_chat_template(),
    producing pure {role, content} messages compatible with LLaMA-Factory.
    """
    from datasets import load_dataset
    from transformers import AutoTokenizer

    # Import conversion functions from prepare_review_sft_data.py
    from prepare_review_sft_data import (
        preprocess_messages,
        split_rendered_text,
        add_error_mask,
    )

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    print(f"Loading tokenizer: {tokenizer_name}")
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_name, trust_remote_code=True)

    print("Downloading trajectories for SFT...")
    ds = load_dataset("Lego-X/SWE-Review-Traj", split="train")

    # Filter decision-correct and convert to ShareGPT format
    sft_data = []
    skipped = 0
    for idx, item in enumerate(ds):
        decision = item.get("decision", "")
        patch_resolved = item.get("patch_resolved", False)
        is_correct = (
            (decision == "approve" and patch_resolved) or
            (decision == "request_changes" and not patch_resolved)
        )
        if not is_correct:
            continue

        # Parse messages and tools (JSON strings from HF)
        messages = item["messages"]
        if isinstance(messages, str):
            messages = json.loads(messages)

        tools_str = item.get("tools", "[]")
        tools = json.loads(tools_str) if isinstance(tools_str, str) and tools_str else []

        # Convert OpenAI format → ShareGPT format via tokenizer
        try:
            msgs = preprocess_messages(messages)
            text = tokenizer.apply_chat_template(
                msgs, tools=tools, tokenize=False, add_generation_prompt=False
            )
            result = split_rendered_text(text)
            result = add_error_mask(result)
            sft_data.append({"messages": result})
        except Exception as e:
            skipped += 1
            if skipped <= 5:
                print(f"  [WARN] Conversion failed for row {idx}: {e}")

        if (idx + 1) % 1000 == 0:
            print(f"  Processed {idx + 1}/{len(ds)} rows...")

    out_file = output_path / "swe_review_traj_decision_correct.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(sft_data, f, ensure_ascii=False)
    print(f"Saved {len(sft_data)} SFT records to {out_file} (skipped {skipped})")

    # Create placeholder for patchgen resolved data (needed by mixed training config)
    patchgen_file = output_path / "swe_review_patchgen_resolved.json"
    if not patchgen_file.exists():
        with open(patchgen_file, "w", encoding="utf-8") as f:
            json.dump([], f)
        print(f"\nNote: Created empty {patchgen_file}")
        print("  Mixed training (configs/mixed_6k_8b.yaml) requires patchgen resolved data.")
        print("  Generate it via: python scripts/data_pipeline/prepare_review_sft_data.py --patchgen-job-dir <your_patchgen_job>")

    print(f"\nSFT data ready at: {out_file}")
    print("Training configs already reference this path via dataset_info.json.")


def main():
    parser = argparse.ArgumentParser(description="Download SWE-Review data")
    parser.add_argument("--all", action="store_true", help="Download everything")
    parser.add_argument("--benchmark", action="store_true", help="Download benchmark only")
    parser.add_argument("--trajectories", action="store_true", help="Download trajectories only")
    parser.add_argument("--sft", action="store_true", help="Download and prepare SFT data")
    parser.add_argument("--tokenizer", default="Qwen/Qwen3-8B",
                        help="Tokenizer for rendering OpenAI messages to ShareGPT format (default: Qwen/Qwen3-8B)")
    parser.add_argument("--output-dir", default="data", help="Base output directory")
    args = parser.parse_args()

    if not any([args.all, args.benchmark, args.trajectories, args.sft]):
        parser.print_help()
        return

    if args.all or args.benchmark:
        download_benchmark("data/swebench/benchmark/splits")

    if args.all or args.trajectories:
        download_trajectories(f"{args.output_dir}/trajectories")

    if args.all or args.sft:
        download_sft_data("data/sft", tokenizer_name=args.tokenizer)


if __name__ == "__main__":
    main()
