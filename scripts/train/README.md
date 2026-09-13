# Training

Train reviewer models using LLaMA-Factory with our pre-collected SFT data.

## Prerequisites

- **Conda env**: `lf` (see main README for installation)
- **GPU**: 8x H800/A100 for 30B model, 4x for 8B model
- **LLaMA-Factory**: Installed in the `lf` environment
- **Data**: Downloaded via `python scripts/data_pipeline/download_data.py --sft`

## Quick Start

```bash
conda activate lf

# Download SFT data (if not already done)
python scripts/data_pipeline/download_data.py --sft

# Train SWE-Review-8B
bash scripts/train/swe_review_8b/sft.sh
```

## Available Training Configs

| Config | Model | Description |
|--------|-------|-------------|
| `swe_review_8b/sft.sh` | Qwen3-8B | Review-only SFT (8,914 trajectories) |
| `swe_review_30b_a3b/sft.sh` | Qwen3-30B-A3B | Review-only SFT |
| `mixed_training/sft.sh` | Qwen3-8B | Mixed training (review + patchgen) |

## Training Details

All configs use:
- **Framework**: LLaMA-Factory with DeepSpeed ZeRO-3
- **Context length**: 131,072 tokens (YaRN rope scaling)
- **Epochs**: 4
- **Learning rate**: 1e-4 with cosine scheduler
- **Optimizations**: Liger kernel + Flash Attention v2

Config files are in `configs/`:
- `configs/swe_review_8b.yaml`
- `configs/swe_review_30b_a3b.yaml`
- `configs/mixed_6k_8b.yaml`
- `configs/deepspeed_z3.json`

## Serving Trained Models

After training, serve your model with vLLM:

```bash
conda activate vllm

# SWE-Review-8B
bash scripts/train/swe_review_8b/serve_vllm.sh

# SWE-Review-30B-A3B
bash scripts/train/swe_review_30b_a3b/serve_vllm.sh
```

Default serving config: TP=4 (8B) or TP=8 (30B), port 8000, tool-call-parser=hermes.

## Custom Training

To train with your own data:

1. Prepare data in ShareGPT format (see `prepare_review_sft_data.py`)
2. Copy and modify a config YAML (e.g., `configs/swe_review_8b.yaml`)
3. Update `dataset` field to point to your data
4. Run: `cd LLaMA-Factory && llamafactory-cli train ../configs/your_config.yaml`

## Notes

- **Transformers version**: Qwen3 models require `transformers==4.55.0` (included in `lf` env setup)
- **LLaMA-Factory data format**: Place your JSON in `LLaMA-Factory/data/` and register in `dataset_info.json`
- **Mixed training**: Combines review trajectories with patchgen (issue-resolution) data, enabling self-contained review-revise loops
