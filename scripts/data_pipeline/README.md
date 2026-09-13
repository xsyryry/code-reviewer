# Data Pipeline

Complete workflow for collecting review trajectories and preparing SFT training data.

## Overview

```
SWE-rebench instances → Patchgen (generate PRs) → Review (teacher model)
→ Filter (decision correctness) → Prepare SFT data
```

## Prerequisites

- **Conda env**: `harbor` (see main README for installation)
- **Docker**: Required for Harbor agent execution
- **vLLM**: For serving patchgen/review models
- **GPU**: At least 1x H100/A100 for model serving

## Quick Start: Download Pre-collected Data

Skip the full pipeline and download our pre-collected trajectories:

```bash
conda activate harbor
python scripts/data_pipeline/download_data.py --sft
```

This downloads the SFT-ready data to `data/sft/`.

## Full Pipeline

### Step 1: Generate Patchgen Tasks

Create Harbor tasks from SWE-bench instances:

```bash
python scripts/data_pipeline/generate_patchgen_tasks.py \
    --instances-file data/swerebench_instances.txt \
    --output-dir tasks/patchgen_batch \
    --dataset swebench
```

### Step 2: Run Patchgen (Generate Candidate PRs)

```bash
export LLM_API_KEY="your-api-key"
export LLM_BASE_URL="http://172.17.0.1:8000/v1"  # Docker bridge to host vLLM

harbor run -a openhands-sdk \
    -m hosted_vllm/Qwen3-Coder-30B-A3B-Instruct \
    --ak max_iterations=100 \
    -p tasks/patchgen_batch \
    -n 16 --timeout-multiplier 3 \
    -o outputs/patchgen
```

### Step 3: Extract Patches

```bash
python scripts/data_pipeline/extract_patches.py \
    --job-dir outputs/patchgen \
    --patches-output data/candidate_patches.json \
    --pr-output data/candidate_pr_contents.json
```

### Step 4: Generate Review Tasks

```bash
python scripts/data_pipeline/generate_review_tasks.py \
    --patches data/candidate_patches.json \
    --output tasks/review_batch
```

### Step 5: Run Agentic Review (Teacher Model)

```bash
export LLM_API_KEY="your-api-key"
export LLM_BASE_URL="http://172.17.0.1:8001/v1"  # Separate port for reviewer

harbor run -a openhands-sdk \
    -m hosted_vllm/GLM-5 \
    --ak max_iterations=100 \
    -p tasks/review_batch \
    -n 16 --timeout-multiplier 3 \
    -o outputs/review_trajectories
```

### Step 6: Filter and Prepare SFT Data

```bash
python scripts/data_pipeline/prepare_review_sft_data.py \
    --job-dir outputs/review_trajectories \
    --tokenizer Qwen/Qwen3-8B \
    --output data/sft/swe_review_traj.json
```

## Script Reference

| Script | Purpose |
|--------|---------|
| `download_data.py` | Download pre-collected data from HuggingFace |
| `generate_patchgen_tasks.py` | Create Harbor patchgen tasks from SWE-bench |
| `extract_patches.py` | Extract patches from patchgen job outputs |
| `generate_review_tasks.py` | Create Harbor review tasks from patches |
| `prepare_review_sft_data.py` | Convert review trajectories to SFT format |

## Notes

- **Docker bridge IP**: Harbor runs agents in Docker containers. Use `172.17.0.1` to reach host vLLM from inside containers.
- **Concurrency**: `-n 16` is a good default. Reduce if you hit rate limits or OOM.
- **Filtering**: `prepare_review_sft_data.py` filters by decision correctness using `verifier/report.json` reward field (1 = correct decision).
- **Tokenizer**: Must match your target training model for correct chat template rendering.
