from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import tomli


def read_addon_json(root_dir: Path) -> dict[str, Any]:
    try:
        with open(root_dir / "addon.json", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return {}


def write_addon_json(root_dir: Path, data: dict[str, Any]) -> None:
    with open(root_dir / "addon.json", "w", encoding="utf-8") as file:
        return json.dump(data, file)


def read_pyproject_toml(root_dir: Path) -> dict[str, Any]:
    with open(root_dir / "pyproject.toml", "rb") as file:
        return tomli.load(file)


def uv(*args: Any, **kwargs: Any) -> int:
    return subprocess.check_call(
        [shutil.which("uv"), *args], encoding="utf-8", **kwargs
    )


def pip_install(
    target: Path | str | None = None,
    python_version: str = "3.8",
    platform: str | None = None,
    env: dict[str, str] | None = None,
    hardlink: bool = False,
) -> None:
    extra_args = []
    if target:
        extra_args.extend(["--target", str(target)])
    if platform:
        extra_args.extend(["--python-platform", platform])
    kwargs = {}
    if env:
        kwargs["env"] = {**os.environ, **env}
    uv(
        "pip",
        "install",
        "--upgrade",
        "--requirements",
        "pyproject.toml",
        "--link-mode",
        "hardlink" if hardlink else "copy",
        "--python-version",
        python_version,
        *extra_args,
        **kwargs,
    )


def symlink_addon(addon_root: Path, addon_package: str) -> None:
    src_path = addon_root / "src"
    install_path = addon_root / "ankidata" / "addons21" / str(addon_package)
    if not install_path.exists():
        install_path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win32"):
            subprocess.run(
                f'mklink /J "{install_path}" "{src_path}"',
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


def run_powershell_script(path: Path) -> int:
    powershell_exe = shutil.which("powershell")
    return subprocess.check_call([powershell_exe, "-File", str(path)])


def run_npm(
    *args: str, wait: bool = True, **kwargs: Any
) -> int | subprocess.Popen[Any]:
    npm_exe = shutil.which("npm")
    return (
        subprocess.check_call([npm_exe, *args], **kwargs)
        if wait
        else subprocess.Popen([npm_exe, *args], **kwargs)
    )


def run_protoc(*args: str, **kwargs: Any) -> int:
    protoc_exe = shutil.which("protoc")
    return subprocess.check_call([protoc_exe, *args], **kwargs)


def run_protol(*args: str, **kwargs: Any) -> int:
    protol_exe = shutil.which("protol")
    return subprocess.check_call([protol_exe, *args], **kwargs)


def run_llvm_lipo(
    *args: str, check: bool = True, **kwargs: Any
) -> subprocess.CompletedProcess:
    llvm_lipo = shutil.which("llvm-lipo")
    return subprocess.run(
        [llvm_lipo, *args], check=check, text=True, stdout=subprocess.PIPE, **kwargs
    )


def create_universal_macos_binary(lib1: Path, lib2: Path, output_lib: Path) -> bool:
    return (
        run_llvm_lipo(
            "-create", str(lib1), str(lib2), "-output", str(output_lib), check=False
        ).returncode
        == 0
    )


def detect_macos_lib_archs(lib: Path) -> list[str]:
    return run_llvm_lipo(
        "-archs",
        str(lib),
    ).stdout.split()


def run_script(scripts_dir: Path, name: str) -> int:
    if sys.platform == "win32" and (scripts_dir / f"{name}.ps1").exists():
        script_path = scripts_dir / f"{name}.ps1"
    else:
        script_path = scripts_dir / f"{name}.sh"
    if script_path.exists():
        if script_path.suffix == ".ps1":
            return run_powershell_script(script_path)
        return run_bash_script(script_path)
    return 0
