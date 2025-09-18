from __future__ import annotations

import argparse
import dataclasses
import shutil
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from ._utils import (
    create_universal_macos_binary,
    detect_macos_lib_archs,
    pip_install,
    read_addon_json,
    run_script,
)
from .rewrite_imports import rewrite_imports_in_vendor_dir

addon_root = Path.cwd()
scripts_dir = addon_root / "scripts"


def get_supported_python_versions() -> Iterable[str]:
    addon_meta = read_addon_json(addon_root)
    min_point_version = int(addon_meta.get("min_point_version", 0))
    max_point_version = abs(int(addon_meta.get("max_point_version", 999999)))
    versions = []
    if min_point_version < 17:
        versions.append("36")
    if min_point_version < 36:
        versions.append("37")
    if min_point_version < 50:
        versions.append("38")
    if max_point_version >= 50:
        versions.append("39")
    if max_point_version >= 250700:
        versions.append("313")

    return versions


@dataclass
class BuildPlatform:
    name: str
    env: dict[str, str] = dataclasses.field(default_factory=dict)


def get_supported_platforms(version: str) -> list[BuildPlatform]:
    # https://github.com/ankitects/anki/blob/4506ad0c97dc543b2142bf9ee8f9717e92eab1fd/build/configure/src/python.rs#L148
    platforms = [
        BuildPlatform("x86_64-pc-windows-msvc"),
        BuildPlatform("x86_64-apple-darwin"),
    ]
    if int(version) <= 38:
        platforms.append(
            BuildPlatform("x86_64-manylinux2014", {"MACOSX_DEPLOYMENT_TARGET": "10.7"})
        )
    else:
        platforms.append(
            BuildPlatform("aarch64-apple-darwin", {"MACOSX_DEPLOYMENT_TARGET": "12.0"})
        )
        platforms.append(BuildPlatform("x86_64-manylinux_2_36"))
        platforms.append(BuildPlatform("aarch64-manylinux_2_36"))

    return platforms


LIB_EXT_GLOBS = ("*.so", "*.pyd", "*.dylib")


def get_extension_modules(module_dir: Path) -> Iterator[Path]:
    for pattern in LIB_EXT_GLOBS:
        yield from module_dir.rglob(pattern)


def get_installed_package_dirs(install_dir: Path | str) -> Iterator[Path]:
    install_dir = Path(install_dir)
    for dist_info_dir in install_dir.iterdir():
        if not dist_info_dir.is_dir() or not dist_info_dir.match("*.dist-info"):
            continue
        package_name = dist_info_dir.name.split("-")[0]
        try:
            with open(dist_info_dir / "top_level.txt", encoding="utf-8") as file:
                module = file.read().strip()
        except Exception:
            module = package_name
        yield install_dir / module


def remove_excluded_paths(vendor_path: Path, exclude: list[str]) -> None:
    for pattern in exclude:
        for path in vendor_path.glob(pattern):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()


def install_libs(exclude: list[str]) -> None:
    python_exe = shutil.which("python")
    assert python_exe is not None

    python_versions = get_supported_python_versions()
    addon_root = Path(".")
    vendor_path = addon_root / "src" / "vendor"
    vendor_path.mkdir(exist_ok=True)
    shutil.rmtree(vendor_path)
    min_python_version = min([(int(v[0]), int(v[1:])) for v in python_versions])
    pip_install(
        target=vendor_path, python_version=".".join(str(p) for p in min_python_version)
    )
    bin_path = vendor_path / "bin"
    if bin_path.exists():
        shutil.rmtree(bin_path)

    # Handle dependencies with C modules by installing dependencies for
    # all supported Python versions and platforms separately
    # and copying extension modules from them
    build_dir = addon_root / "build"
    build_dir.mkdir(exist_ok=True)
    for python_version in python_versions:
        for platform in get_supported_platforms(python_version):
            print(
                "Copying extension modules for "
                f"python_version={python_version}, platform={platform.name}"
            )
            install_dir = build_dir / f"{python_version}_{platform.name}_venv"
            pip_install(
                target=install_dir,
                python_version=f"{python_version[0]}.{python_version[1:]}",
                platform=platform.name,
                env=platform.env,
                hardlink=True,
            )
            for module_dir in get_installed_package_dirs(install_dir):
                for extension_module_path in get_extension_modules(module_dir):
                    dest_path = (
                        vendor_path / module_dir.name / extension_module_path.name
                    )
                    if dest_path.exists() and "darwin" in dest_path.name:
                        extension_module_path2 = dest_path.with_stem(
                            dest_path.stem + "_tmp"
                        )
                        dest_path.rename(extension_module_path2)
                        if create_universal_macos_binary(
                            extension_module_path, extension_module_path2, dest_path
                        ):
                            print(f"Created universal binary for {module_dir.name}")
                        else:
                            path_to_use = extension_module_path2
                            for path in (
                                extension_module_path,
                                extension_module_path2,
                            ):
                                archs = detect_macos_lib_archs(path)
                                if "arm64" in archs:
                                    path_to_use = path
                                    break
                            if dest_path.exists():
                                dest_path.unlink()
                            shutil.copy(path_to_use, dest_path)
                        extension_module_path2.unlink()
                    else:
                        shutil.copy(extension_module_path, dest_path)

    # Rewrite imports in vendored packages to be relative to the vendor directory
    rewrite_imports_in_vendor_dir(vendor_path)

    # Additional vendoring logic (e.g. installing node modules)
    # can be specified in scripts/vendor.(sh|ps1)
    run_script(scripts_dir, "vendor")

    remove_excluded_paths(vendor_path, exclude)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--exclude",
        help="Exclude paths relative to src/vendor matching given glob",
        action="append",
        default=[],
        metavar="PATTERN",
    )
    args = parser.parse_args()
    install_libs(args.exclude)
