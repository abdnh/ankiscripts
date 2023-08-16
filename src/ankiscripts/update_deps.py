from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ._utils import add_exe_suffix, pip_install

pip_compile_args = [
    "--resolver=backtracking",
    "--allow-unsafe",
    "--no-header",
    "--strip-extras",
    "--upgrade",
]


def compile_requirements(pip_compile_exe: str, req_type: str) -> None:
    requirements_filename = f"requirements/{req_type}.in"
    if os.path.exists(requirements_filename):
        with open(requirements_filename, "r", encoding="utf-8") as file:
            if file.read().strip():
                subprocess.check_call(
                    [pip_compile_exe, *pip_compile_args, requirements_filename]
                )


def update_deps(bin_path: str | Path, req_type: str | None = None) -> None:
    bin_path = Path(bin_path)
    if bin_path.exists():
        python_exe = add_exe_suffix(str(bin_path / "python"))
        pip_compile_exe = add_exe_suffix(str(bin_path / "pip-compile"))
        pip_sync_exe = add_exe_suffix(str(bin_path / "pip-sync"))
    else:
        python_exe = shutil.which("python")
        pip_compile_exe = shutil.which("pip-compile")
        pip_sync_exe = shutil.which("pip-sync")
    if req_type is not None:
        compile_requirements(pip_compile_exe, req_type)
    else:
        pip_install(python_exe, "requirements/base.txt")
        compile_requirements(pip_compile_exe, "base")
        compile_requirements(pip_compile_exe, "bundle")
        compile_requirements(pip_compile_exe, "dev")
        subprocess.check_call([pip_sync_exe, "requirements/dev.txt"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=("base", "bundle", "dev"), required=False)
    args = parser.parse_args()
    if sys.platform.startswith("win32"):
        bin_path = "venv/Scripts"
    else:
        bin_path = "venv/bin"
    update_deps(bin_path, args.type)
