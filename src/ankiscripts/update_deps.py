from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ._utils import add_exe_suffix, pip_install

# Work around 'error: invalid object' when run as pre-commit hook
# https://github.com/jazzband/pip-tools/issues/1359
if "GIT_INDEX_FILE" in os.environ:
    del os.environ["GIT_INDEX_FILE"]

pip_compile_args = [
    "--resolver=backtracking",
    "--allow-unsafe",
    "--no-header",
    "--strip-extras",
]


def compile_requirements(
    pip_compile_exe: str, req_type: str, package: str = "all"
) -> None:
    requirements_in_path = Path(f"requirements/{req_type}.in")
    if requirements_in_path.exists():
        args = pip_compile_args.copy()
        if package == "all":
            args.append("--upgrade")
        else:
            args.extend(["--upgrade-package", package])
        with open(requirements_in_path, "r", encoding="utf-8") as file:
            if file.read().strip():
                subprocess.check_call(
                    [pip_compile_exe, *args, str(requirements_in_path)]
                )
                requirements_txt_path = requirements_in_path.with_suffix(".txt")
                requirements_txt_contents = requirements_txt_path.read_text(
                    encoding="utf-8"
                )
                # Work around pip-tools writing absolute paths: https://github.com/jazzband/pip-tools/issues/2131
                requirements_txt_contents = re.sub(
                    f"{re.escape(str(requirements_txt_path.parent.parent.absolute()))}(\\\\|/)",
                    "",
                    requirements_txt_contents,
                )
                requirements_txt_path.write_text(
                    requirements_txt_contents, encoding="utf-8"
                )


def update_deps(
    bin_path: str | Path, req_type: str | None = None, package: str = "all"
) -> None:
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
        compile_requirements(pip_compile_exe, req_type, package)
    else:
        pip_install(python_exe, "requirements/base.txt")
        compile_requirements(pip_compile_exe, "base", package)
        compile_requirements(pip_compile_exe, "bundle", package)
        compile_requirements(pip_compile_exe, "dev", package)
        subprocess.check_call([pip_sync_exe, "requirements/dev.txt"])


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
