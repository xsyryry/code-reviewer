# SWE-Review-Bench

Benchmark for evaluating agentic code reviewers. Contains 1,384 AI-generated PRs across 3 quality tiers from 500 SWE-bench Verified issues.

## Prerequisites

- **Docker** — for Harbor agent execution and SWE-bench test environments
- **Two conda environments** (see Installation in root README):
  - `vllm` — model serving (vLLM)
  - `harbor` — agent orchestration and evaluation
- **GPU** — for local model serving (not needed if using external API)

**Required environment variable** (must be set before any Harbor run):
```bash
export OPENHANDS_LLM_NATIVE_TOOL_CALLING=true
```

## One-Click Full Evaluation

Run all 3 splits (review + revision + metrics) with a single command.

**Mode 1: API reviewer + local revision models** (recommended for frontier models):
```bash
conda activate harbor
export OPENHANDS_LLM_NATIVE_TOOL_CALLING=true

bash SWE-Review-Bench/run_full_benchmark.sh --mode api \
    --reviewer-model openai/claude-opus-4-7 \
    --api-key "your-api-key" \
    --api-url "https://your-api-provider.com/v1" \
    --revision-url "https://your-api-provider.com/v1" \
    -n 32 \
    --output outputs/benchmark/claude_opus_47
```

When `--revision-url` is set, the script automatically decides per split:
- Models available on HuggingFace (Qwen3-Coder-30B-A3B-Instruct, Qwen3-30B-A3B-Instruct-2507) → deployed locally via vLLM
- Models not on HuggingFace (GLM-5) → uses the revision API URL

**Mode 2: Local reviewer + local revision** (for locally served models):
```bash
# Terminal 1: serve the reviewer model
conda activate vllm
python -m vllm.entrypoints.openai.api_server \
    --model Lego-X/SWE-Review-8B \
    --served-model-name SWE-Review-8B \
    --host 0.0.0.0 --port 8000 \
    --tensor-parallel-size 4 --gpu-memory-utilization 0.9 \
    --max-model-len 131072 --max-num-seqs 24 \
    --enable-auto-tool-choice --tool-call-parser hermes \
    --chat-template-content-format string --api-key dummy-key

# Terminal 2: run evaluation
conda activate harbor
export OPENHANDS_LLM_NATIVE_TOOL_CALLING=true

bash SWE-Review-Bench/run_full_benchmark.sh --mode api \
    --reviewer-model hosted_vllm/SWE-Review-8B \
    --api-key dummy-key \
    --api-url http://172.17.0.1:8000/v1 \
    -n 32 \
    --output outputs/benchmark/swe_review_8b
```

> **Note**: Use `172.17.0.1` (Docker bridge gateway) instead of `localhost` — Harbor runs agents inside Docker containers that cannot reach the host's localhost.

Both modes automatically:
1. Download benchmark data (if not present)
2. Run agentic review on all 3 splits
3. Run revision on rejected patches (using each split's original PR generator)
4. Compute DA and RRR metrics
5. Save aggregated results to `results_summary.json`

Add `--skip-revision` to only compute DA without revision.

### Full Options

| Option | Default | Description |
|--------|---------|-------------|
| `--mode` | (required) | `api` or `local` |
| `--reviewer-model` | (required) | Model name (e.g., `openai/claude-opus-4-7` or `hosted_vllm/SWE-Review-8B`) |
| `--api-key` | `dummy-key` | API key for the reviewer |
| `--api-url` | (required for api) | API URL for the reviewer |
| `--reviewer-parser` | `hermes` | Tool call parser for local mode |
| `--tp` | `4` | Tensor parallel size for reviewer |
| `--gpu-ids` | — | GPU IDs for reviewer (e.g., `0,1,2,3`) |
| `--port` | `8000` | vLLM port for reviewer |
| `--revision-url` | — | External API URL for revision models (if not set, deploys locally) |
| `--revision-api-key` | (same as `--api-key`) | API key for revision URL |
| `--revision-port` | `8020` | vLLM port for local revision models |
| `--revision-tp` | `4` | TP for revision models |
| `--revision-gpu-ids` | — | GPU IDs for revision |
| `--vllm-python` | (auto-detected) | Path to python with vLLM installed |
| `-n` / `--concurrency` | `16` | Harbor concurrency (number of parallel agents) |
| `--skip-revision` | — | Only compute DA, skip revision + RRR |

## Step-by-Step Evaluation

If you prefer manual control over each step:

```bash
# 1. Download benchmark data
conda activate harbor
python scripts/data_pipeline/download_data.py --benchmark

# 2. Serve your reviewer model (in a separate terminal)
conda activate vllm
python -m vllm.entrypoints.openai.api_server \
    --model Lego-X/SWE-Review-8B \
    --served-model-name SWE-Review-8B \
    --host 0.0.0.0 --port 8000 \
    --tensor-parallel-size 4 --gpu-memory-utilization 0.9 \
    --max-model-len 131072 --max-num-seqs 24 \
    --enable-auto-tool-choice --tool-call-parser hermes \
    --chat-template-content-format string --api-key dummy-key

# 3. Run agentic review on one split
conda activate harbor
export OPENHANDS_LLM_NATIVE_TOOL_CALLING=true
export LLM_API_KEY="dummy-key"
export LLM_BASE_URL="http://172.17.0.1:8000/v1"

bash SWE-Review-Bench/run_agentic_review.sh glm5_500 \
    hosted_vllm/SWE-Review-8B \
    outputs/benchmark/swe_review_8b/glm5_500

# 4. Compute DA
python SWE-Review-Bench/compute_da.py \
    --results-dir outputs/benchmark/swe_review_8b/glm5_500 \
    --benchmark-split glm5_500 \
    --output outputs/benchmark/swe_review_8b/glm5_500/da_metrics.json

# 5. Run revision (serve revision model, then run)
# For qwen3_coder_30b_500: revision model is Qwen3-Coder-30B-A3B-Instruct
# For glm5_500: GLM-5 is not on HuggingFace, use --revision-url with an API instead
export LLM_BASE_URL="http://172.17.0.1:8020/v1"
bash SWE-Review-Bench/run_revision.sh \
    outputs/benchmark/swe_review_8b/glm5_500 \
    hosted_vllm/GLM-5 \
    glm5_500

# 6. Compute RRR
python SWE-Review-Bench/compute_rrr.py \
    --results-dir outputs/benchmark/swe_review_8b/glm5_500 \
    --benchmark-split glm5_500 \
    --output outputs/benchmark/swe_review_8b/glm5_500/rrr_metrics.json
```

## Available Splits

| Split | PR Generator | Instances | Baseline Resolve Rate |
|-------|-------------|-----------|----------------------|
| `glm5_500` | GLM-5 | 500 | 72.2% |
| `qwen3_coder_30b_500` | Qwen3-Coder-30B-A3B-Instruct | 462 | 50.9% |
| `qwen3_30b_instruct2507_500` | Qwen3-30B-A3B-Instruct-2507 | 422 | 27.5% |

## Metrics

| Metric | Description |
|--------|-------------|
| **CR** (Completion Rate) | Fraction of instances with a parseable review |
| **DA** (Decision Accuracy) | Correct approve/reject decisions / produced reviews |
| **DA(total,50)** | (correct + 0.5 × model_fail) / evaluable |
| **FAR** (False Approve Rate) | Incorrectly approved / total non-resolved |
| **FRR** (False Reject Rate) | Incorrectly rejected / total resolved |
| **RRR** (Resolve Rate after Revision) | Final resolve rate after review-then-revise loop |

## Scripts

| Script | Usage | Description |
|--------|-------|-------------|
| `run_full_benchmark.sh` | See above | One-click full evaluation |
| `run_agentic_review.sh` | `<split> <harbor_model> <output_dir>` | Run agentic review on one split |
| `run_revision.sh` | `<review_dir> <harbor_model> [split_name]` | Run revision on rejected patches |
| `compute_da.py` | `--results-dir <dir> --benchmark-split <split>` | Compute Decision Accuracy |
| `compute_rrr.py` | `--results-dir <dir> --benchmark-split <split>` | Compute Resolve Rate after Revision |
| `reverify_revision.py` | `--results-dir <dir> --benchmark-split <split>` | Re-verify revision patches via SWE-bench |
| `aggregate_results.py` | `--results-dirs <dir1> <dir2> <dir3>` | Aggregate results across splits |
| `serve_example_qwen3_coder.sh` | — | Example vLLM serve script |

## Revision Protocol

For computing RRR:
1. Approved patches are kept unchanged
2. Rejected patches are revised by the **original PR generator** for that split
3. Model failures fall back to the original patch

| Split | Revision Model |
|-------|---------------|
| `glm5_500` | GLM-5 |
| `qwen3_coder_30b_500` | Qwen3-Coder-30B-A3B-Instruct |
| `qwen3_30b_instruct2507_500` | Qwen3-30B-A3B-Instruct-2507 |

## Python API

```python
from SWE_Review_Bench.compute_da import compute_da
from SWE_Review_Bench.compute_rrr import compute_rrr
from SWE_Review_Bench.utils import load_review_results, load_benchmark_split

# Load results and benchmark
results = load_review_results("outputs/benchmark/swe_review_8b/glm5_500")
benchmark = load_benchmark_split("glm5_500")

# Compute metrics
da_metrics = compute_da(results, benchmark)
print(f"DA: {da_metrics['DA']}%, CR: {da_metrics['CR']}%")
```
