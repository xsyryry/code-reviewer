#!/bin/bash
# Serve SWE-Review-30B-A3B (Qwen3-30B-A3B fine-tuned reviewer) via vLLM

conda activate vllm

python -m vllm.entrypoints.openai.api_server \
    --model Lego-X/SWE-Review-30B-A3B \
    --served-model-name SWE-Review-30B-A3B \
    --host 0.0.0.0 \
    --port 8000 \
    --tensor-parallel-size 8 \
    --gpu-memory-utilization 0.9 \
    --max-model-len 131072 \
    --max-num-seqs 24 \
    --enable-auto-tool-choice \
    --tool-call-parser hermes \
    --chat-template-content-format string \
    --api-key "dummy-key"
