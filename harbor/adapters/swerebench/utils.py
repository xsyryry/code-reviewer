import re
import shlex
from pathlib import Path
from textwrap import dedent

from swebench.harness.constants import MAP_REPO_VERSION_TO_SPECS
from swebench.harness.test_spec.python import get_test_directives
from swebench.harness.test_spec.test_spec import make_test_spec


def read_text(path: Path) -> str:
    """Read text from a file path, raising FileNotFoundError if it doesn't exist."""
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")
    return path.read_text()


def render_literal(template_text: str, **repls: str) -> str:
    """Replace only exact placeholders like {key} with provided values."""
    out = template_text
    for k, v in repls.items():
        out = out.replace("{" + k + "}", v)
    return out


def get_image_name(record: dict) -> str:
    """
    Return the Docker image name for a SWE-rebench instance.

    SWE-rebench records carry a ``docker_image`` field (and sometimes
    ``image_name``) that points to a pre-built image on Docker Hub
    under the ``swerebench/`` namespace.

    Falls back to building the image key via swebench's make_test_spec
    when neither field is present.
    """
    # Prefer the explicit docker_image / image_name fields
    if record.get("docker_image"):
        return record["docker_image"]
    if record.get("image_name"):
        return record["image_name"]

    # Fallback: compute via swebench fork (handles install_config)
    spec = make_test_spec(record, namespace="swerebench")
    return spec.instance_image_key.replace("arm64", "x86_64")


def get_image_names(samples: list[dict]) -> dict[str, str]:
    """
    Return a mapping from instance_id → Docker image name for a batch of records.
    """
    return {s["instance_id"]: get_image_name(s) for s in samples}


def get_test_commands(record: dict) -> str:
    """
    Build a bash eval script that runs the tests for a SWE-rebench instance.

    For SWE-rebench the ``install_config`` field on each record provides
    ``test_cmd``, ``eval_commands``, ``install``, etc. — so we read them
    from there instead of relying on the hardcoded MAP_REPO_VERSION_TO_SPECS.
    """
    test_patch = record.get("test_patch") or ""
    repo = record["repo"]
    version = record.get("version", "")
    base_commit = record["base_commit"]

    install_config = record.get("install_config") or {}

    conda_env = "testbed"
    repo_directory = "/testbed"

    # ---------- test command ----------
    if "test_cmd" in install_config:
        test_command_base = install_config["test_cmd"]
    elif repo in MAP_REPO_VERSION_TO_SPECS and version in MAP_REPO_VERSION_TO_SPECS[repo]:
        test_command_base = MAP_REPO_VERSION_TO_SPECS[repo][version]["test_cmd"]
    else:
        test_command_base = "pytest"

    # Resolve test files / directives from the test patch
    if test_patch:
        test_files = get_test_directives(record)
    else:
        test_files = []

    # ---------- eval-time setup commands ----------
    eval_commands = install_config.get("eval_commands", [])
    if isinstance(eval_commands, str):
        eval_commands = [eval_commands]

    install_cmd = install_config.get("install", "")

    # ---------- test-patch file list ----------
    test_patch_files = re.findall(r"--- a/(.*)", test_patch)

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
        git checkout {base_commit} {" ".join(test_patch_files)}

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
        {test_command_base} {" ".join(test_files)} || true
        exec 1>&3 2>&4 # stop record
        exec 1>&3 2>&4 # stop record

        # Reset tests back to base commit
        git checkout {base_commit} {" ".join(test_patch_files)}
    """
    )

    return eval_script
