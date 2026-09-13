#!/bin/bash
# Serve Qwen3-Coder-30B-A3B as a reviewer via vLLM
# This is an example for evaluating a custom reviewer on SWE-Review-Bench

conda activate vllm

python -m vllm.entrypoints.openai.api_server \
    --model Qwen/Qwen3-Coder-30B-A3B-Instruct \
    --served-model-name Qwen3-Coder-30B-A3B-Instruct \
    --host 0.0.0.0 \
    --port 8000 \
    --tensor-parallel-size 8 \
    --gpu-memory-utilization 0.9 \
    --max-model-len 131072 \
    --max-num-seqs 24 \
    --enable-auto-tool-choice \
    --tool-call-parser qwen3_coder \
    --chat-template-content-format string \
    --api-key "dummy-key"
