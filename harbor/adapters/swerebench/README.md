## SWE-rebench → Harbor Adapter

## Overview

[SWE-rebench](https://huggingface.co/datasets/nebius/SWE-rebench) is a large-scale benchmark for evaluating LLM-based software engineering agents, extending the [SWE-bench](https://www.swebench.com/) methodology to 3,400+ Python repositories with over 21,000 real-world issue–pull request pairs.

- **Task types**: Bug fixes, feature implementations, and code changes derived from real GitHub issues.
- **Languages**: Python only (all 3,400+ repos are Python).
- **Dataset size**: 21,336 tasks (test split) / 6,542 tasks (filtered split — curated for the leaderboard).
- **Provenance**: [SWE-rebench paper (arXiv:2505.20411)](https://arxiv.org/abs/2505.20411), dataset on [HuggingFace](https://huggingface.co/datasets/nebius/SWE-rebench). Licensed under CC-BY-4.0, though individual repo licenses vary.
- **Key difference from SWE-bench Verified**: SWE-rebench uses per-instance `install_config` fields (instead of a hardcoded repo→specs mapping), covers 3,400+ arbitrary repos (vs. ~18 fixed repos), and provides pre-built Docker images on [Docker Hub](https://hub.docker.com/repositories/swerebench).
- **Evaluation**: Uses the [SWE-bench-fork](https://github.com/SWE-rebench/SWE-bench-fork) which extends the original SWE-bench evaluation harness with `install_config` support.

## What is SWE-rebench?

SWE-rebench is a continuously updated benchmark constructed using a fully automated pipeline that extracts real-world interactive software engineering tasks from GitHub at scale. Each task consists of a GitHub issue paired with a pull request that resolves it, validated through automated environment setup and test execution. The benchmark measures whether an agent can generate a patch that passes the same tests the human-authored PR passes. Scoring is binary: resolved (1) or unresolved (0).

## Adapter Features

- Loads tasks from the `nebius/SWE-rebench` HuggingFace dataset (supports `test` and `filtered` splits)
- Uses pre-built Docker images from Docker Hub (`swerebench/` namespace) via the `docker_image` field in each record
- Per-instance `install_config` drives test commands, install commands, and log parser selection
- Grading via the [SWE-bench-fork](https://github.com/SWE-rebench/SWE-bench-fork) which handles `install_config["log_parser"]` for parsing test output
- Oracle solutions (gold patches) from the dataset
- Configurable timeout, split selection, and instance filtering

## Generated Task Structure

```
swerebench/
├── {instance_id}/
│   ├── task.toml                 # Task configuration and metadata
│   ├── instruction.md            # Task instructions for the agent
│   ├── environment/              # Container definition
│   │   └── Dockerfile
│   ├── solution/                 # Oracle/golden solution
│   │   └── solve.sh
│   └── tests/                    # Test assets and scripts
│       ├── test.sh               # Test execution + grading script
│       └── config.json           # Full instance record for grading
```

Adapter code directory:

```
harbor/adapters/swerebench/
├── README.md
├── adapter.py
├── run_adapter.py
├── utils.py
├── pyproject.toml
├── adapter_metadata.json
├── parity_experiment.json
├── swerebench.yaml
└── template/
    ├── task.toml
    ├── instruction.md
    ├── Dockerfile
    ├── solve.sh
    └── test.sh
```

## Run Evaluation / Harness in Harbor

### Running with Datasets Registry

After the dataset is registered:

```bash
# Use oracle agent (reference solution)
uv run harbor jobs start -d swerebench

# Use your specified agent and model
uv run harbor jobs start -d swerebench -a <agent_name> -m "<model_name>"
```

### Using Job Configurations

```bash
# Run a job with the adapter configuration
uv run harbor jobs start -c adapters/swerebench/swerebench.yaml -a <agent_name> -m "<model_name>"

# Or run with locally prepared dataset path
uv run harbor jobs start -p datasets/swerebench -a <agent_name> -m "<model_name>"

# Resume a previously started job
uv run harbor jobs resume -p /path/to/jobs/directory
```

### Running Individual Trials

```bash
# Run a single trial with oracle (pre-written solution)
uv run harbor trials start -p datasets/swerebench/<instance_id>

# Run a single trial with a specific agent and model
uv run harbor trials start -p datasets/swerebench/<instance_id> -a <agent_name> -m "<model_name>"
```

## Usage: Create Task Directories

```bash
# From adapter directory
cd adapters/swerebench

# Install dependencies
uv sync

# Generate tasks from the filtered split (6,542 curated instances, recommended)
uv run run_adapter.py --split filtered

# Generate tasks from the full test split (21,336 instances)
uv run run_adapter.py --split test

# Generate a single instance
uv run run_adapter.py --instance-id "owner__repo-123" --split filtered

# Generate with a limit
uv run run_adapter.py --split filtered --limit 100

# Custom output directory
uv run run_adapter.py --output-dir /path/to/harbor-datasets/datasets/swerebench --split filtered
```

Tasks are written to `datasets/swerebench/` (relative to harbor root) with one directory per task. Each task follows the structure shown in "Generated Task Structure" above.

## Comparison with Original Benchmark (Parity)

Parity experiments are pending.

| Agent | Model | Metric | Number of Trials | Dataset Size | Original Benchmark Performance | Harbor Adapter Performance |
|-------|-------|--------|------------------|--------------|--------------------------------|----------------------------|
| TBD | TBD | Resolved Rate | TBD | TBD | TBD | TBD |

## Notes & Caveats

- **Docker images**: SWE-rebench provides ~7,500 pre-built Docker images on Docker Hub. Tasks without a pre-built image will fall back to building via the SWE-bench-fork's `make_test_spec`, which may be slow.
- **Large dataset**: The full test split has 21,336 instances. The filtered split (6,542) is recommended for evaluation.
- **`install_config` dependency**: The grading parser in `test.sh` uses `swebench @ git+https://github.com/SWE-rebench/SWE-bench-fork.git` (not the vanilla `swebench` package) to support `install_config`-based log parser selection.
- **License**: The dataset is CC-BY-4.0, but each task inherits the license of its source repository (provided in the `license_name` field).

## Installation / Prerequisites

- Docker installed and running
- Harbor installed and working (see main repository README)
- Python environment with dependencies:
  ```bash
  cd adapters/swerebench
  uv sync
  ```
- API keys for agents/models (export as environment variables)
- Sufficient disk space for Docker images (~7,500 pre-built images)

## Troubleshooting

- **Docker pull failures**: Some pre-built images may not be available. Check `https://hub.docker.com/repositories/swerebench` for available images.
- **Grading failures**: Ensure `config.json` in each task's `tests/` directory contains the full instance record including `install_config`, `FAIL_TO_PASS`, and `PASS_TO_PASS` fields.
- **Timeout issues**: Increase `--timeout` (default: 3000s) for complex instances.

## Citation

```bibtex
@misc{badertdinov2025swerebenchautomatedpipelinetask,
      title={SWE-rebench: An Automated Pipeline for Task Collection and Decontaminated Evaluation of Software Engineering Agents},
      author={Ibragim Badertdinov and Alexander Golubev and Maksim Nekrashevich and Anton Shevtsov and Simon Karasik and Andrei Andriushchenko and Maria Trofimova and Daria Litvintseva and Boris Yangel},
      year={2025},
      eprint={2505.20411},
      archivePrefix={arXiv},
      primaryClass={cs.SE},
      url={https://arxiv.org/abs/2505.20411}
}
```

## Authors & Contributions

Adapter built for Harbor. For issues or contributions, please file a PR to the [Harbor repository](https://github.com/laude-institute/harbor).
