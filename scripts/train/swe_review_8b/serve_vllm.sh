#!/bin/bash
# Serve SWE-Review-8B (Qwen3-8B fine-tuned reviewer) via vLLM

conda activate vllm

python -m vllm.entrypoints.openai.api_server \
    --model Lego-X/SWE-Review-8B \
    --served-model-name SWE-Review-8B \
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
