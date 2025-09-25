"""
Run vermin using the add-on's minimum supported Python version.
"""

import subprocess
import sys
from pathlib import Path

from ._utils import read_pyproject_toml, uv


def get_required_python() -> str:
    pyproject = read_pyproject_toml(Path.cwd())
    spec = pyproject["project"]["requires-python"]
    # NOTE: Assuming >= is used
    return spec.lstrip(">=")


def run_vermin(min_version: str) -> int:
    cmd = [
        f"--target={min_version}",
        "--violations",
        "--eval-annotations",
        "--no-make-paths-absolute",
        "--no-tips",
        "--exclude-regex",
        r"^src[/\\](vendor|proto)[/\\]",
        "src",
    ]
    try:
        return uv("run", "--", "vermin", *cmd)
    except subprocess.CalledProcessError as e:
        return e.returncode


if __name__ == "__main__":
    min_version = get_required_python()
    sys.exit(run_vermin(min_version))
