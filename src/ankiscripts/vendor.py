from __future__ import annotations

import argparse
import itertools
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Iterable

from ._utils import pip_install, read_addon_json, run_script

LIB_EXT_GLOBS = ("*.so", "*.pyd", "*.dylib")

addon_root = Path.cwd()


def default_python_versions() -> Iterable[str]:
    addon_meta = read_addon_json(addon_root)
    min_point_version = int(addon_meta.get("min_point_version", 0))
    max_point_version = abs(int(addon_meta.get("max_point_version", 999)))
    versions = []
    if min_point_version < 17:
        versions.append("36")
    if min_point_version < 36:
        versions.append("37")
    if min_point_version < 50:
        versions.append("38")
    if max_point_version >= 50:
        versions.append("39")

    return versions


def default_platforms_for_python_version(version: str) -> tuple[str, ...]:
    if int(version) <= 38:
        return ("win_amd64", "manylinux2014_x86_64", "macosx_10_7_x86_64")
    # https://github.com/ankitects/anki/blob/740528eaf913ff4bb9d112d494a10e84fd01365a/build/configure/src/python.rs#L141
    return (
        "manylinux_2_35_x86_64",
        "manylinux_2_35_aarch64",
        # FIXME: the following two are conflicting
        "macosx_12_0_x86_64",
        # "macosx_12_0_arm64",
        "win_amd64",
    )


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
        python_versions = default_python_versions()
    if not platforms:
        platforms = itertools.chain(
            *(
                default_platforms_for_python_version(version)
                for version in python_versions
            )
        )

    addon_root = Path(".")
    reqs_path = addon_root / "requirements" / "bundle.txt"
    if reqs_path.exists():
        vendor_path = addon_root / "src" / "vendor"
        vendor_path.mkdir(exist_ok=True)
        shutil.rmtree(vendor_path)
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
            if not any(list(module_dir.rglob(g)) for g in LIB_EXT_GLOBS):
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
                    should_copy = True
                    for platform in platforms:
                        os, *_, arch = platform.split("_")
                        if os not in wheel_path.name or arch not in wheel_path.name:
                            should_copy = False
                            break
                    if not should_copy:
                        continue
                    wheel_dir = build_dir / wheel_path.stem
                    wheel_dir.mkdir(exist_ok=True)
                    with zipfile.ZipFile(wheel_path, "r") as file:
                        file.extractall(wheel_dir)
                    for p in (wheel_dir / module).rglob("*"):
                        if any(p.match(g) for g in LIB_EXT_GLOBS):
                            dst = module_dir / p.relative_to(wheel_dir / module)
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy(p, dst)

    # Additional vendoring logic (e.g. installing node modules) can be specified in scripts/vendor.(sh|ps1)
    scripts_dir = addon_root / "scripts"
    run_script(scripts_dir, "vendor")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--python-versions",
        default=",".join(default_python_versions()),
        help="A comma-separated list of Python versions to build platform-specific dependencies for (e.g. 38,39)",
    )
    parser.add_argument(
        "--platforms",
        default=",".join(
            (
                *default_platforms_for_python_version("38"),
                *default_platforms_for_python_version("39"),
            )
        ),
        help="A comma-separated list of platforms to build platform-specific dependencies for (e.g. win_amd64,manylinux_2_28_x86_64)",
    )

    args = parser.parse_args()

    install_libs(args.python_versions.split(","), args.platforms.split(","))
