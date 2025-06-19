from __future__ import annotations

import argparse
import itertools
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Iterable, Sequence

import libcst as cst

from ._utils import pip_install, read_addon_json, run_script, uv

LIB_EXT_GLOBS = ("*.so", "*.pyd", "*.dylib")

addon_root = Path.cwd()


def _get_relative_import_level(current_file_path: Path, vendor_path: Path) -> int:
    """Calculate the number of dots needed for relative import."""
    try:
        # Get relative path from current file to vendor directory
        rel_path = current_file_path.relative_to(vendor_path)
        # Number of parent directories to go up
        return len(rel_path.parts) - 1  # -1 because file itself doesn't count
    except ValueError:
        # File is outside vendor directory
        return 0


class LibCSTImportTransformer(cst.CSTTransformer):
    """LibCST transformer to rewrite imports for vendored packages."""

    def __init__(
        self, vendored_packages: set[str], current_file_path: Path, vendor_path: Path
    ):
        self.vendored_packages = vendored_packages
        self.current_file_path = current_file_path
        self.vendor_path = vendor_path

    def _get_relative_import_level(self) -> int:
        """Calculate the number of dots needed for relative import."""
        return _get_relative_import_level(self.current_file_path, self.vendor_path)

    def _create_dotted_name(self, dotted_name: str) -> cst.BaseExpression:
        """Create a LibCST node for a dotted module name like 'sentry_sdk.integrations.dedupe'."""
        parts = dotted_name.split(".")
        if len(parts) == 1:
            return cst.Name(parts[0])

        # Build the attribute chain: a.b.c becomes Attribute(Attribute(Name('a'), Name('b')), Name('c'))
        result = cst.Name(parts[0])
        for part in parts[1:]:
            result = cst.Attribute(value=result, attr=cst.Name(part))
        return result

    def _create_module_for_import_from(
        self, module_name: str
    ) -> cst.BaseExpression | None:
        """Create the module part for ImportFrom statements, handling dotted names properly."""
        if not module_name:
            return None

        parts = module_name.split(".")
        if len(parts) == 1:
            return cst.Name(parts[0])

        # For ImportFrom, we need to handle nested modules properly
        # If we have 'sentry_sdk.integrations.dedupe', we want the module part to be 'integrations.dedupe'
        # after the package part 'sentry_sdk'
        return self._create_dotted_name(module_name)

    def _clean_import_names(
        self, names: cst.ImportStar | Sequence[cst.ImportAlias]
    ) -> Sequence[cst.ImportAlias]:
        """Clean up import names to avoid trailing comma syntax errors."""
        if isinstance(names, cst.ImportStar):
            return names

        if not names:
            return names

        # Convert to list and ensure no trailing commas cause syntax issues
        cleaned_names = []
        for name_item in names:
            if isinstance(name_item, cst.ImportAlias):
                # Create a new ImportAlias without any trailing comma issues
                cleaned_names.append(
                    cst.ImportAlias(
                        name=name_item.name,
                        asname=name_item.asname,
                        comma=cst.MaybeSentinel.DEFAULT,  # Let LibCST handle commas properly
                    )
                )

        return cleaned_names

    def leave_SimpleStatementLine(
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ) -> (
        cst.SimpleStatementLine
        | cst.RemovalSentinel
        | Sequence[cst.SimpleStatementLine]
    ):
        """Handle import statements within simple statement lines."""
        new_statements = []

        for stmt in updated_node.body:
            if isinstance(stmt, cst.Import):
                # Handle 'import package' statements
                new_imports = []
                vendored_imports = []

                for name_item in stmt.names:
                    if isinstance(name_item, cst.ImportAlias):
                        module_name = cst.helpers.get_full_name_for_node(name_item.name)
                        if module_name:
                            package_name = module_name.split(".")[0]
                            if package_name in self.vendored_packages:
                                vendored_imports.append(name_item)
                            else:
                                new_imports.append(name_item)

                statements_to_add = []

                # Add non-vendored imports
                if new_imports:
                    statements_to_add.append(cst.Import(names=new_imports))

                # Add vendored imports as ImportFrom statements
                level = self._get_relative_import_level()
                for alias in vendored_imports:
                    module_name = cst.helpers.get_full_name_for_node(alias.name)
                    if not module_name:
                        continue

                    if level == 0:
                        # File is outside vendor directory
                        # For "import sentry_sdk.integrations.dedupe", create "from .vendor.sentry_sdk.integrations import dedupe"
                        if "." in module_name:
                            parts = module_name.split(".")
                            package_part = parts[0]  # sentry_sdk
                            submodule_parts = parts[1:]  # ['integrations', 'dedupe']
                            imported_name = parts[-1]  # dedupe

                            # Create vendor.sentry_sdk.integrations
                            vendor_module = cst.Attribute(
                                value=cst.Name("vendor"), attr=cst.Name(package_part)
                            )
                            for part in submodule_parts[
                                :-1
                            ]:  # All except the last part
                                vendor_module = cst.Attribute(
                                    value=vendor_module, attr=cst.Name(part)
                                )

                            statements_to_add.append(
                                cst.ImportFrom(
                                    module=vendor_module,
                                    names=[
                                        cst.ImportAlias(
                                            name=cst.Name(imported_name),
                                            asname=alias.asname,
                                        )
                                    ],
                                    relative=[cst.Dot()],
                                )
                            )
                        else:
                            # Simple import like "import sentry_sdk"
                            statements_to_add.append(
                                cst.ImportFrom(
                                    module=cst.Attribute(
                                        value=cst.Name("vendor"),
                                        attr=cst.Name(module_name),
                                    ),
                                    names=[
                                        cst.ImportAlias(
                                            name=cst.Name(module_name),
                                            asname=alias.asname,
                                        )
                                    ],
                                    relative=[cst.Dot()],
                                )
                            )
                    else:
                        # File is inside vendor directory
                        current_package = self.current_file_path.parent.name
                        package_name = module_name.split(".")[0]

                        if package_name == current_package:
                            # Importing from same package
                            if "." in module_name:
                                # Submodule import: "import sentry_sdk.integrations.dedupe" -> "from .integrations import dedupe"
                                parts = module_name.split(".")
                                submodule_parts = parts[
                                    1:
                                ]  # Everything after the package name
                                imported_name = parts[-1]  # The final module name

                                if len(submodule_parts) == 1:
                                    # Simple submodule: sentry_sdk.client -> from . import client
                                    statements_to_add.append(
                                        cst.ImportFrom(
                                            module=None,
                                            names=[
                                                cst.ImportAlias(
                                                    name=cst.Name(imported_name),
                                                    asname=alias.asname,
                                                )
                                            ],
                                            relative=[cst.Dot()],
                                        )
                                    )
                                else:
                                    # Nested submodule: sentry_sdk.integrations.dedupe -> from .integrations import dedupe
                                    submodule_path = ".".join(submodule_parts[:-1])
                                    statements_to_add.append(
                                        cst.ImportFrom(
                                            module=self._create_module_for_import_from(
                                                submodule_path
                                            ),
                                            names=[
                                                cst.ImportAlias(
                                                    name=cst.Name(imported_name),
                                                    asname=alias.asname,
                                                )
                                            ],
                                            relative=[cst.Dot()],
                                        )
                                    )
                            else:
                                # Simple package import: "import sentry_sdk" -> "from .. import sentry_sdk"
                                dots = [cst.Dot()] * (level + 1)
                                statements_to_add.append(
                                    cst.ImportFrom(
                                        module=None,
                                        names=[
                                            cst.ImportAlias(
                                                name=cst.Name(package_name),
                                                asname=alias.asname,
                                            )
                                        ],
                                        relative=dots,
                                    )
                                )
                        else:
                            # Importing from different package
                            if "." in module_name:
                                # Submodule import: "import requests.auth.basic" -> "from ..requests.auth import basic"
                                parts = module_name.split(".")
                                package_name = parts[0]
                                submodule_parts = parts[1:]
                                imported_name = parts[-1]
                                dots = [cst.Dot()] * (level + 1)

                                if len(submodule_parts) == 1:
                                    # requests.auth -> from ..requests import auth
                                    statements_to_add.append(
                                        cst.ImportFrom(
                                            module=cst.Name(package_name),
                                            names=[
                                                cst.ImportAlias(
                                                    name=cst.Name(imported_name),
                                                    asname=alias.asname,
                                                )
                                            ],
                                            relative=dots,
                                        )
                                    )
                                else:
                                    # requests.auth.basic -> from ..requests.auth import basic
                                    submodule_path = ".".join(submodule_parts[:-1])
                                    module_node = cst.Attribute(
                                        value=cst.Name(package_name),
                                        attr=self._create_dotted_name(submodule_path),
                                    )
                                    statements_to_add.append(
                                        cst.ImportFrom(
                                            module=module_node,
                                            names=[
                                                cst.ImportAlias(
                                                    name=cst.Name(imported_name),
                                                    asname=alias.asname,
                                                )
                                            ],
                                            relative=dots,
                                        )
                                    )
                            else:
                                # Simple package import: "import requests" -> "from .. import requests"
                                dots = [cst.Dot()] * (level + 1)
                                statements_to_add.append(
                                    cst.ImportFrom(
                                        module=None,
                                        names=[
                                            cst.ImportAlias(
                                                name=cst.Name(module_name),
                                                asname=alias.asname,
                                            )
                                        ],
                                        relative=dots,
                                    )
                                )

                if statements_to_add:
                    new_statements.extend(statements_to_add)

            elif isinstance(stmt, cst.ImportFrom):
                # Handle 'from package import x' statements
                if stmt.module and not stmt.relative:  # Skip relative imports
                    module_name = cst.helpers.get_full_name_for_node(stmt.module)
                    if module_name:
                        package_name = module_name.split(".")[0]
                        if package_name == "vendor" and len(module_name.split(".")) > 1:
                            package_name = module_name.split(".")[1]

                        if package_name in self.vendored_packages:
                            level = self._get_relative_import_level()
                            if level == 0:
                                # File is outside vendor directory
                                vendor_module = cst.Attribute(
                                    value=cst.Name("vendor"),
                                    attr=self._create_dotted_name(module_name),
                                )
                                new_statements.append(
                                    cst.ImportFrom(
                                        module=vendor_module,
                                        names=self._clean_import_names(stmt.names),
                                        relative=[cst.Dot()],
                                    )
                                )
                            else:
                                # File is inside vendor directory
                                current_package = self.current_file_path.parent.name
                                if package_name == current_package:
                                    # Importing from same package
                                    if module_name == package_name:
                                        # Direct import: "from sentry_sdk import Hub" -> "from . import Hub"
                                        new_statements.append(
                                            cst.ImportFrom(
                                                module=None,
                                                names=self._clean_import_names(
                                                    stmt.names
                                                ),
                                                relative=[cst.Dot()],
                                            )
                                        )
                                    else:
                                        # Submodule import: "from sentry_sdk.integrations.dedupe import something"
                                        # -> "from .integrations.dedupe import something"
                                        submodule = module_name[len(package_name) + 1 :]
                                        new_statements.append(
                                            cst.ImportFrom(
                                                module=self._create_module_for_import_from(
                                                    submodule
                                                ),
                                                names=self._clean_import_names(
                                                    stmt.names
                                                ),
                                                relative=[cst.Dot()],
                                            )
                                        )
                                else:
                                    # Importing from different package
                                    dots = [cst.Dot()] * (level + 1)
                                    new_statements.append(
                                        cst.ImportFrom(
                                            module=self._create_module_for_import_from(
                                                module_name
                                            ),
                                            names=self._clean_import_names(stmt.names),
                                            relative=dots,
                                        )
                                    )
                        else:
                            new_statements.append(stmt)
                    else:
                        new_statements.append(stmt)
                else:
                    new_statements.append(stmt)
            else:
                new_statements.append(stmt)

        if new_statements != list(updated_node.body):
            return updated_node.with_changes(body=new_statements)

        return updated_node


def rewrite_imports_with_libcst(
    file_path: Path, vendored_packages: set[str], vendor_path: Path
) -> None:
    """Rewrite imports in a single Python file using LibCST."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            source_code = f.read()

        # Parse the code with LibCST
        tree = cst.parse_module(source_code)

        # Transform the tree
        transformer = LibCSTImportTransformer(vendored_packages, file_path, vendor_path)
        new_tree = tree.visit(transformer)

        # Generate new code
        new_code = new_tree.code

        # Only write if content changed
        if new_code != source_code:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_code)

    except Exception as e:
        print(
            f"Warning: Could not rewrite imports in {file_path}: {e}", file=sys.stderr
        )


def rewrite_imports_in_vendor_dir(vendor_path: Path) -> None:
    """Rewrite imports in all Python files within the vendor directory using LibCST."""
    vendored_packages = set()

    for item in vendor_path.iterdir():
        if (
            item.is_dir()
            and not item.name.endswith(".dist-info")
            and not item.name.endswith(".egg-info")
        ):
            # Package directory
            vendored_packages.add(item.name)
        elif item.is_file() and item.suffix == ".py" and not item.name.startswith("_"):
            # Single module file (like typing_extensions.py)
            vendored_packages.add(item.stem)

    print(f"Found vendored packages: {', '.join(sorted(vendored_packages))}")

    python_files = list(vendor_path.rglob("*.py"))
    print(f"Rewriting imports in {len(python_files)} Python files...")

    for py_file in python_files:
        rewrite_imports_with_libcst(py_file, vendored_packages, vendor_path)

    print("Import rewriting completed.")


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
        "macosx_12_0_universal2",
        "macosx_12_0_x86_64",
        "macosx_12_0_arm64",
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


def create_universal_binary(
    x86_64_lib: Path, arm64_lib: Path, output_lib: Path
) -> bool:
    """Create a universal binary from x86_64 and arm64 libraries using llvm-lipo."""
    try:
        subprocess.check_call(
            [
                "llvm-lipo",
                "-create",
                str(x86_64_lib),
                str(arm64_lib),
                "-output",
                str(output_lib),
            ]
        )
        print(f"Created universal binary: {output_lib}")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(
            f"Warning: Could not create universal binary for {output_lib}: {e}",
            file=sys.stderr,
        )
        return False


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

    reqs_path = addon_root / ".reqs.txt"
    reqs_path.write_text(
        uv("export", "--no-dev", "--no-editable", "--no-emit-project"),
        encoding="utf-8",
    )
    vendor_path = addon_root / "src" / "vendor"
    vendor_path.mkdir(exist_ok=True)
    shutil.rmtree(vendor_path)
    python_exe = shutil.which("python")
    pip_install(str(reqs_path), str(vendor_path))
    reqs_path.unlink()
    bin_path = vendor_path / "bin"
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
            with open(dist_info_dir / "top_level.txt", "r", encoding="utf-8") as file:
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

            # Check if we have both x86_64 and arm64 macOS platforms
            macos_platforms = [p for p in platforms if p.startswith("macosx_")]
            has_x86_64 = any("x86_64" in p for p in macos_platforms)
            has_arm64 = any("arm64" in p for p in macos_platforms)
            should_create_universal = has_x86_64 and has_arm64

            # Extract wheels and collect libraries by architecture
            wheel_libs_by_arch: dict[
                str, dict[Path, Path]
            ] = {}  # arch -> {relative_path: absolute_path}

            for wheel_path in build_dir.glob(
                f"{package_name}-{version}-cp{python_version}-*.whl"
            ):
                should_process = False
                wheel_arch = None
                is_macos = False

                for platform in platforms:
                    os_name, *_, arch = platform.split("_")
                    if arch == "64":
                        arch = "x86_64"
                    if os_name in wheel_path.name and arch in wheel_path.name:
                        should_process = True
                        wheel_arch = arch
                        is_macos = os_name == "macosx"
                        break

                if not should_process:
                    continue

                wheel_dir = build_dir / wheel_path.stem
                wheel_dir.mkdir(exist_ok=True)
                with zipfile.ZipFile(wheel_path, "r") as file:
                    file.extractall(wheel_dir)

                # Collect libraries from this wheel
                for p in (wheel_dir / module).rglob("*"):
                    if any(p.match(g) for g in LIB_EXT_GLOBS):
                        relative_path = p.relative_to(wheel_dir / module)

                        if is_macos and wheel_arch:
                            # Collect macOS libraries by architecture
                            if wheel_arch not in wheel_libs_by_arch:
                                wheel_libs_by_arch[wheel_arch] = {}
                            wheel_libs_by_arch[wheel_arch][relative_path] = p
                        else:
                            # For non-macOS, copy directly
                            dst = module_dir / relative_path
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy(p, dst)

                            # Process macOS libraries - prioritize universal2, then create universal binaries, then copy individually
            if "universal2" in wheel_libs_by_arch:
                # Use universal2 wheel if available (already contains both architectures)
                universal2_libs = wheel_libs_by_arch["universal2"]
                for relative_path, lib_path in universal2_libs.items():
                    dst = module_dir / relative_path
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(lib_path, dst)
                print(f"Used universal2 wheel for {package_name}")
            elif (
                should_create_universal
                and "x86_64" in wheel_libs_by_arch
                and "arm64" in wheel_libs_by_arch
            ):
                # Create universal binaries from separate x86_64 and arm64 wheels
                x86_64_libs = wheel_libs_by_arch["x86_64"]
                arm64_libs = wheel_libs_by_arch["arm64"]

                # Find common libraries that exist in both architectures
                common_libs = set(x86_64_libs.keys()) & set(arm64_libs.keys())
                x86_64_only = set(x86_64_libs.keys()) - common_libs
                arm64_only = set(arm64_libs.keys()) - common_libs

                # Create universal binaries for common libraries
                for relative_path in common_libs:
                    dst = module_dir / relative_path
                    dst.parent.mkdir(parents=True, exist_ok=True)

                    x86_64_lib = x86_64_libs[relative_path]
                    arm64_lib = arm64_libs[relative_path]

                    if not create_universal_binary(x86_64_lib, arm64_lib, dst):
                        # Fall back to copying x86_64 version if lipo fails
                        shutil.copy(x86_64_lib, dst)

                # Copy architecture-specific macOS libraries
                for relative_path in x86_64_only:
                    dst = module_dir / relative_path
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(x86_64_libs[relative_path], dst)

                for relative_path in arm64_only:
                    dst = module_dir / relative_path
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(arm64_libs[relative_path], dst)

                print(
                    f"Created {len(common_libs)} universal binaries for {package_name}"
                )
            elif wheel_libs_by_arch:
                # Copy remaining macOS libraries that weren't processed for universal binaries
                for arch_libs in wheel_libs_by_arch.values():
                    for relative_path, lib_path in arch_libs.items():
                        dst = module_dir / relative_path
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy(lib_path, dst)

    # Rewrite imports in vendored packages to be relative to the vendor directory
    rewrite_imports_in_vendor_dir(vendor_path)

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
