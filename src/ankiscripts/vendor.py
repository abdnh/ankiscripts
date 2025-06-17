from __future__ import annotations

import argparse
import ast
import itertools
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Iterable

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


def _rewrite_import_line(
    line: str, vendored_packages: set[str], current_file_path: Path, vendor_path: Path
) -> str:
    """Rewrite a single import line if it imports from vendored packages."""
    stripped = line.strip()

    # Skip non-import lines, comments, and empty lines
    if (
        not stripped
        or stripped.startswith("#")
        or not (stripped.startswith("import ") or stripped.startswith("from "))
    ):
        return line

    level = _get_relative_import_level(current_file_path, vendor_path)

    # Handle 'import package' statements
    import_match = re.match(r"^(\s*)import\s+(.+)$", line)
    if import_match:
        indent, imports = import_match.groups()
        import_parts = [part.strip() for part in imports.split(",")]

        new_imports = []
        vendored_imports = []

        for import_part in import_parts:
            # Handle 'as' aliases
            if " as " in import_part:
                module_name, alias = import_part.split(" as ", 1)
                module_name = module_name.strip()
                alias = alias.strip()
            else:
                module_name = import_part.strip()
                alias = None

            package_name = module_name.split(".")[0]

            if package_name in vendored_packages:
                vendored_imports.append((module_name, alias))
            else:
                new_imports.append(import_part)

        # Build the replacement lines
        result_lines = []

        # Add non-vendored imports
        if new_imports:
            result_lines.append(f"{indent}import {', '.join(new_imports)}")

        # Add vendored imports as ImportFrom statements
        for module_name, alias in vendored_imports:
            alias_part = f" as {alias}" if alias else ""

            if level == 0:
                # File is outside vendor directory
                result_lines.append(
                    f"{indent}from .vendor import {module_name}{alias_part}"
                )
            else:
                # File is inside vendor directory
                try:
                    current_package = current_file_path.parent.name
                    package_name = module_name.split(".")[0]

                    if package_name == current_package:
                        # Importing from same package
                        if "." in module_name:
                            # Submodule import: "import sentry_sdk.utils" -> "from . import utils"
                            submodule = module_name.split(".")[-1]
                            result_lines.append(
                                f"{indent}from . import {submodule}{alias_part}"
                            )
                        else:
                            # Simple package import: "import sentry_sdk" -> "from .. import sentry_sdk"
                            dots = "." * (level + 1)
                            result_lines.append(
                                f"{indent}from {dots} import {package_name}{alias_part}"
                            )
                    else:
                        # Importing from different package
                        if "." in module_name:
                            # Submodule import: "import typing_extensions.utils" -> "from ..typing_extensions import utils"
                            package_name, *submodule_parts = module_name.split(".")
                            submodule_name = submodule_parts[-1]
                            dots = "." * (level + 1)
                            result_lines.append(
                                f"{indent}from {dots}{package_name} import {submodule_name}{alias_part}"
                            )
                        else:
                            # Simple package import: "import typing_extensions" -> "from .. import typing_extensions"
                            dots = "." * (level + 1)
                            result_lines.append(
                                f"{indent}from {dots} import {module_name}{alias_part}"
                            )
                except (AttributeError, IndexError):
                    # Fallback
                    if "." in module_name:
                        package_name, *submodule_parts = module_name.split(".")
                        submodule_name = submodule_parts[-1]
                        dots = "." * (level + 1)
                        result_lines.append(
                            f"{indent}from {dots}{package_name} import {submodule_name}{alias_part}"
                        )
                    else:
                        dots = "." * (level + 1)
                        result_lines.append(
                            f"{indent}from {dots} import {module_name}{alias_part}"
                        )

        if len(result_lines) == 1:
            return result_lines[0]
        elif len(result_lines) > 1:
            return "\n".join(result_lines)
        else:
            return ""  # All imports were removed

    # Handle 'from package import x' statements
    from_match = re.match(r"^(\s*)from\s+([^\s]+)\s+import\s+(.+)$", line)
    if from_match:
        indent, module, imports = from_match.groups()

        # Skip relative imports that are already correct
        if module.startswith("."):
            return line

        # Check if this is a vendored package
        package_name = module.split(".")[0]
        if package_name == "vendor":
            package_name = module.split(".")[1] if len(module.split(".")) > 1 else ""

        if package_name in vendored_packages:
            if level == 0:
                # File is outside vendor directory
                new_module = f".vendor.{module}"
                return f"{indent}from {new_module} import {imports}"
            else:
                # File is inside vendor directory
                try:
                    current_package = current_file_path.parent.name
                    if package_name == current_package:
                        # Importing from same package
                        if module == package_name:
                            # Direct import: "from sentry_sdk import utils" -> "from . import utils"
                            return f"{indent}from . import {imports}"
                        else:
                            # Submodule import: "from sentry_sdk.utils import AnnotatedValue" -> "from .utils import AnnotatedValue"
                            submodule = module[
                                len(package_name) + 1 :
                            ]  # +1 for the dot
                            return f"{indent}from .{submodule} import {imports}"
                    else:
                        # Importing from different package
                        dots = "." * (level + 1)
                        return f"{indent}from {dots}{module} import {imports}"
                except (AttributeError, IndexError):
                    # Fallback
                    dots = "." * (level + 1)
                    return f"{indent}from {dots}{module} import {imports}"

    return line


def rewrite_imports_in_file(
    file_path: Path, vendored_packages: set[str], vendor_path: Path
) -> None:
    """Rewrite imports in a single Python file using line-by-line processing."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        new_lines = []
        for line in lines:
            new_line = _rewrite_import_line(
                line.rstrip("\n\r"), vendored_packages, file_path, vendor_path
            )

            # Handle case where one line becomes multiple lines
            if "\n" in new_line:
                new_lines.extend(new_line.split("\n"))
            elif new_line:  # Skip empty lines (removed imports)
                new_lines.append(new_line)

        # Add back line endings
        new_content = "\n".join(new_lines)
        if lines and lines[-1].endswith("\n"):
            new_content += "\n"

        # Only write if content changed
        original_content = "".join(lines)
        if new_content != original_content:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)

    except (UnicodeDecodeError, OSError) as e:
        print(
            f"Warning: Could not rewrite imports in {file_path}: {e}", file=sys.stderr
        )


def rewrite_imports_in_vendor_dir(vendor_path: Path) -> None:
    """Rewrite imports in all Python files within the vendor directory."""
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
        rewrite_imports_in_file(py_file, vendored_packages, vendor_path)

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
        # FIXME: the following two are conflicting
        # "macosx_12_0_x86_64",
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
            for wheel_path in build_dir.glob(
                f"{package_name}-{version}-cp{python_version}-*.whl"
            ):
                should_copy = False
                for platform in platforms:
                    os, *_, arch = platform.split("_")
                    if arch == "64":
                        arch = "x86_64"
                    if os in wheel_path.name and arch in wheel_path.name:
                        should_copy = True
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

    # Rewrite imports in vendored packages to be relative to the vendor directory
    rewrite_imports_in_vendor_dir(vendor_path)

    # Additional vendoring logic (e.g. installing node modules) can be specified in scripts/vendor.(sh|ps1)
    scripts_dir = addon_root / "scripts"
    run_script(scripts_dir, "vendor")


class ImportRewriter(ast.NodeTransformer):
    """AST transformer to rewrite absolute imports to relative imports for vendored packages.

    NOTE: This class is now deprecated in favor of line-by-line processing.
    It's kept for backward compatibility but should not be used directly.
    """

    def __init__(
        self, vendored_packages: set[str], current_file_path: Path, vendor_path: Path
    ):
        self.vendored_packages = vendored_packages
        self.current_file_path = current_file_path
        self.vendor_path = vendor_path

    def _get_relative_import_level(self) -> int:
        """Calculate the number of dots needed for relative import."""
        return _get_relative_import_level(self.current_file_path, self.vendor_path)

    def visit_Import(self, node: ast.Import) -> ast.Import | ast.ImportFrom | list:
        """Rewrite 'import package' to relative imports for vendored packages."""
        new_aliases = []
        vendored_imports = []

        for alias in node.names:
            package_name = alias.name.split(".")[0]
            if package_name in self.vendored_packages:
                vendored_imports.append(alias)
            else:
                new_aliases.append(alias)

        # Create nodes for the different types of imports
        nodes: list[ast.Import | ast.ImportFrom] = []

        # Add non-vendored imports
        if new_aliases:
            nodes.append(ast.Import(names=new_aliases))

        # Add vendored imports as ImportFrom statements
        for alias in vendored_imports:
            level = self._get_relative_import_level()
            if level == 0:
                # File is outside vendor directory, import from vendor
                nodes.append(
                    ast.ImportFrom(
                        module="vendor",
                        names=[ast.alias(name=alias.name, asname=alias.asname)],
                        level=1,
                    )
                )
            else:
                # File is inside vendor directory, use relative import
                package_name = alias.name.split(".")[0]
                try:
                    current_package = self.current_file_path.parent.name
                    if package_name == current_package:
                        # Importing from same package, use level=1
                        if "." in alias.name:
                            # Submodule import: "import sentry_sdk.utils" -> "from . import utils"
                            submodule = alias.name.split(".")[-1]
                            nodes.append(
                                ast.ImportFrom(
                                    module="",
                                    names=[
                                        ast.alias(name=submodule, asname=alias.asname)
                                    ],
                                    level=1,
                                )
                            )
                        else:
                            # Simple package import from within same package: "import sentry_sdk" in sentry_sdk/utils.py
                            # Convert to relative import: "import sentry_sdk" -> "from .. import sentry_sdk"
                            nodes.append(
                                ast.ImportFrom(
                                    module="",
                                    names=[
                                        ast.alias(
                                            name=package_name, asname=alias.asname
                                        )
                                    ],
                                    level=level + 1,
                                )
                            )
                    else:
                        # Importing from different package
                        if "." in alias.name:
                            # Submodule import: "import typing_extensions.utils" -> "from ..typing_extensions import utils"
                            package_name, *submodule_parts = alias.name.split(".")
                            submodule_name = submodule_parts[-1]
                            nodes.append(
                                ast.ImportFrom(
                                    module=package_name,
                                    names=[
                                        ast.alias(
                                            name=submodule_name, asname=alias.asname
                                        )
                                    ],
                                    level=level + 1,
                                )
                            )
                        else:
                            # Simple package import: "import typing_extensions" -> "from .. import typing_extensions"
                            nodes.append(
                                ast.ImportFrom(
                                    module="",
                                    names=[
                                        ast.alias(name=alias.name, asname=alias.asname)
                                    ],
                                    level=level + 1,
                                )
                            )
                except (AttributeError, IndexError):
                    # Fallback logic if path parsing fails
                    if "." in alias.name:
                        # Submodule import: "import typing_extensions.utils" -> "from ..typing_extensions import utils"
                        package_name, *submodule_parts = alias.name.split(".")
                        submodule_name = submodule_parts[-1]
                        nodes.append(
                            ast.ImportFrom(
                                module=package_name,
                                names=[
                                    ast.alias(name=submodule_name, asname=alias.asname)
                                ],
                                level=level + 1,
                            )
                        )
                    else:
                        # Simple package import: "import typing_extensions" -> "from .. import typing_extensions"
                        nodes.append(
                            ast.ImportFrom(
                                module="",
                                names=[ast.alias(name=alias.name, asname=alias.asname)],
                                level=level + 1,
                            )
                        )

        # Return single node or list of nodes
        if len(nodes) == 1:
            return nodes[0]
        elif len(nodes) > 1:
            return nodes
        else:
            # All imports were removed, return None
            return None

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.ImportFrom:
        """Rewrite 'from package import x' to use relative imports within vendor directory."""
        if node.module:
            package_name, *rest = node.module.split(".")
            if package_name == "vendor":
                package_name = rest[0]
            if package_name in self.vendored_packages:
                level = self._get_relative_import_level()
                if level == 0:
                    # File is outside vendor directory, use relative import from src/
                    new_module = f"vendor.{node.module}"
                    return ast.ImportFrom(module=new_module, names=node.names, level=1)
                else:
                    # File is inside vendor directory, use relative import within vendor
                    # Check if we're importing from the same package
                    try:
                        current_package = self.current_file_path.parent.name
                        if package_name == current_package:
                            # Importing from same package, need to determine the correct relative import
                            if node.module == package_name:
                                # Direct import from package: "from sentry_sdk import utils" -> "from . import utils"
                                return ast.ImportFrom(
                                    module=None, names=node.names, level=1
                                )
                            else:
                                # Submodule import: "from sentry_sdk.utils import AnnotatedValue" -> "from .utils import AnnotatedValue"
                                # Remove the package prefix and use relative import
                                submodule = node.module[
                                    len(package_name) + 1 :
                                ]  # +1 for the dot
                                return ast.ImportFrom(
                                    module=submodule, names=node.names, level=1
                                )
                        else:
                            # Importing from different package, use appropriate relative level
                            return ast.ImportFrom(
                                module=node.module, names=node.names, level=level + 1
                            )
                    except (AttributeError, IndexError):
                        # Fallback to original logic if path parsing fails
                        return ast.ImportFrom(
                            module=node.module, names=node.names, level=level + 1
                        )

        return node


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
