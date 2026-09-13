#!/bin/bash
# Run revision on rejected patches from a review run.
#
# Usage:
#   bash SWE-Review-Bench/run_revision.sh <review_output_dir> <harbor_model>
#
# Environment variables (must be set before calling):
#   LLM_API_KEY   - API key for the revision model
#   LLM_BASE_URL  - Base URL for the revision model
#
# Example:
#   export LLM_API_KEY="dummy-key"
#   export LLM_BASE_URL="http://172.17.0.1:8020/v1"
#   bash SWE-Review-Bench/run_revision.sh outputs/benchmark/glm5_500 hosted_vllm/Qwen3-Coder-30B-A3B-Instruct

set -e

REVIEW_DIR=${1:?"Usage: $0 <review_output_dir> <harbor_model> [split_name]"}
HARBOR_MODEL=${2:?"Usage: $0 <review_output_dir> <harbor_model> [split_name]"}
SPLIT_NAME=${3:-""}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

REVISION_DIR="${REVIEW_DIR}/revision"

# Auto-detect split name from directory basename if not provided
if [ -z "$SPLIT_NAME" ]; then
    SPLIT_NAME="$(basename "$REVIEW_DIR")"
fi

echo "=== Revision after Review ==="
echo "  Review dir:  $REVIEW_DIR"
echo "  Model:       $HARBOR_MODEL"
echo "  Split:       $SPLIT_NAME"
echo "  Output:      $REVISION_DIR"
echo ""

# Generate revision tasks from review results (only for rejected patches)
# Pass --revision-split to generate patchgen-style test.sh (SWE-bench verifier)
echo "[1/2] Generating revision tasks from rejected patches..."
python "$REPO_ROOT/scripts/data_pipeline/generate_review_tasks.py" \
    --mode revision \
    --review-dir "$REVIEW_DIR/results" \
    --output-dir "$REVISION_DIR/tasks" \
    --revision-split "$SPLIT_NAME"

# Check if any revision tasks were generated
N_TASKS=$(find "$REVISION_DIR/tasks" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)
if [ "$N_TASKS" -eq 0 ]; then
    echo "  No revision tasks generated (all patches approved). Skipping revision."
    echo ""
    echo "Revision complete for: $REVIEW_DIR (0 tasks)"
    exit 0
fi
echo "  $N_TASKS revision tasks to run"

# Run revision via Harbor
echo "[2/2] Running revision..."
harbor run \
    -a openhands-sdk \
    -m "$HARBOR_MODEL" \
    --ak max_iterations=100 \
    -p "$REVISION_DIR/tasks" \
    -n "${CONCURRENCY:-16}" --timeout-multiplier 3 \
    -o "$REVISION_DIR/results"

echo ""
echo "Revision complete for: $REVIEW_DIR"
