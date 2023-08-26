from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Iterable

from ._utils import pip_install

LIB_EXT_GLOBS = ("*.so", "*.pyd", "*.dylib")
# TODO: do we really need to specify all of these?
DEFAULT_PLATFORMS = (
    "win_amd64",
    "manylinux_2_5_x86_64",
    "manylinux_2_12_x86_64",
    "manylinux_2_17_x86_64",
    "manylinux_2_28_x86_64",
    "manylinux_2_31_aarch64",
    "macosx_10_9_x86_64",
    "macosx_10_10_x86_64",
    "macosx_10_13_x86_64",
    "macosx_11_0_arm64",
)
DEFAULT_PYTHON_VERSIONS = ("38", "39")


def pip_download(
    python_exe: str,
    package_name: str,
    version: str,
    python_version: str,
    platform: str,
    dest: str,
) -> None:
    try:
        subprocess.check_call(
            [
                python_exe,
                "-m",
                "pip",
                "download",
                "--only-binary=:all:",
                f"{package_name}=={version}",
                "--python-version",
                python_version,
                "--implementation",
                "cp",
                "--platform",
                platform,
                "-d",
                dest,
            ]
        )
    except subprocess.CalledProcessError as exc:
        print(str(exc), file=sys.stderr)


def install_libs(
    python_versions: Iterable[str] | None = None, platforms: Iterable[str] | None = None
) -> None:
    if not python_versions:
        python_versions = DEFAULT_PYTHON_VERSIONS
    if not platforms:
        platforms = DEFAULT_PLATFORMS

    addon_root = Path(".")
    reqs_path = addon_root / "requirements" / "bundle.txt"
    if reqs_path.exists():
        vendor_path = addon_root / "src" / "vendor"
        bin_path = vendor_path / "bin"
        python_exe = shutil.which("python")
        pip_install(python_exe, str(reqs_path), str(vendor_path))
        if bin_path.exists():
            shutil.rmtree(bin_path)

        # Handle dependencies with C modules by downloading wheels for all supported platforms and copying C libraries from them
        build_dir = addon_root / "build"
        build_dir.mkdir(exist_ok=True)
        for dist_info_dir in vendor_path.iterdir():
            if not dist_info_dir.is_dir() or not dist_info_dir.match("*.dist-info"):
                continue
            package_name = dist_info_dir.name.split("-")[0]
            try:
                with open(
                    dist_info_dir / "top_level.txt", "r", encoding="utf-8"
                ) as file:
                    module = file.read().strip()
            except Exception:
                module = package_name
            module_dir = vendor_path / module
            if not any(module_dir.rglob(g) for g in LIB_EXT_GLOBS):
                continue
            version = dist_info_dir.name.split("-")[1].rsplit(".", maxsplit=1)[0]
            for python_version in python_versions:
                for platform in platforms:
                    pip_download(
                        python_exe,
                        package_name,
                        version,
                        python_version,
                        platform,
                        str(build_dir),
                    )
                for wheel_path in build_dir.glob(
                    f"{package_name}-{version}-cp{python_version}-*.whl"
                ):
                    wheel_dir = build_dir / wheel_path.stem
                    wheel_dir.mkdir(exist_ok=True)
                    with zipfile.ZipFile(wheel_path, "r") as file:
                        file.extractall(wheel_dir)
                    for p in (wheel_dir / module).rglob("*"):
                        if any(p.match(g) for g in LIB_EXT_GLOBS):
                            dst = module_dir / p.relative_to(wheel_dir / module)
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy(p, dst)

    # Additional vendoring logic (e.g. installing node modules) can be specified in scripts/vendor.sh
    vendor_script_path = addon_root / "scripts" / "vendor.sh"
    bash_exe = shutil.which("bash")
    if vendor_script_path.exists():
        # Seems like Bash on Windows expects POSIX paths
        subprocess.check_call([bash_exe, str(vendor_script_path.as_posix())])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--python-versions",
        default=",".join(DEFAULT_PYTHON_VERSIONS),
        help="A comma-separated list of Python versions to build platform-specific dependencies for (e.g. 38,39)",
    )
    parser.add_argument(
        "--platforms",
        default=",".join(DEFAULT_PLATFORMS),
        help="A comma-separated list of platforms to build platform-specific dependencies for (e.g. win_amd64,manylinux_2_28_x86_64)",
    )

    args = parser.parse_args()

    install_libs(args.python_versions.split(","), args.platforms.split(","))
