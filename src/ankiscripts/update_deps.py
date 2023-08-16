from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ._utils import add_exe_suffix, pip_install


def update_deps(bin_path: str | Path) -> None:
    args = [
        "--resolver=backtracking",
        "--allow-unsafe",
        "--no-header",
        "--strip-extras",
        "--upgrade",
    ]
    bin_path = Path(bin_path)
    python_exe = add_exe_suffix(str(bin_path / "python"))
    pip_install(python_exe, "requirements/base.txt")
    pip_compile_exe = add_exe_suffix(str(bin_path / "pip-compile"))
    pip_sync_exe = add_exe_suffix(str(bin_path / "pip-sync"))

    subprocess.check_call([pip_compile_exe, *args, "requirements/base.in"])
    subprocess.check_call([pip_compile_exe, *args, "requirements/bundle.in"])
    subprocess.check_call([pip_compile_exe, *args, "requirements/dev.in"])
    subprocess.check_call([pip_sync_exe, "requirements/dev.txt"])


if __name__ == "__main__":
    if sys.platform.startswith("win32"):
        bin_path = "venv/Scripts"
    else:
        bin_path = "venv/bin"
    update_deps(bin_path)
