#!/bin/bash
# Run single-turn review baseline on SWE-Review-Bench.
#
# Usage:
#   bash SWE-Review-Bench/run_single_turn_review.sh <split> <setting> \
#       --model hosted_vllm/SWE-Review-8B \
#       --api-url http://172.17.0.1:8000/v1
#
# Settings: diff_only, diff_with_context

set -e

SPLIT=${1:?"Usage: $0 <split> <setting> [--model MODEL] [--api-url URL] [--api-key KEY]"}
SETTING=${2:?"Settings: diff_only, diff_with_context"}
shift 2

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python "$SCRIPT_DIR/run_single_turn_review.py" \
    --split "$SPLIT" \
    --setting "$SETTING" \
    --output "outputs/benchmark/${SPLIT}_single_turn_${SETTING}" \
    "$@"
