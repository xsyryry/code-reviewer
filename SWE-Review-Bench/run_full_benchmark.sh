#!/bin/bash
# One-click benchmark evaluation on all 3 SWE-Review-Bench splits.
#
# Two modes:
#
# Mode 1: API (reviewer is already served externally)
#   bash SWE-Review-Bench/run_full_benchmark.sh --mode api \
#       --reviewer-model hosted_vllm/SWE-Review-8B \
#       --api-key dummy-key \
#       --api-url http://172.17.0.1:8000/v1 \
#       --output outputs/benchmark/swe_review_8b
#
# Mode 2: Local (script manages vLLM deployment for reviewer and revision models)
#   bash SWE-Review-Bench/run_full_benchmark.sh --mode local \
#       --reviewer-model Lego-X/SWE-Review-8B \
#       --reviewer-parser hermes \
#       --tp 4 \
#       --gpu-ids 0,1,2,3 \
#       --port 8000 \
#       --output outputs/benchmark/swe_review_8b
#
# In both modes, revision is handled automatically:
# - If --revision-url is set, revision uses the external API (no local vLLM)
# - Otherwise, the script deploys each split's revision model via vLLM
#   (auto-detects 'vllm' conda env, or use --vllm-python)
# - After revision completes for one split, it kills the vLLM and starts the next
#
# Revision models per split:
#   glm5_500 → GLM-5 (or your GLM-5 endpoint)
#   qwen3_coder_30b_500 → Qwen/Qwen3-Coder-30B-A3B-Instruct
#   qwen3_30b_instruct2507_500 → Qwen/Qwen3-30B-A3B-Instruct-2507

set -e

# ============================================================
# Defaults
# ============================================================
MODE=""
REVIEWER_MODEL=""
API_KEY="dummy-key"
API_URL=""
REVIEWER_PARSER="hermes"
TP=4
GPU_IDS=""
PORT=8000
REVISION_PORT=8020
REVISION_TP=4
REVISION_GPU_IDS=""
REVISION_URL=""          # If set, use this URL for revision (skip local vLLM)
REVISION_API_KEY=""      # API key for revision URL (defaults to API_KEY)
OUTPUT_DIR="outputs/benchmark"
CONCURRENCY=16
SKIP_REVISION=false
VLLM_PYTHON=""           # Path to vLLM-capable python (auto-detected)

# Revision model configs per split
declare -A REVISION_HF_MODELS
REVISION_HF_MODELS[glm5_500]="GLM-5"
REVISION_HF_MODELS[qwen3_coder_30b_500]="Qwen/Qwen3-Coder-30B-A3B-Instruct"
REVISION_HF_MODELS[qwen3_30b_instruct2507_500]="Qwen/Qwen3-30B-A3B-Instruct-2507"

declare -A REVISION_PARSERS
REVISION_PARSERS[glm5_500]="hermes"
REVISION_PARSERS[qwen3_coder_30b_500]="qwen3_coder"
REVISION_PARSERS[qwen3_30b_instruct2507_500]="hermes"

declare -A REVISION_HARBOR_MODELS
REVISION_HARBOR_MODELS[glm5_500]="hosted_vllm/GLM-5"
REVISION_HARBOR_MODELS[qwen3_coder_30b_500]="hosted_vllm/Qwen3-Coder-30B-A3B-Instruct"
REVISION_HARBOR_MODELS[qwen3_30b_instruct2507_500]="hosted_vllm/Qwen3-30B-A3B-Instruct-2507"

# ============================================================
# Parse arguments
# ============================================================
while [[ $# -gt 0 ]]; do
    case $1 in
        --mode) MODE="$2"; shift 2 ;;
        --reviewer-model) REVIEWER_MODEL="$2"; shift 2 ;;
        --api-key) API_KEY="$2"; shift 2 ;;
        --api-url) API_URL="$2"; shift 2 ;;
        --reviewer-parser) REVIEWER_PARSER="$2"; shift 2 ;;
        --tp) TP="$2"; shift 2 ;;
        --gpu-ids) GPU_IDS="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --revision-port) REVISION_PORT="$2"; shift 2 ;;
        --revision-tp) REVISION_TP="$2"; shift 2 ;;
        --revision-gpu-ids) REVISION_GPU_IDS="$2"; shift 2 ;;
        --revision-url) REVISION_URL="$2"; shift 2 ;;
        --revision-api-key) REVISION_API_KEY="$2"; shift 2 ;;
        --vllm-python) VLLM_PYTHON="$2"; shift 2 ;;
        --output) OUTPUT_DIR="$2"; shift 2 ;;
        --concurrency|-n) CONCURRENCY="$2"; shift 2 ;;
        --skip-revision) SKIP_REVISION=true; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ============================================================
# Validation
# ============================================================
if [ -z "$MODE" ] || [ -z "$REVIEWER_MODEL" ]; then
    cat << 'EOF'
Usage:
  Mode 1 - API (reviewer already served):
    bash SWE-Review-Bench/run_full_benchmark.sh --mode api \
        --reviewer-model hosted_vllm/SWE-Review-8B \
        --api-key dummy-key \
        --api-url http://172.17.0.1:8000/v1 \
        --output outputs/benchmark/swe_review_8b

  Mode 2 - Local (script deploys vLLM):
    bash SWE-Review-Bench/run_full_benchmark.sh --mode local \
        --reviewer-model Lego-X/SWE-Review-8B \
        --reviewer-parser hermes \
        --tp 4 --port 8000 \
        --output outputs/benchmark/swe_review_8b

Options:
  --mode              api or local (required)
  --reviewer-model    Model name (required)
  --api-key           API key (default: dummy-key)
  --api-url           API URL (required for api mode)
  --reviewer-parser   Tool call parser for local mode (default: hermes)
  --tp                Tensor parallel size (default: 4)
  --gpu-ids           GPU IDs for reviewer (e.g., 0,1,2,3)
  --port              vLLM port for reviewer (default: 8000)
  --revision-port     vLLM port for revision models (default: 8020)
  --revision-tp       TP for revision models (default: 4)
  --revision-gpu-ids  GPU IDs for revision (e.g., 4,5,6,7)
  --revision-url      External API URL for revision (skip local vLLM deploy)
  --revision-api-key  API key for revision URL (defaults to --api-key)
  --vllm-python       Path to python with vLLM installed (auto-detected)
  --output            Output directory
  --concurrency|-n    Harbor concurrency (default: 16)
  --skip-revision     Skip revision, only compute DA
EOF
    exit 1
fi

if [ "$MODE" = "api" ] && [ -z "$API_URL" ]; then
    echo "Error: --api-url is required for api mode"
    exit 1
fi

SPLITS=("glm5_500" "qwen3_coder_30b_500" "qwen3_30b_instruct2507_500")

# ============================================================
# Helper functions
# ============================================================
start_vllm() {
    local model=$1
    local port=$2
    local tp=$3
    local parser=$4
    local gpus=$5
    local served_name=$(basename "$model")

    echo "  Starting vLLM: model=$model port=$port tp=$tp parser=$parser"

    # Auto-detect vLLM python: use --vllm-python, or check 'vllm' conda env,
    # or fall back to current python
    local vllm_py="${VLLM_PYTHON}"
    if [ -z "$vllm_py" ]; then
        # Try to find vllm conda env python
        local conda_base
        conda_base=$(conda info --base 2>/dev/null) || true
        if [ -n "$conda_base" ] && [ -x "$conda_base/envs/vllm/bin/python" ]; then
            vllm_py="$conda_base/envs/vllm/bin/python"
        else
            vllm_py="python"
        fi
    fi
    echo "  Using python: $vllm_py"

    local gpu_env=""
    if [ -n "$gpus" ]; then
        gpu_env="CUDA_VISIBLE_DEVICES=$gpus"
    fi

    eval $gpu_env "$vllm_py" -m vllm.entrypoints.openai.api_server \
        --model "$model" \
        --served-model-name "$served_name" \
        --host 0.0.0.0 --port "$port" \
        --tensor-parallel-size "$tp" \
        --gpu-memory-utilization 0.9 \
        --max-model-len 131072 \
        --max-num-seqs 24 \
        --enable-auto-tool-choice \
        --tool-call-parser "$parser" \
        --chat-template-content-format string \
        --api-key "$API_KEY" &

    VLLM_PID=$!
    echo "  vLLM PID: $VLLM_PID"

    # Wait for vLLM to be ready
    echo "  Waiting for vLLM to start..."
    for i in $(seq 1 120); do
        if curl -s "http://localhost:$port/health" > /dev/null 2>&1; then
            echo "  vLLM ready on port $port"
            return 0
        fi
        sleep 5
    done
    echo "  ERROR: vLLM failed to start within 600s"
    kill $VLLM_PID 2>/dev/null
    exit 1
}

stop_vllm() {
    local pid=$1
    local port=${2:-$REVISION_PORT}
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        echo "  Stopping vLLM (PID $pid)..."
        kill "$pid"
        # Wait up to 30s for graceful shutdown
        for i in $(seq 1 30); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 1
        done
        # Force kill if still alive
        if kill -0 "$pid" 2>/dev/null; then
            echo "  Force killing vLLM (PID $pid)..."
            kill -9 "$pid" 2>/dev/null || true
        fi
        wait "$pid" 2>/dev/null || true
        # Wait for port to be released
        echo "  Waiting for port $port to be released..."
        for i in $(seq 1 30); do
            if ! curl -s "http://localhost:$port/health" > /dev/null 2>&1; then
                echo "  Port $port released"
                return 0
            fi
            sleep 1
        done
        echo "  WARNING: Port $port may still be in use"
    fi
}

# ============================================================
# Main
# ============================================================
echo "============================================================"
echo " SWE-Review-Bench Full Evaluation"
echo "============================================================"
echo "  Mode:        $MODE"
echo "  Reviewer:    $REVIEWER_MODEL"
if [ "$MODE" = "api" ]; then
    echo "  API URL:     $API_URL"
else
    echo "  Port:        $PORT (tp=$TP, parser=$REVIEWER_PARSER)"
fi
echo "  Revision:    ${SKIP_REVISION:-false}"
echo "  Output:      $OUTPUT_DIR"
echo "  Concurrency: $CONCURRENCY"
export CONCURRENCY
echo "============================================================"
echo ""

# Step 0: Download benchmark data
if [ ! -d "data/swebench/benchmark/splits" ]; then
    echo "[Step 0] Downloading benchmark data..."
    python scripts/data_pipeline/download_data.py --benchmark
    echo ""
fi

# ============================================================
# Step 1: Deploy reviewer (local mode only)
# ============================================================
REVIEWER_VLLM_PID=""
if [ "$MODE" = "local" ]; then
    echo "[Step 1] Deploying reviewer via vLLM..."
    start_vllm "$REVIEWER_MODEL" "$PORT" "$TP" "$REVIEWER_PARSER" "$GPU_IDS"
    REVIEWER_VLLM_PID=$VLLM_PID
    REVIEWER_URL="http://172.17.0.1:${PORT}/v1"
    HARBOR_REVIEWER_MODEL="hosted_vllm/$(basename $REVIEWER_MODEL)"
else
    REVIEWER_URL="$API_URL"
    HARBOR_REVIEWER_MODEL="$REVIEWER_MODEL"
fi

# ============================================================
# Step 2: Run agentic review on all splits
# ============================================================
echo ""
echo "============================================================"
echo " Step 2: Agentic Review (all splits)"
echo "============================================================"
for split in "${SPLITS[@]}"; do
    split_output="$OUTPUT_DIR/${split}"
    echo ""
    echo "[Review] Split: $split"

    export LLM_API_KEY="$API_KEY"
    export LLM_BASE_URL="$REVIEWER_URL"

    bash SWE-Review-Bench/run_agentic_review.sh "$split" "$HARBOR_REVIEWER_MODEL" "$split_output"
done

# Stop reviewer vLLM (local mode) to free GPU for revision
if [ -n "$REVIEWER_VLLM_PID" ]; then
    echo ""
    echo "  Stopping reviewer vLLM to free GPU for revision..."
    stop_vllm "$REVIEWER_VLLM_PID"
fi

# ============================================================
# Step 3: Compute DA
# ============================================================
echo ""
echo "============================================================"
echo " Step 3: Decision Accuracy"
echo "============================================================"
for split in "${SPLITS[@]}"; do
    split_output="$OUTPUT_DIR/${split}"
    echo ""
    echo "[DA] Split: $split"
    python SWE-Review-Bench/compute_da.py \
        --results-dir "$split_output" \
        --benchmark-split "$split" \
        --output "$split_output/da_metrics.json"
done

# ============================================================
# Step 4: Revision (per-split model switching)
# ============================================================
if [ "$SKIP_REVISION" = false ]; then
    echo ""
    echo "============================================================"
    echo " Step 4: Revision (switching model per split)"
    echo "============================================================"

    for split in "${SPLITS[@]}"; do
        split_output="$OUTPUT_DIR/${split}"
        revision_hf_model="${REVISION_HF_MODELS[$split]}"
        revision_parser="${REVISION_PARSERS[$split]}"
        revision_harbor_model="${REVISION_HARBOR_MODELS[$split]}"

        echo ""
        echo "[Revision] Split: $split"
        echo "  Model: $revision_hf_model (parser: $revision_parser)"

        REVISION_VLLM_PID=""
        if [ -n "$REVISION_URL" ]; then
            # Check if this split's model is available locally (HF path with /)
            # If so, prefer local deployment over API
            if [[ "$revision_hf_model" == *"/"* ]] && [ -z "$FORCE_REVISION_API" ]; then
                echo "  Deploying locally (model available on HuggingFace)"
                start_vllm "$revision_hf_model" "$REVISION_PORT" "$REVISION_TP" "$revision_parser" "$REVISION_GPU_IDS"
                REVISION_VLLM_PID=$VLLM_PID
                export LLM_API_KEY="$API_KEY"
                export LLM_BASE_URL="http://172.17.0.1:${REVISION_PORT}/v1"
            else
                # Use external API (model not on HuggingFace, e.g., GLM-5)
                echo "  Using external revision URL: $REVISION_URL (model not available locally)"
                export LLM_API_KEY="${REVISION_API_KEY:-$API_KEY}"
                export LLM_BASE_URL="$REVISION_URL"
                # Map split revision models to API model names
                declare -A REVISION_API_MODELS
                REVISION_API_MODELS[glm5_500]="openai/glm-5"
                REVISION_API_MODELS[qwen3_coder_30b_500]="openai/qwen3-coder-30b-a3b-instruct"
                REVISION_API_MODELS[qwen3_30b_instruct2507_500]="openai/qwen3-30b-a3b-instruct-2507"
                revision_harbor_model="${REVISION_API_MODELS[$split]}"
            fi
        else
            # No revision URL — deploy all models locally (will fail for GLM-5)
            start_vllm "$revision_hf_model" "$REVISION_PORT" "$REVISION_TP" "$revision_parser" "$REVISION_GPU_IDS"
            REVISION_VLLM_PID=$VLLM_PID
            export LLM_API_KEY="$API_KEY"
            export LLM_BASE_URL="http://172.17.0.1:${REVISION_PORT}/v1"
        fi

        bash SWE-Review-Bench/run_revision.sh "$split_output" "$revision_harbor_model" "$split"

        # Stop revision model (if deployed locally)
        if [ -n "$REVISION_VLLM_PID" ]; then
            stop_vllm "$REVISION_VLLM_PID"
        fi
    done

    # ============================================================
    # Step 5: Compute RRR
    # ============================================================
    echo ""
    echo "============================================================"
    echo " Step 5: Resolve Rate after Revision (RRR)"
    echo "============================================================"
    for split in "${SPLITS[@]}"; do
        split_output="$OUTPUT_DIR/${split}"
        echo ""
        echo "[RRR] Split: $split"
        python SWE-Review-Bench/compute_rrr.py \
            --results-dir "$split_output" \
            --benchmark-split "$split" \
            --output "$split_output/rrr_metrics.json"
    done
fi

# ============================================================
# Step 6: Aggregate results
# ============================================================
echo ""
echo "============================================================"
echo " Final Results"
echo "============================================================"
python SWE-Review-Bench/aggregate_results.py \
    --results-dirs $OUTPUT_DIR/glm5_500 $OUTPUT_DIR/qwen3_coder_30b_500 $OUTPUT_DIR/qwen3_30b_instruct2507_500 \
    --output "$OUTPUT_DIR/results_summary.json"

echo ""
echo "============================================================"
echo " Done! Results saved to: $OUTPUT_DIR/results_summary.json"
echo "============================================================"
