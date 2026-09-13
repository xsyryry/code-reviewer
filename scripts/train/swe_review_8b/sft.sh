#!/bin/bash
# Train SWE-Review-8B reviewer via LLaMA-Factory
#
# Prerequisites:
#   - conda env: lf (see main README)
#   - LLaMA-Factory installed in lf env
#   - SFT data downloaded: python scripts/data_pipeline/download_data.py --sft
#
# Usage:
#   conda activate lf
#   bash scripts/train/swe_review_8b/sft.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
CONFIG="$REPO_ROOT/configs/swe_review_8b.yaml"

export WANDB_API_KEY=${WANDB_API_KEY:-""}

echo "Training SWE-Review-8B"
echo "  Config: $CONFIG"
echo ""

# Must run from repo root so dataset_info.json is found (dataset_dir: .)
cd "$REPO_ROOT"
FORCE_TORCHRUN=1 llamafactory-cli train "$CONFIG"
