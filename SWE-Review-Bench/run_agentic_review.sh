#!/bin/bash
# Run agentic review on a single SWE-Review-Bench split.
#
# Usage:
#   bash SWE-Review-Bench/run_agentic_review.sh <split> <harbor_model> <output_dir>
#
# Environment variables (must be set before calling):
#   LLM_API_KEY   - API key for the reviewer model
#   LLM_BASE_URL  - Base URL for the reviewer model
#
# Example:
#   export LLM_API_KEY="dummy-key"
#   export LLM_BASE_URL="http://172.17.0.1:8000/v1"
#   bash SWE-Review-Bench/run_agentic_review.sh glm5_500 hosted_vllm/SWE-Review-8B outputs/benchmark/glm5_500

set -e

SPLIT=${1:?"Usage: $0 <split> <harbor_model> <output_dir>"}
HARBOR_MODEL=${2:?"Usage: $0 <split> <harbor_model> <output_dir>"}
OUTPUT_DIR=${3:?"Usage: $0 <split> <harbor_model> <output_dir>"}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== Agentic Review on SWE-Review-Bench ==="
echo "  Split:  $SPLIT"
echo "  Model:  $HARBOR_MODEL"
echo "  Output: $OUTPUT_DIR"
echo ""

# Generate review tasks from benchmark data
echo "[1/2] Generating review tasks..."
python "$REPO_ROOT/scripts/data_pipeline/generate_review_tasks.py" \
    --split "$SPLIT" \
    --reviewer benchmark \
    --output-dir "$OUTPUT_DIR/tasks"

# Run agentic review via Harbor
echo "[2/2] Running agentic review..."
harbor run \
    -a openhands-sdk \
    -m "$HARBOR_MODEL" \
    --ak max_iterations=100 \
    -p "$OUTPUT_DIR/tasks" \
    -n "${CONCURRENCY:-16}" --timeout-multiplier 3 \
    -o "$OUTPUT_DIR/results"

echo ""
echo "Review complete for split: $SPLIT"
