# Harbor Agent Orchestration

[Harbor](https://github.com/harbor-ai/harbor) is used to orchestrate agentic tasks (review, revision, patchgen) at scale.

## Installation

```bash
pip install harbor-ai openhands-sdk
```

## Task Structure

Each task directory contains:
```
task_name/
├── task.toml          # Task metadata (Docker image, timeout)
├── instruction.md     # Agent instructions
└── environment/       # (optional) Environment setup files
```

## Running Tasks

```bash
export LLM_API_KEY="dummy-key"
export LLM_BASE_URL="http://localhost:8000/v1"

harbor run \
    -a openhands-sdk \
    -m hosted_vllm/SWE-Review-8B \
    --ak max_iterations=100 \
    -p harbor/tasks/review \
    -n 16 --timeout-multiplier 3 \
    -o outputs/review
```

## Key Parameters

| Parameter | Description |
|-----------|-------------|
| `-a openhands-sdk` | Agent type (always use openhands-sdk) |
| `-m hosted_vllm/MODEL` | Model name (must match vLLM served-model-name) |
| `--ak max_iterations=100` | Max agent iterations per task |
| `-p TASK_DIR` | Directory containing task subdirectories |
| `-n 16` | Concurrency (parallel tasks) |
| `--timeout-multiplier 3` | Timeout multiplier |
| `-o OUTPUT_DIR` | Output directory |

## Notes

- Harbor runs inside Docker. Use `172.17.0.1` instead of `localhost` for LLM API access.
- Set `OPENHANDS_LLM_NATIVE_TOOL_CALLING=true` for vLLM with tool-call-parser.
