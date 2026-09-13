#!/bin/bash
# Serve SWE-Review-Mixed-6K-8B (mixed-trained reviewer+generator) via vLLM
#
# This checkpoint is not published on HuggingFace — train it locally first:
#   bash scripts/train/mixed_training/sft.sh
# Then point CKPT at the resulting checkpoint directory.

conda activate vllm

CKPT=${CKPT:-saves/mixed_6k_8b/full/sft}

python -m vllm.entrypoints.openai.api_server \
    --model "$CKPT" \
    --served-model-name SWE-Review-Mixed-6K-8B \
    --host 0.0.0.0 \
    --port 8000 \
    --tensor-parallel-size 4 \
    --gpu-memory-utilization 0.9 \
    --max-model-len 131072 \
    --max-num-seqs 24 \
    --enable-auto-tool-choice \
    --tool-call-parser hermes \
    --chat-template-content-format string \
    --api-key "dummy-key"
