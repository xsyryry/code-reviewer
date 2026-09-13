#!/bin/bash
# Evaluate review results using SWE-bench harness
#
# Usage: bash scripts/swe_review_8b/eval.sh <output_dir>

OUTPUT_DIR=${1:?"Usage: $0 <output_dir>"}

conda activate harbor

# Extract patches and convert to SWE-bench format
python evaluation/extract_patches.py --results-dir "$OUTPUT_DIR" --output "${OUTPUT_DIR}/predictions.jsonl"

# Run SWE-bench evaluation
python -m swebench.harness.run_evaluation \
    --max_workers 10 \
    --dataset_name "Lego-X/SWE-Review-Bench" \
    --report_dir "${OUTPUT_DIR}/swebench_results" \
    --cache_level instance \
    --predictions_path "${OUTPUT_DIR}/predictions.jsonl" \
    --run_id swe_review \
    --timeout 500

echo "Evaluation complete. Results in ${OUTPUT_DIR}/swebench_results/"
