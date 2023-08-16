from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from ._utils import add_exe_suffix, pip_install


def update_deps(bin_path: str | Path, req_type: str | None = None) -> None:
    args = [
        "--resolver=backtracking",
        "--allow-unsafe",
        "--no-header",
        "--strip-extras",
        "--upgrade",
    ]
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
        subprocess.check_call([pip_compile_exe, *args, f"requirements/{req_type}.in"])
    else:
        pip_install(python_exe, "requirements/base.txt")
        subprocess.check_call([pip_compile_exe, *args, "requirements/base.in"])
        subprocess.check_call([pip_compile_exe, *args, "requirements/bundle.in"])
        subprocess.check_call([pip_compile_exe, *args, "requirements/dev.in"])
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
