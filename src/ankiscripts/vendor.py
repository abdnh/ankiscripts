from __future__ import annotations

import argparse
import difflib
import importlib
import importlib.util
import itertools
import logging
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Iterable, Sequence
from pathlib import Path
from types import ModuleType

import libcst as cst
from libcst.helpers import get_full_name_for_node

from ._utils import pip_install, read_addon_json, run_script

LIB_EXT_GLOBS = ("*.so", "*.pyd", "*.dylib")

addon_root = Path.cwd()
scripts_dir = addon_root / "scripts"


# Set up logging for import rewrites
def setup_import_rewrite_logging(enabled: bool = False) -> logging.Logger:
    """Set up a logger specifically for tracking import rewrites."""
    logger = logging.getLogger("import_rewriter")

    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()

    if not enabled:
        logger.setLevel(logging.CRITICAL + 1)  # Effectively disable logging
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        return logger

    logger.setLevel(logging.INFO)

    # Create file handler
    log_file = addon_root / "import_rewrites.log"
    handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    handler.setLevel(logging.INFO)

    # Create formatter
    formatter = logging.Formatter("%(asctime)s - %(message)s")
    handler.setFormatter(formatter)

    logger.addHandler(handler)
    logger.propagate = False  # Don't propagate to root logger

    return logger


# Global logger instance - will be configured based on command line args
import_logger = setup_import_rewrite_logging()


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


def get_vendor_hooks() -> ModuleType | None:
    module_path = scripts_dir / "vendor_hooks.py"
    if module_path.exists():
        spec = importlib.util.spec_from_file_location("vendor_hooks", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return None


class LibCSTImportTransformer(cst.CSTTransformer):
    """LibCST transformer to rewrite imports for vendored packages."""

    def __init__(
        self, vendored_packages: set[str], current_file_path: Path, vendor_path: Path
    ):
        self.vendored_packages = vendored_packages
        self.current_file_path = current_file_path
        self.vendor_path = vendor_path
        self.transformations_made = False
        # Track whether we've already added sys import
        # and package assignment in __init__.py
        self._added_sys_import = False
        self._added_package_assignment = False

    def _log_transformation(self, original: str, new: str, import_type: str) -> None:
        """Log an import transformation for debugging."""
        import_logger.info(
            f"FILE: {self.current_file_path}\n"
            f"  TYPE: {import_type}\n"
            f"  ORIGINAL: {original}\n"
            f"  NEW: {new}\n"
        )

    def _get_relative_import_level(self) -> int:
        """Calculate the number of dots needed for relative import."""
        return _get_relative_import_level(self.current_file_path, self.vendor_path)

    def _is_package_init_file(self) -> bool:
        """Check if the current file is a package's __init__.py file."""
        return self.current_file_path.name == "__init__.py"

    def _create_dotted_name(self, dotted_name: str) -> cst.Attribute | cst.Name:
        """Create a LibCST node for a dotted module name
        like 'sentry_sdk.integrations.dedupe'."""
        parts = dotted_name.split(".")
        if len(parts) == 1:
            return cst.Name(parts[0])

        # Build the attribute chain: a.b.c becomes
        # Attribute(Attribute(Name('a'), Name('b')), Name('c'))
        result: cst.Attribute | cst.Name = cst.Name(parts[0])
        for part in parts[1:]:
            result = cst.Attribute(value=result, attr=cst.Name(part))
        return result

    def _create_module_for_import_from(
        self, module_name: str
    ) -> cst.Attribute | cst.Name | None:
        """Create the module part for ImportFrom statements,
        handling dotted names properly."""
        if not module_name:
            return None

        parts = module_name.split(".")
        if len(parts) == 1:
            return cst.Name(parts[0])

        # For ImportFrom, we need to handle nested modules properly
        # If we have 'sentry_sdk.integrations.dedupe',
        # we want the module part to be 'integrations.dedupe'
        # after the package part 'sentry_sdk'
        return self._create_dotted_name(module_name)

    def _clean_import_names(
        self, names: cst.ImportStar | Sequence[cst.ImportAlias]
    ) -> Sequence[cst.ImportAlias] | cst.ImportStar:
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
                        # Let LibCST handle commas properly
                        comma=cst.MaybeSentinel.DEFAULT,
                    )
                )

        return cleaned_names

    def leave_SimpleStatementLine(  # noqa: PLR0912, PLR0915
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ) -> (
        cst.BaseStatement | cst.FlattenSentinel[cst.BaseStatement] | cst.RemovalSentinel
    ):
        """Handle import statements within simple statement lines."""
        new_statements: list[cst.CSTNode | None] = []

        for stmt in updated_node.body:
            if isinstance(stmt, cst.Import):
                # Handle 'import package' statements
                new_imports = []
                vendored_imports = []

                for name_item in stmt.names:
                    if isinstance(name_item, cst.ImportAlias):
                        module_name = get_full_name_for_node(name_item.name)
                        if module_name:
                            package_name = module_name.split(".")[0]
                            if package_name in self.vendored_packages:
                                vendored_imports.append(name_item)
                                # Log the import that will be transformed
                                original_import = f"import {module_name}"
                                if name_item.asname:
                                    alias_name = (
                                        get_full_name_for_node(name_item.asname.name)
                                        if name_item.asname.name
                                        else "unknown"
                                    )
                                    original_import += f" as {alias_name}"
                                import_logger.info(
                                    f"FILE: {self.current_file_path} -"
                                    " WILL TRANSFORM: {original_import}"
                                )
                            else:
                                new_imports.append(name_item)

                statements_to_add: list[cst.CSTNode] = []

                # Add non-vendored imports
                if new_imports:
                    statements_to_add.append(cst.Import(names=new_imports))

                # Add vendored imports as ImportFrom statements
                level = self._get_relative_import_level()
                for alias in vendored_imports:
                    module_name = get_full_name_for_node(alias.name)
                    if not module_name:
                        continue

                    original_import = f"import {module_name}"
                    if alias.asname:
                        original_import += (
                            f" as {get_full_name_for_node(alias.asname.name)}"
                        )

                    if level == 0:
                        # File is outside vendor directory
                        # For "import sentry_sdk.integrations.dedupe",
                        # create "from .vendor.sentry_sdk.integrations import dedupe"
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
                                # Submodule import:
                                # "import sentry_sdk.integrations.dedupe"
                                # -> "from .integrations import dedupe"
                                parts = module_name.split(".")
                                submodule_parts = parts[
                                    1:
                                ]  # Everything after the package name
                                imported_name = parts[-1]  # The final module name

                                # Special handling for __init__.py files
                                if self._is_package_init_file():
                                    # In __init__.py files, transform same-package
                                    # submodule imports
                                    # to relative imports but also make
                                    # the package name available
                                    # e.g., "import pycountry.db" becomes:
                                    # from . import db
                                    # import sys
                                    # pycountry = sys.modules[__name__]

                                    # Import the submodule
                                    statements_to_add.append(
                                        cst.ImportFrom(
                                            module=None,
                                            names=[
                                                cst.ImportAlias(
                                                    name=cst.Name(imported_name),
                                                    asname=None,
                                                )
                                            ],
                                            relative=[cst.Dot()],
                                        )
                                    )

                                    # Import sys if not already added
                                    if not self._added_sys_import:
                                        statements_to_add.append(
                                            cst.Import(
                                                names=[
                                                    cst.ImportAlias(
                                                        name=cst.Name("sys")
                                                    )
                                                ]
                                            )
                                        )
                                        self._added_sys_import = True

                                    # Make the package name available as a reference
                                    # to the current module
                                    # Only add this once per file
                                    if not self._added_package_assignment:
                                        package_assignment = cst.Assign(
                                            targets=[
                                                cst.AssignTarget(
                                                    target=cst.Name(package_name)
                                                )
                                            ],
                                            value=cst.Subscript(
                                                value=cst.Attribute(
                                                    value=cst.Name("sys"),
                                                    attr=cst.Name("modules"),
                                                ),
                                                slice=[
                                                    cst.SubscriptElement(
                                                        slice=cst.Index(
                                                            value=cst.Name("__name__")
                                                        )
                                                    )
                                                ],
                                            ),
                                        )
                                        statements_to_add.append(package_assignment)
                                        self._added_package_assignment = True

                                    self._log_transformation(
                                        f"import {module_name}",
                                        f"from . import {imported_name}; import sys; "
                                        f"{package_name} = sys.modules[__name__]",
                                        "SAME_PACKAGE_INIT_TRANSFORM",
                                    )
                                elif len(submodule_parts) == 1:
                                    # Simple submodule: sentry_sdk.client
                                    # -> from . import client
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
                                    # Nested submodule: sentry_sdk.integrations.dedupe
                                    # -> from .integrations import dedupe
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
                            elif self._is_package_init_file():
                                # In __init__.py, importing the package itself
                                # doesn't make sense
                                # and would create circular imports, so preserve as-is
                                statements_to_add.append(stmt)
                                self._log_transformation(
                                    f"import {module_name}",
                                    f"import {module_name} "
                                    "(preserved - circular import avoided)",
                                    "SAME_PACKAGE_INIT_CIRCULAR",
                                )
                            else:
                                # Simple package import: "import sentry_sdk"
                                # -> "from .. import sentry_sdk"
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
                        elif "." in module_name:
                            # Importing from different package
                            # Submodule import: "import requests.auth.basic"
                            # -> "from ..requests.auth import basic"
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
                                # requests.auth.basic ->
                                # from ..requests.auth import basic
                                submodule_path = ".".join(submodule_parts[:-1])
                                module_node = cst.Attribute(
                                    value=cst.Name(package_name),
                                    attr=self._create_dotted_name(submodule_path),  # type: ignore
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
                            # Simple package import: "import requests"
                            # -> "from .. import requests"
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
                    module_name = get_full_name_for_node(stmt.module)
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
                                    attr=self._create_dotted_name(module_name),  # type: ignore
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
                                        # Direct import: "from sentry_sdk import Hub"
                                        # -> "from . import Hub"
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
                                        # Submodule import:
                                        # "from sentry_sdk.integrations.dedupe import
                                        # something"
                                        # -> "from .integrations.dedupe import
                                        # something"
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
        with open(file_path, encoding="utf-8") as f:
            source_code = f.read()

        import_logger.info(f"PROCESSING FILE: {file_path}")

        # Log all import statements found in the file
        import_lines = [
            line.strip()
            for line in source_code.split("\n")
            if line.strip().startswith(("import ", "from "))
        ]
        if import_lines:
            import_logger.info(f"FOUND {len(import_lines)} IMPORT STATEMENTS:")
            for import_line in import_lines:
                import_logger.info(f"  {import_line}")
        else:
            import_logger.info("NO IMPORT STATEMENTS FOUND")

        # Parse the code with LibCST
        tree = cst.parse_module(source_code)

        # Transform the tree
        transformer = LibCSTImportTransformer(vendored_packages, file_path, vendor_path)
        new_tree = tree.visit(transformer)

        # Generate new code
        new_code = new_tree.code

        vendor_hooks = get_vendor_hooks()
        if vendor_hooks and hasattr(vendor_hooks, "transform_code"):
            import_logger.info(f"Transforming code with vendor hooks: {file_path}")
            new_code = vendor_hooks.transform_code(file_path, new_code)

        # Only write if content changed
        if new_code != source_code:
            import_logger.info(f"CHANGES DETECTED in {file_path}")

            # Log the diff for debugging
            diff_lines = list(
                difflib.unified_diff(
                    source_code.splitlines(keepends=True),
                    new_code.splitlines(keepends=True),
                    fromfile=f"original/{file_path.name}",
                    tofile=f"modified/{file_path.name}",
                    lineterm="",
                )
            )

            import_logger.info("DIFF:")
            for line in diff_lines:
                import_logger.info(line.rstrip())

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_code)
        else:
            import_logger.info(f"NO CHANGES NEEDED in {file_path}")

    except Exception as e:
        import_logger.exception(f"ERROR processing {file_path}")
        print(
            f"Warning: Could not rewrite imports in {file_path}: {e}", file=sys.stderr
        )


def rewrite_imports_in_vendor_dir(
    vendor_path: Path, enable_logging: bool = False
) -> None:
    """Rewrite imports in all Python files within the vendor directory using LibCST."""
    # Reconfigure the global logger based on the enable_logging parameter
    global import_logger
    import_logger = setup_import_rewrite_logging(enable_logging)

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
    import_logger.info("=" * 50)
    import_logger.info("IMPORT REWRITING COMPLETED")
    import_logger.info(f"Total files processed: {len(python_files)}")
    import_logger.info(f"Log file location: {addon_root / 'import_rewrites.log'}")
    import_logger.info("=" * 50)
    if enable_logging:
        print(f"Import rewrite log saved to: {addon_root / 'import_rewrites.log'}")


def default_python_versions() -> Iterable[str]:
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


def default_platforms_for_python_version(version: str) -> tuple[str, ...]:
    if int(version) <= 38:
        return ("win_amd64", "manylinux2014_x86_64", "macosx_10_7_x86_64")
    # https://github.com/ankitects/anki/blob/740528eaf913ff4bb9d112d494a10e84fd01365a/build/configure/src/python.rs#L141
    return (
        "manylinux_2_36_x86_64",
        "manylinux_2_36_aarch64",
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
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(
            f"Warning: Could not create universal binary for {output_lib}: {e}",
            file=sys.stderr,
        )
        return False
    else:
        print(f"Created universal binary: {output_lib}")
        return True


def install_libs(  # noqa: PLR0912, PLR0915
    python_versions: Iterable[str] | None = None,
    platforms: Iterable[str] | None = None,
    enable_logging: bool = False,
) -> None:
    python_exe = shutil.which("python")
    assert python_exe is not None

    if not python_versions:
        python_versions = default_python_versions()
    if not platforms:
        platforms = list(
            itertools.chain(
                *(
                    default_platforms_for_python_version(version)
                    for version in python_versions
                )
            )
        )

    addon_root = Path(".")
    vendor_path = addon_root / "src" / "vendor"
    vendor_path.mkdir(exist_ok=True)
    shutil.rmtree(vendor_path)
    min_python_version = min([(int(v[0]), int(v[1:])) for v in python_versions])
    pip_install(str(vendor_path), ".".join(str(p) for p in min_python_version))
    bin_path = vendor_path / "bin"
    if bin_path.exists():
        shutil.rmtree(bin_path)

    # Handle dependencies with C modules by downloading wheels for
    # all supported platforms and copying C libraries from them
    build_dir = addon_root / "build"
    build_dir.mkdir(exist_ok=True)
    for dist_info_dir in vendor_path.iterdir():
        if not dist_info_dir.is_dir() or not dist_info_dir.match("*.dist-info"):
            continue
        package_name = dist_info_dir.name.split("-")[0]
        try:
            with open(dist_info_dir / "top_level.txt", encoding="utf-8") as file:
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

                            # Process macOS libraries - prioritize universal2,
                            # then create universal binaries, then copy individually
            if "universal2" in wheel_libs_by_arch:
                # Use universal2 wheel if available
                # (already contains both architectures)
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
                # Copy remaining macOS libraries
                # that weren't processed for universal binaries
                for arch_libs in wheel_libs_by_arch.values():
                    for relative_path, lib_path in arch_libs.items():
                        dst = module_dir / relative_path
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy(lib_path, dst)

    # Rewrite imports in vendored packages to be relative to the vendor directory
    rewrite_imports_in_vendor_dir(vendor_path, enable_logging)

    # Additional vendoring logic (e.g. installing node modules)
    # can be specified in scripts/vendor.(sh|ps1)
    run_script(scripts_dir, "vendor")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--python-versions",
        default=",".join(default_python_versions()),
        help="A comma-separated list of Python versions to build "
        "platform-specific dependencies for (e.g. 38,39)",
    )
    parser.add_argument(
        "--platforms",
        help="A comma-separated list of platforms to build platform-specific "
        "dependencies for (e.g. win_amd64,manylinux_2_28_x86_64)",
    )
    parser.add_argument(
        "--enable-logging",
        action="store_true",
        help="Enable detailed logging of import rewrites to import_rewrites.log"
        " (default: disabled)",
    )

    args = parser.parse_args()

    install_libs(
        args.python_versions.split(","),
        args.platforms.split(",") if args.platforms else None,
        args.enable_logging,
    )
