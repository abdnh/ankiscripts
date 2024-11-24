from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def read_addon_json(root_dir: Path) -> dict[str, Any]:
    try:
        with open(root_dir / "addon.json", "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return {}


def write_addon_json(root_dir: Path, data: dict[str, Any]) -> None:
    with open(root_dir / "addon.json", "w", encoding="utf-8") as file:
        return json.dump(data, file)


def add_exe_suffix(path: str) -> str:
    if sys.platform == "win32":
        path = path + ".exe"
    return path


def pip_install(python_exe: str, reqs_filename: str, target: str | None = None) -> None:
    with open(reqs_filename, "r", encoding="utf-8") as file:
        if not file.read().strip():
            return
    target_args = []
    if target:
        target_args.extend(["--target", target])
    subprocess.check_call(
        [
            python_exe,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "-r",
            reqs_filename,
            *target_args,
        ]
    )


def symlink_addon(addon_root: Path, addon_package: str) -> None:
    src_path = addon_root / "src"
    install_path = addon_root / "ankidata" / "addons21" / str(addon_package)
    if not install_path.exists():
        install_path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win32"):
            subprocess.run(
                'mklink /J "{}" "{}"'.format(str(install_path), str(src_path)),
                shell=True,
                check=True,
            )
        else:
            os.link(src_path, install_path)


def run_bash_script(path: Path) -> int:
    wsl_exe = shutil.which("wsl")
    if wsl_exe:
        return subprocess.check_call(
            [wsl_exe, "--", "bash", f"$(wslpath {path.as_posix()})"]
        )

    bash_exe = shutil.which("bash")
    # Seems like Bash on Windows expects POSIX paths
    return subprocess.check_call([bash_exe, str(path.as_posix())])
