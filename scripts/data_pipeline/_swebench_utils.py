"""
SWE-bench infrastructure utilities for task generation.

Provides functions for:
- Loading SWE-bench Verified dataset
- Generating test scripts, solve scripts, and config JSON
- Computing Docker image names
"""

import json
import re
import shlex
from pathlib import Path
from textwrap import dedent


def make_task_id(instance_id: str) -> str:
    """Convert instance_id to a valid task directory name."""
    return instance_id.strip()


def generate_solve_sh(golden_patch: str) -> str:
    """Generate solve.sh that applies the golden patch."""
    return f"""\
#!/bin/bash
set -euo pipefail

cat > /testbed/solution_patch.diff << '__SOLUTION__'
{golden_patch}
__SOLUTION__

cd /testbed
patch --fuzz=5 -p1 -i /testbed/solution_patch.diff
"""


def prepare_config_json(raw_item: dict) -> dict:
    """Prepare config.json for SWE-bench test harness.

    Serializes FAIL_TO_PASS/PASS_TO_PASS to JSON strings if they are lists,
    because make_test_spec() internally calls json.loads().
    """
    config = dict(raw_item)
    for key in ("FAIL_TO_PASS", "PASS_TO_PASS"):
        val = config.get(key)
        if isinstance(val, list):
            config[key] = json.dumps(val)
    return config


def get_test_commands(
    test_patch: str,
    repo: str,
    version: str,
    base_commit: str,
    install_config: dict | None = None,
) -> str:
    """Generate SWE-bench test execution script (3-level fallback for config)."""
    from swebench.harness.constants import MAP_REPO_VERSION_TO_SPECS
    from swebench.harness.test_spec.python import get_test_directives

    ic = install_config or {}
    map_entry = MAP_REPO_VERSION_TO_SPECS.get(repo, {}).get(version, {})

    conda_env = "testbed"
    repo_directory = "/testbed"

    if "test_cmd" in ic:
        test_command = ic["test_cmd"]
    elif "test_cmd" in map_entry:
        test_command = map_entry["test_cmd"]
    else:
        test_command = "pytest"

    eval_commands = ic.get("eval_commands") or map_entry.get("eval_commands") or []
    if isinstance(eval_commands, str):
        eval_commands = [eval_commands]

    install_cmd = ic.get("install") or map_entry.get("install") or ""
    if repo == "scikit-learn/scikit-learn":
        install_cmd = ""

    test_patch_files = re.findall(r"--- a/(.*)", test_patch)
    if test_patch:
        test_files = get_test_directives({"repo": repo, "test_patch": test_patch})
    else:
        test_files = []

    newline = "\n"
    eval_script = dedent(
        f"""#!/bin/bash
        set -uo pipefail -x

        cd {repo_directory}
        set +x
        source /opt/miniconda3/bin/activate
        conda activate {conda_env}
        set -x

        # Repo-specific eval setup commands
        {newline.join(eval_commands)}

        # Re-run install command (if any)
        {install_cmd}

        # Reset test files to base commit state
        {f'git checkout {base_commit} {" ".join(test_patch_files)}' if test_patch_files else '# No test patch files to reset'}

        # Apply the test patch
        echo {shlex.quote(test_patch)} > /tmp/test_patch.diff
        git apply --check /tmp/test_patch.diff
        git apply /tmp/test_patch.diff

        # Record terminal output in LOG_FILE
        LOG_FILE=$(mktemp)
        export LOG_FILE
        exec 3>&1 4>&2
        exec > >(tee "$LOG_FILE") 2>&1

        set +x
        {test_command} {" ".join(test_files)} || true
        exec 1>&3 2>&4 # stop record

        # Reset tests back to base commit
        {f'git checkout {base_commit} {" ".join(test_patch_files)}' if test_patch_files else '# No test patch files to reset'}
    """
    )

    return eval_script


def load_swebench_verified_data(cache_dir: str) -> tuple[dict, dict]:
    """Load SWE-bench Verified dataset from HuggingFace.

    Returns:
        (lookup, raw_items) where:
        - lookup: {instance_id: {repo, version, base_commit, test_patch, patch,
                                  problem_statement, image_name, ...}}
        - raw_items: {instance_id: raw_dataset_item}
    """
    from datasets import load_dataset
    from swebench.harness.test_spec.test_spec import make_test_spec

    ds = load_dataset(
        "princeton-nlp/SWE-bench_Verified",
        split="test",
        cache_dir=cache_dir,
    )

    lookup = {}
    raw_items = {}

    for item in ds:
        instance_id = item["instance_id"]
        raw_items[instance_id] = dict(item)

        # Compute Docker image name
        try:
            test_spec = make_test_spec(item, namespace="swebench")
            image_name = f"{test_spec.image_key}:latest"
        except Exception:
            image_name = ""

        lookup[instance_id] = {
            "repo": item["repo"],
            "version": item.get("version", ""),
            "base_commit": item["base_commit"],
            "test_patch": item.get("test_patch", ""),
            "patch": item.get("patch", ""),
            "problem_statement": item.get("problem_statement", ""),
            "image_name": image_name,
            "instance_id": instance_id,
        }

    return lookup, raw_items


# ============================================================
# TEST_SH_PARSER_AND_REWARD — appended to test.sh for grading
# ============================================================

TEST_SH_PARSER_AND_REWARD = r"""
cd ..
cat > parser.py << EOF
# /// script
# requires-python = ">=3.11"
# dependencies = ["swebench @ git+https://github.com/SWE-rebench/SWE-bench-fork.git", "datasets==2.16.1", "fastcore<1.11"]
# ///

import sys
import json
import os
import pathlib
from swebench.harness.test_spec.test_spec import make_test_spec
from swebench.harness.constants import ResolvedStatus, EvalType, PASS_TO_PASS, \
                                       FAIL_TO_PASS, KEY_INSTANCE_ID, \
                                       FAIL_ONLY_REPOS, START_TEST_OUTPUT, END_TEST_OUTPUT
from swebench.harness.grading import get_logs_eval, \
                                     get_eval_tests_report, \
                                     get_resolution_status

with open("/tests/config.json", "r") as file:
    datum = json.load(file)

test_spec   = make_test_spec(datum)
report_map = {}
instance_id = datum[KEY_INSTANCE_ID]
report_map[instance_id] = {
    "patch_is_None": False,
    "patch_exists": False,
    "patch_successfully_applied": False,
    "resolved": False,
}
report_map[instance_id]["patch_exists"] = True
test_log_path = os.environ["LOG_FILE"]
with open(test_log_path, 'r+') as f:
    content = f.read()
    f.seek(0)
    f.write(f'{START_TEST_OUTPUT}\n{content}\n{END_TEST_OUTPUT}')
    f.truncate()

eval_status_map, found = get_logs_eval(test_spec, test_log_path)
if found:
    report_map[instance_id]["patch_successfully_applied"] = True

    eval_ref = {
        KEY_INSTANCE_ID: test_spec.instance_id,
        FAIL_TO_PASS: test_spec.FAIL_TO_PASS,
        PASS_TO_PASS: test_spec.PASS_TO_PASS,
    }

    eval_type = EvalType.FAIL_ONLY if test_spec.repo in FAIL_ONLY_REPOS \
        else EvalType.PASS_AND_FAIL

    report = get_eval_tests_report(
        eval_status_map, eval_ref, eval_type=eval_type
    )
    if get_resolution_status(report) == ResolvedStatus.FULL.value:
        report_map[instance_id]["resolved"] = True
    report_map[instance_id]["tests_status"] = report

json.dump(report_map, open("/logs/verifier/report.json", "w"), indent=4)

print(f"SWEBench results starts here")
if report_map[instance_id]["resolved"]:
    print("PASSED")
else:
    print("FAILED")
print("SWEBench results ends here")
sys.exit(0 if report_map[instance_id]["resolved"] else 1)
EOF

chmod +x parser.py

# Add uv to PATH if installed
export PATH="$HOME/.local/bin:$PATH"

# Run parser and CAPTURE its exit code
set +e
uv run parser.py | tee -a "$LOG_FILE"
exit_code=$?
set -e

# Reward logging
mkdir -p /logs/verifier
if [ "${exit_code}" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

exit "${exit_code}"
"""
