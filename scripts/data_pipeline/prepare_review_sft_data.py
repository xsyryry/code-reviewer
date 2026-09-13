#!/usr/bin/env python3
"""
Generate SFT training data (ShareGPT format) from Harbor Review Job trajectories.

Uses tokenizer.apply_chat_template() to render raw API messages directly,
ensuring train-inference format consistency without manual fn_call_converter alignment.

Pipeline:
1. Extract recommendation decision from review report
2. Filter by decision accuracy using verifier/report.json reward field (1=correct)
3. Load the full message history from the last completion
4. Render via tokenizer.apply_chat_template() -> split into role/content + error mask
5. Output ShareGPT JSON: [{messages: [{role, content, turn_mask}]}]

Usage:
    python3 scripts/data_pipeline/prepare_review_sft_data.py \
        --job-dir outputs/review_trajectories \
        --patchgen-job-dir outputs/patchgen \
        --output data/sft/review_patchgen_merged.json \
        --tokenizer Qwen/Qwen3-30B-A3B
"""

import argparse
import copy
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent

# Import from local utilities
sys.path.insert(0, str(Path(__file__).parent))
from _utils import collect_all_trials, process_trial_review as process_trial

# ---- Error Mask ----


ERROR_PATTERN = re.compile(
    r"<tool_response>\s*(?:.*?)(?:ERROR:|"
    r"\[The command completed with exit code ([1-9]\d*)\.\])",
    re.DOTALL,
)

# Error observations for unregistered tools / invalid arguments (OpenHands does not
# record the corresponding assistant tool_call, resulting in consecutive user messages
# which LLaMA-Factory rejects)
INVALID_TOOL_OBSERVATION = re.compile(
    r"^\s*Tool \S+ is not registered\.|^Unexpected argument\b",
)


def add_error_mask(messages: list[dict]) -> list[dict]:
    """Apply turn_mask: all non-assistant turns are masked,
    and if a user turn contains an error, the preceding assistant turn is also masked."""
    for msg in messages:
        msg["turn_mask"] = msg["role"] != "assistant"

    for i, msg in enumerate(messages):
        if msg["role"] == "user" and ERROR_PATTERN.search(msg["content"]):
            if i > 0 and messages[i - 1]["role"] == "assistant":
                messages[i - 1]["turn_mask"] = True

    return messages


# ---- Message Preprocessing ----


def preprocess_messages(messages: list[dict]) -> list[dict]:
    """Preprocess raw API messages for tokenizer.apply_chat_template():
    1. Flatten content: [{type: text, text: x}] -> "x", None -> ""
    2. Parse tool_call arguments: JSON string -> dict
    3. Filter invalid tool observations (unregistered tool / invalid arguments)
    """
    msgs = copy.deepcopy(messages)
    result = []
    for m in msgs:
        # Flatten content
        content = m.get("content")
        if isinstance(content, list):
            m["content"] = "\n".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            )
        elif content is None:
            m["content"] = ""

        # Parse tool_call arguments (chat_template expects dict, not JSON string)
        if m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = tc.get("function", tc)
                if isinstance(fn.get("arguments"), str):
                    fn["arguments"] = json.loads(fn["arguments"])

        # Filter invalid tool observations
        if (
            m["role"] == "tool"
            and INVALID_TOOL_OBSERVATION.search(m["content"])
        ):
            continue

        result.append(m)
    return result


def split_rendered_text(text: str) -> list[dict]:
    """Split text rendered by apply_chat_template into [{role, content}].

    Rendered format: <|im_start|>role\\ncontent<|im_end|>\\n
    """
    parts = text.split("<|im_end|>")[:-1]
    result = []
    for part in parts:
        s = part.lstrip("\n")
        role = s.split("\n")[0].split("<|im_start|>")[-1]
        content = "\n".join(s.split("\n")[1:])
        result.append({"role": role, "content": content})
    return result


# ---- Completion Loading ----


def load_trajectory_from_completions(trial_dir: Path) -> dict | None:
    """Load the last completion file, concatenate messages + response into a full trajectory."""
    completions_dir = trial_dir / "agent" / "completions"
    if not completions_dir.exists():
        return None

    files = sorted(completions_dir.glob("*.json"))
    if not files:
        return None

    last_file = files[-1]
    try:
        with open(last_file) as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return None

    messages = data.get("messages", [])
    response = data.get("response", {})
    choices = response.get("choices", [])
    if not choices:
        return None

    resp_msg_raw = choices[0].get("message", {})

    # Load tool definitions from trajectory (OpenAI format, consistent with vLLM inference)
    tools: list[dict] = []
    trajectory_path = trial_dir / "agent" / "trajectory.json"
    if trajectory_path.exists():
        try:
            traj = json.loads(trajectory_path.read_text())
            tools = traj.get("agent", {}).get("tool_definitions") or []
        except (json.JSONDecodeError, IOError):
            print(f"  [WARN] Failed to read trajectory.json: {trajectory_path}")
    else:
        print(f"  [WARN] trajectory.json not found, tools=[] (may cause train-inference mismatch): {trial_dir}")

    # Append response to messages
    resp_msg = {
        "role": resp_msg_raw.get("role", "assistant"),
        "content": resp_msg_raw.get("content"),
    }
    if resp_msg_raw.get("tool_calls"):
        resp_msg["tool_calls"] = resp_msg_raw["tool_calls"]
    # Preserve reasoning_content (thinking models like GLM-5 include it at every step)
    if resp_msg_raw.get("reasoning_content"):
        resp_msg["reasoning_content"] = resp_msg_raw["reasoning_content"]
    messages.append(resp_msg)

    return {"messages": messages, "tools": tools}


# ---- Trajectory Conversion ----


def convert_trajectory(
    messages: list[dict],
    tools: list[dict],
    tokenizer,
) -> list[dict] | None:
    """Full conversion pipeline: preprocess -> apply_chat_template -> split -> error mask.

    Uses the tokenizer's native chat_template for rendering, ensuring train-inference consistency.
    Returns [{role, content, turn_mask}] or None on conversion failure.
    """
    try:
        # 1. Preprocess (flatten content + parse arguments + filter invalid tools)
        msgs = preprocess_messages(messages)

        # 2. Render using tokenizer's native chat_template
        text = tokenizer.apply_chat_template(
            msgs, tools=tools, tokenize=False, add_generation_prompt=False
        )

        # 3. Split rendered text into {role, content} list
        result = split_rendered_text(text)

        # 4. Filter invalid tool observations post-conversion
        #    (some invalid observations have role:user instead of role:tool)
        result = [
            m for m in result
            if not (
                m["role"] == "user"
                and INVALID_TOOL_OBSERVATION.search(m["content"])
            )
        ]

        # 5. Add error mask
        result = add_error_mask(result)

        return result

    except Exception as e:
        print(f"  [WARN] Trajectory conversion failed: {e}")
        return None


# ---- Decision Extraction ----


def extract_decision(report: dict) -> bool | None:
    """Extract decision from review report (approve=True, request_changes=False).

    Uses the recommendation field (standard output format).
    """
    if not report:
        return None

    # Primary: recommendation field (standard format)
    rec = report.get("recommendation")
    if rec == "approve":
        return True
    if rec == "request_changes":
        return False

    # Fallback: decision.recommendation (legacy format)
    decision = report.get("decision", {})
    if isinstance(decision, dict):
        rec = decision.get("recommendation")
        if rec == "approve":
            return True
        if rec == "request_changes":
            return False

    return None


# ---- Patchgen Data Collection ----


def collect_patchgen_samples(
    job_dir: Path, tokenizer
) -> list[dict]:
    """Collect resolved trajectories from patchgen job. Filter: reward == 1."""
    samples = []
    trial_dirs = sorted([
        d for d in job_dir.iterdir()
        if d.is_dir()
    ])
    stats = {"total": 0, "resolved": 0, "loaded": 0, "converted": 0}

    for trial_dir in trial_dirs:
        stats["total"] += 1
        # Check reward
        reward_file = trial_dir / "verifier" / "reward.txt"
        if not reward_file.exists():
            continue
        reward = reward_file.read_text().strip()
        if reward != "1":
            continue
        stats["resolved"] += 1

        # Load trajectory
        traj = load_trajectory_from_completions(trial_dir)
        if traj is None:
            continue
        stats["loaded"] += 1

        # Convert
        converted = convert_trajectory(
            traj["messages"], traj["tools"], tokenizer
        )
        if converted is None:
            continue
        stats["converted"] += 1
        samples.append({"messages": converted})

    print(f"  Patchgen stats: {stats}")
    return samples


# ---- Main ----


def main():
    parser = argparse.ArgumentParser(
        description="Generate SFT training data from Harbor Review Job trajectories"
    )
    parser.add_argument(
        "--job-dir", required=True, help="Harbor job directory"
    )
    parser.add_argument(
        "--output", "-o", required=True, help="Output ShareGPT JSON path"
    )
    parser.add_argument(
        "--cache-dir", default="datasets", help="HF cache directory"
    )
    parser.add_argument(
        "--max-instances",
        type=int,
        default=None,
        help="Limit number of instances to process",
    )
    parser.add_argument(
        "--patchgen-job-dir",
        default=None,
        help="Patchgen job directory (collect resolved trajectories as bug-fix SFT data)",
    )
    parser.add_argument(
        "--tokenizer",
        default="Qwen/Qwen3-Coder-30B-A3B-Instruct",
        help="HuggingFace tokenizer name or path",
    )
    args = parser.parse_args()

    job_dir = Path(args.job_dir)
    if not job_dir.is_absolute():
        job_dir = PROJECT_ROOT / job_dir

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_path

    # ---- 0. Load Tokenizer ----
    print("=" * 60)
    print("Step 0: Load Tokenizer")
    print("=" * 60)
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer)
    print(f"  Tokenizer: {args.tokenizer}")
    print(f"  Vocab size: {tokenizer.vocab_size}")

    # ---- 1. Collect all trials ----
    print("\n" + "=" * 60)
    print("Step 1: Collect Harbor trials")
    print("=" * 60)
    trials = collect_all_trials(job_dir)
    print(f"  Found {len(trials)} trial directories")

    if args.max_instances:
        trials = trials[: args.max_instances]
        print(f"  Limited to first {args.max_instances}")

    # ---- 2. Extract + Filter + Convert ----
    print("\n" + "=" * 60)
    print("Step 2: Extract review report -> decision filtering -> trajectory conversion")
    print("=" * 60)

    stats = {
        "total_trials": len(trials),
        "report_extracted": 0,
        "gt_found": 0,
        "decision_accurate": 0,
        "trajectory_loaded": 0,
        "conversion_success": 0,
        # Decision breakdown
        "bug_fixed_true_resolved_true": 0,   # TP
        "bug_fixed_false_resolved_false": 0,  # TN
        "bug_fixed_true_resolved_false": 0,   # FP (false positive)
        "bug_fixed_false_resolved_true": 0,   # FN (false negative)
    }

    output = []

    for i, trial_dir in enumerate(trials):
        trial_name = trial_dir.name
        if (i + 1) % 50 == 0 or i == 0:
            print(f"  Processing {i + 1}/{len(trials)}: {trial_name}")

        # 2a. Extract review report
        trial_data = process_trial(trial_dir)
        instance_id = trial_data["instance_id"]
        report = trial_data["review_report"]

        if report is None:
            continue
        stats["report_extracted"] += 1

        # Read verifier/report.json reward field (1=correct decision)
        verifier_report_path = trial_dir / "verifier" / "report.json"
        if not verifier_report_path.exists():
            continue
        try:
            verifier_report = json.loads(verifier_report_path.read_text())
        except (json.JSONDecodeError, IOError):
            continue
        reward = verifier_report.get("reward")
        if reward is None:
            continue
        stats["gt_found"] += 1
        # Decision breakdown
        decision = extract_decision(report)
        gt_resolved = verifier_report.get("gt_resolved")
        if decision is True and gt_resolved:
            stats["bug_fixed_true_resolved_true"] += 1
        elif decision is False and not gt_resolved:
            stats["bug_fixed_false_resolved_false"] += 1
        elif decision is True and not gt_resolved:
            stats["bug_fixed_true_resolved_false"] += 1
        else:
            stats["bug_fixed_false_resolved_true"] += 1
        if reward != 1:
            continue
        stats["decision_accurate"] += 1

        # 2c. Load completion trajectory
        traj_data = load_trajectory_from_completions(trial_dir)
        if traj_data is None:
            continue
        stats["trajectory_loaded"] += 1

        # 2d. Convert trajectory
        converted = convert_trajectory(
            traj_data["messages"], traj_data["tools"], tokenizer
        )
        if converted is None:
            continue
        stats["conversion_success"] += 1

        output.append({"messages": converted})

    n_review_samples = len(output)

    # ---- 2.5. Collect patchgen samples (optional) ----
    n_patchgen_samples = 0
    if args.patchgen_job_dir:
        print("\n" + "=" * 60)
        print("Step 2.5: Collect Patchgen trajectories (resolved only)")
        print("=" * 60)
        patchgen_dir = Path(args.patchgen_job_dir)
        if not patchgen_dir.is_absolute():
            patchgen_dir = PROJECT_ROOT / patchgen_dir
        # patchgen job may have multiple timestamp subdirectories; use the latest
        if not any((patchgen_dir / d.name / "verifier").exists()
                   for d in patchgen_dir.iterdir() if d.is_dir()):
            # May be a job parent directory; use the latest timestamp
            subdirs = sorted(
                [d for d in patchgen_dir.iterdir() if d.is_dir()],
                key=lambda d: d.name, reverse=True,
            )
            if subdirs:
                patchgen_dir = subdirs[0]
                print(f"  Using latest patchgen job: {patchgen_dir.name}")

        patchgen_samples = collect_patchgen_samples(
            patchgen_dir, tokenizer
        )
        n_patchgen_samples = len(patchgen_samples)
        output.extend(patchgen_samples)
        print(f"  Review samples: {n_review_samples}")
        print(f"  Patchgen samples: {n_patchgen_samples}")
        print(f"  Merged total: {len(output)}")

    # ---- 3. Output ----
    print("\n" + "=" * 60)
    print("Step 3: Write output file")
    print("=" * 60)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"  Output: {output_path}")
    print(f"  Sample count: {len(output)}")

    # ---- 4. Statistics ----
    print("\n" + "=" * 60)
    print("Statistics Summary")
    print("=" * 60)
    stats["output_samples"] = len(output)
    stats["review_samples"] = n_review_samples
    stats["patchgen_samples"] = n_patchgen_samples
    if stats["gt_found"] > 0:
        stats["accuracy_rate"] = round(
            stats["decision_accurate"] / stats["gt_found"] * 100, 1
        )
    else:
        stats["accuracy_rate"] = 0.0

    if stats["total_trials"] > 0:
        stats["filtering_rate"] = round(
            len(output) / stats["total_trials"] * 100, 1
        )
    else:
        stats["filtering_rate"] = 0.0

    for k, v in stats.items():
        print(f"  {k}: {v}")

    # Write statistics JSON
    stats_path = output_path.with_suffix(".stats.json")
    with open(stats_path, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"\n  Statistics file: {stats_path}")

    # ---- 5. Data quality quick check ----
    if output:
        print("\n" + "=" * 60)
        print("Data quality check (first 3 samples)")
        print("=" * 60)
        for idx, sample in enumerate(output[:3]):
            msgs = sample["messages"]
            n_msgs = len(msgs)
            roles = [m["role"] for m in msgs]
            n_assistant = roles.count("assistant")
            n_user = roles.count("user")
            n_system = roles.count("system")
            masked_assistant = sum(
                1
                for m in msgs
                if m["role"] == "assistant" and m.get("turn_mask")
            )
            total_chars = sum(len(m["content"]) for m in msgs)
            print(
                f"  Sample {idx}: {n_msgs} msgs "
                f"(sys={n_system}, user={n_user}, asst={n_assistant}), "
                f"masked_asst={masked_assistant}, "
                f"total_chars={total_chars:,}"
            )


if __name__ == "__main__":
    main()
