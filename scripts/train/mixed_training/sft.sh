#!/bin/bash
# Train SWE-Review-Mixed-6K-8B (review + patchgen) via LLaMA-Factory
#
# Prerequisites:
#   - conda env: lf (see main README)
#   - SFT data downloaded: python scripts/data_pipeline/download_data.py --sft
#
# Usage:
#   conda activate lf
#   bash scripts/train/mixed_training/sft.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
CONFIG="$REPO_ROOT/configs/mixed_6k_8b.yaml"

export WANDB_API_KEY=${WANDB_API_KEY:-""}

echo "Training SWE-Review-Mixed-6K-8B"
echo "  Config: $CONFIG"
echo ""

# Must run from repo root so dataset_info.json is found (dataset_dir: .)
cd "$REPO_ROOT"
FORCE_TORCHRUN=1 llamafactory-cli train "$CONFIG"
