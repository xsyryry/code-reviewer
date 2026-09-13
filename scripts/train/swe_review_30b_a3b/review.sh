#!/bin/bash
# Run SWE-Review-30B-A3B agentic review on a set of tasks
conda activate harbor
#
# Usage: bash scripts/swe_review_30b_a3b/review.sh <task_dir> <output_dir> [port]

TASK_DIR=${1:?"Usage: $0 <task_dir> <output_dir> [port]"}
OUTPUT_DIR=${2:?"Usage: $0 <task_dir> <output_dir> [port]"}
PORT=${3:-8000}

export LLM_API_KEY="dummy-key"
export LLM_BASE_URL="http://172.17.0.1:${PORT}/v1"

harbor run \
    -a openhands-sdk \
    -m "hosted_vllm/SWE-Review-30B-A3B" \
    --ak max_iterations=100 \
    -p "$TASK_DIR" \
    -n 16 --timeout-multiplier 3 \
    -o "$OUTPUT_DIR"
