from __future__ import annotations

import difflib
import importlib
import importlib.util
import logging
import os
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import libcst as cst
from libcst.helpers import get_full_name_for_node

addon_root = Path.cwd()
scripts_dir = addon_root / "scripts"


def setup_import_rewrite_logging(enabled: bool = False) -> logging.Logger:
    logger = logging.getLogger("import_rewriter")

    logger.handlers.clear()

    if not enabled:
        logger.setLevel(logging.CRITICAL + 1)
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        return logger

    logger.setLevel(logging.INFO)

    log_file = addon_root / "import_rewrites.log"
    handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    handler.setLevel(logging.INFO)

    formatter = logging.Formatter("%(asctime)s - %(message)s")
    handler.setFormatter(formatter)

    logger.addHandler(handler)
    logger.propagate = False

    return logger


import_logger = setup_import_rewrite_logging()


def _get_relative_import_level(current_file_path: Path, vendor_path: Path) -> int:
    try:
        rel_path = current_file_path.relative_to(vendor_path)
        return len(rel_path.parts)
    except ValueError:
        # File is outside vendor directory
        # We assume it's one level deep inside a sibling directory
        # (e.g. src/proto/backend_pb2.py)
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

    def _handle_import_statement(self, stmt: cst.Import) -> list[cst.CSTNode]:  # noqa: PLR0912, PLR0915
        new_statements: list[cst.CSTNode] = []
        new_imports = []
        vendored_imports: list[cst.ImportAlias] = []

        for name_item in stmt.names:
            module_name = get_full_name_for_node(name_item.name)
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

        # Add non-vendored imports
        if new_imports:
            new_statements.append(cst.Import(names=new_imports))

        # Add vendored imports as ImportFrom statements
        level = self._get_relative_import_level()
        for alias in vendored_imports:
            module_name = get_full_name_for_node(alias.name)

            original_import = f"import {module_name}"
            if alias.asname:
                original_import += f" as {get_full_name_for_node(alias.asname.name)}"

            if level == 0:
                if "." in module_name:
                    parts = module_name.split(".")
                    package_part = parts[0]  # sentry_sdk
                    submodule_parts = parts[1:]  # ['integrations', 'dedupe']
                    imported_name = parts[-1]  # dedupe

                    # Create vendor.sentry_sdk.integrations
                    vendor_module = cst.Attribute(
                        value=cst.Name("vendor"), attr=cst.Name(package_part)
                    )
                    for part in submodule_parts[:-1]:  # All except the last part
                        vendor_module = cst.Attribute(
                            value=vendor_module, attr=cst.Name(part)
                        )

                    new_statements.append(
                        cst.ImportFrom(
                            module=vendor_module,
                            names=[
                                cst.ImportAlias(
                                    name=cst.Name(imported_name),
                                    asname=alias.asname,
                                )
                            ],
                            relative=[cst.Dot(), cst.Dot()],
                        )
                    )
                else:
                    # Simple import like "import sentry_sdk"
                    new_statements.append(
                        cst.ImportFrom(
                            module=cst.Name(value="vendor"),
                            names=[
                                cst.ImportAlias(
                                    name=cst.Name(module_name),
                                    asname=alias.asname,
                                )
                            ],
                            relative=[cst.Dot(), cst.Dot()],
                        )
                    )
                continue

            current_package = self.current_file_path.parent.name
            package_name = module_name.split(".")[0]
            if package_name == current_package:
                # Importing from same package
                if "." in module_name:
                    # Submodule import:
                    # "import sentry_sdk.integrations.dedupe"
                    # -> "from .integrations import dedupe"
                    parts = module_name.split(".")
                    submodule_parts = parts[1:]  # Everything after the package name
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
                        new_statements.append(
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
                            new_statements.append(
                                cst.Import(
                                    names=[cst.ImportAlias(name=cst.Name("sys"))]
                                )
                            )
                            self._added_sys_import = True

                        # Make the package name available as a reference
                        # to the current module
                        # Only add this once per file
                        if not self._added_package_assignment:
                            package_assignment = cst.Assign(
                                targets=[
                                    cst.AssignTarget(target=cst.Name(package_name))
                                ],
                                value=cst.Subscript(
                                    value=cst.Attribute(
                                        value=cst.Name("sys"),
                                        attr=cst.Name("modules"),
                                    ),
                                    slice=[
                                        cst.SubscriptElement(
                                            slice=cst.Index(value=cst.Name("__name__"))
                                        )
                                    ],
                                ),
                            )
                            new_statements.append(package_assignment)
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
                        new_statements.append(
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
                        new_statements.append(
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
                    new_statements.append(stmt)
                    self._log_transformation(
                        f"import {module_name}",
                        f"import {module_name} (preserved - circular import avoided)",
                        "SAME_PACKAGE_INIT_CIRCULAR",
                    )
                else:
                    # Simple package import: "import sentry_sdk"
                    # -> "from .. import sentry_sdk"
                    dots = [cst.Dot()] * level
                    new_statements.append(
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
                submodule_path = ".".join(submodule_parts[:-1])
                if len(submodule_parts) == 1:
                    # requests.auth -> from ..requests import auth
                    module_node = cst.Name(package_name)
                else:
                    # requests.auth.basic ->
                    # from ..requests.auth import basic
                    module_node = cst.Attribute(
                        value=cst.Name(package_name),
                        attr=self._create_dotted_name(submodule_path),  # type: ignore
                    )
                # TODO: should we make the base package import available?
                # new_statements.append(
                #     cst.ImportFrom(
                #         module=None,
                #         names=[
                #             cst.ImportAlias(
                #                 name=cst.Name(package_name),
                #             )
                #         ],
                #         relative=[cst.Dot()] * level,
                #     )
                # )
                new_statements.append(
                    cst.ImportFrom(
                        module=module_node,
                        names=[
                            cst.ImportAlias(
                                name=cst.Name(imported_name),
                                asname=alias.asname,
                            )
                        ],
                        relative=[cst.Dot()] * level,
                    )
                )
            else:
                # Simple package import: "import requests"
                # -> "from .. import requests"
                dots = [cst.Dot()] * level
                new_statements.append(
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

        return new_statements

    def _handle_importfrom_statement(self, stmt: cst.ImportFrom) -> list[cst.CSTNode]:
        if not stmt.module or stmt.relative:
            return [stmt]
        module_name = get_full_name_for_node(stmt.module)
        package_name = module_name.split(".")[0]
        if package_name == "vendor" and len(module_name.split(".")) > 1:
            package_name = module_name.split(".")[1]
        if package_name not in self.vendored_packages:
            return [stmt]

        new_statements: list[cst.CSTNode] = []
        level = self._get_relative_import_level()
        if level == 0:
            vendor_module = cst.Attribute(
                value=cst.Name("vendor"),
                attr=self._create_dotted_name(module_name),  # type: ignore
            )
            new_statements.append(
                cst.ImportFrom(
                    module=vendor_module,
                    names=self._clean_import_names(stmt.names),
                    relative=[cst.Dot(), cst.Dot()],
                )
            )
            return new_statements

        current_package = self.current_file_path.parent.name
        if package_name == current_package:
            # Importing from same package
            if module_name == package_name:
                # Direct import: "from sentry_sdk import Hub"
                # -> "from . import Hub"
                new_statements.append(
                    cst.ImportFrom(
                        module=None,
                        names=self._clean_import_names(stmt.names),
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
                        module=self._create_module_for_import_from(submodule),
                        names=self._clean_import_names(stmt.names),
                        relative=[cst.Dot()],
                    )
                )
        else:
            # Importing from different package
            dots = [cst.Dot()] * level
            new_statements.append(
                cst.ImportFrom(
                    module=self._create_module_for_import_from(module_name),
                    names=self._clean_import_names(stmt.names),
                    relative=dots,
                )
            )
        return new_statements

    def leave_SimpleStatementLine(
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ) -> cst.SimpleStatementLine:
        new_statements: list[cst.CSTNode | None] = []

        for stmt in updated_node.body:
            if isinstance(stmt, cst.Import):
                new_statements.extend(self._handle_import_statement(stmt))
            elif isinstance(stmt, cst.ImportFrom):
                new_statements.extend(self._handle_importfrom_statement(stmt))
            else:
                new_statements.append(stmt)

        if new_statements != list(updated_node.body):
            return updated_node.with_changes(body=new_statements)

        return updated_node


def rewrite_imports_with_libcst(
    file_path: Path, vendored_packages: set[str], vendor_path: Path
) -> None:
    """Rewrite imports in a single Python file using LibCST."""
    # try:
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

    # Force pure-Python Protobuf backend to avoid conflicts with Anki's version
    if file_path.name == "api_implementation.py" and any(
        p.name == "protobuf" for p in file_path.parents
    ):
        new_code = new_code.replace(
            "_implementation_type = None", "_implementation_type = 'python'"
        )

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


def rewrite_imports_in_vendor_dir(
    vendor_path: Path, src_path: Path | None = None
) -> None:
    """Rewrite imports in all Python files within the vendor directory using LibCST."""
    # Reconfigure the global logger
    global import_logger
    enable_logging = os.environ.get("ANKISCRIPTS_LOGGING", "") == "1"
    import_logger = setup_import_rewrite_logging(enable_logging)

    if not src_path:
        src_path = vendor_path
    if src_path.parent != vendor_path.parent:
        raise Exception("src_path and vendor_path must be in the same parent directory")  # noqa: TRY002, TRY003
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

    python_files = list(src_path.rglob("*.py"))
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
