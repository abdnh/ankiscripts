from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from ._utils import add_exe_suffix, pip_install


def compile_requirements(uv_exe: str, req_type: str, package: str = "all") -> None:
    requirements_in_path = Path(f"requirements/{req_type}.in")
    if requirements_in_path.exists():
        args = ["--universal", "--no-header"]
        if package == "all":
            args.append("--upgrade")
        else:
            args.extend(["--upgrade-package", package])
        with open(requirements_in_path, "r", encoding="utf-8") as file:
            if file.read().strip():
                subprocess.check_call(
                    [
                        uv_exe,
                        "pip",
                        "compile",
                        *args,
                        str(requirements_in_path),
                        "-o",
                        requirements_in_path.with_suffix(".txt"),
                    ]
                )


def update_deps(
    bin_path: str | Path, req_type: str | None = None, package: str = "all"
) -> None:
    bin_path = Path(bin_path)
    if bin_path.exists():
        python_exe = add_exe_suffix(str(bin_path / "python"))
        uv_exe = add_exe_suffix(str(bin_path / "uv"))
    else:
        python_exe = shutil.which("python")
        uv_exe = shutil.which("uv")
    if req_type is not None:
        compile_requirements(uv_exe, req_type, package)
    else:
        pip_install(python_exe, "requirements/base.txt")
        compile_requirements(uv_exe, "base", package)
        compile_requirements(uv_exe, "bundle", package)
        compile_requirements(uv_exe, "dev", package)
        subprocess.check_call([uv_exe, "pip", "sync", "requirements/dev.txt"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=("base", "bundle", "dev"), required=False)
    parser.add_argument("--package", required=False, default="all")
    args = parser.parse_args()
    if sys.platform.startswith("win32"):
        bin_path = "venv/Scripts"
    else:
        bin_path = "venv/bin"
    update_deps(bin_path, args.type, args.package)
