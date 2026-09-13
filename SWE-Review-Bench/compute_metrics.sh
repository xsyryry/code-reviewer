#!/bin/bash
# Compute DA and RRR metrics from review/revision outputs
#
# Usage: bash SWE-Review-Bench/compute_metrics.sh <output_dir> [benchmark_split]

set -e

OUTPUT_DIR=${1:?"Usage: $0 <output_dir> [benchmark_split]"}
SPLIT=${2:-""}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Computing Metrics ==="
echo "  Output dir: $OUTPUT_DIR"

# Decision Accuracy
SPLIT_ARG=""
if [ -n "$SPLIT" ]; then
    SPLIT_ARG="--benchmark-split $SPLIT"
fi

python "$SCRIPT_DIR/compute_da.py" \
    --results-dir "$OUTPUT_DIR" \
    $SPLIT_ARG \
    --output "$OUTPUT_DIR/da_metrics.json"

# Resolve Rate after Revision (if revision results exist)
if [ -d "$OUTPUT_DIR/revision" ]; then
    python "$SCRIPT_DIR/compute_rrr.py" \
        --results-dir "$OUTPUT_DIR" \
        $SPLIT_ARG \
        --output "$OUTPUT_DIR/rrr_metrics.json"
fi
