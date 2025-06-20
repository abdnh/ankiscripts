from __future__ import annotations

import argparse
import difflib
import itertools
import logging
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Iterable, Sequence
from pathlib import Path

import libcst as cst
from libcst.helpers import get_full_name_for_node

from ._utils import pip_install, read_addon_json, run_script, uv

LIB_EXT_GLOBS = ("*.so", "*.pyd", "*.dylib")

addon_root = Path.cwd()


def _get_addon_identifier(addon_root: Path) -> str:
    """Get a unique identifier for the add-on to use in module names."""
    # Try to get addon ID from addon.json
    addon_meta = read_addon_json(addon_root)
    package_name = addon_meta.get("package")
    if package_name:
        # Use the addon package name if available
        return str(package_name)
    # Fall back to directory name
    return addon_root.name


def _create_unique_module_name(original_name: str, addon_id: str) -> str:
    """Create a unique module name by appending the addon identifier."""
    return f"{original_name}_{addon_id}"


def _rename_vendored_packages(vendor_path: Path) -> dict[str, str]:
    """Rename vendored package directories to have unique names.

    Returns:
        Dictionary mapping original names to unique names
    """
    addon_id = _get_addon_identifier(addon_root)
    renames = {}

    print(f"Renaming vendored packages with addon ID: {addon_id}")

    for item in vendor_path.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            if not (
                item.name.endswith(".dist-info") or item.name.endswith(".egg-info")
            ):
                original_name = item.name
                unique_name = _create_unique_module_name(original_name, addon_id)

                if original_name != unique_name:
                    new_path = vendor_path / unique_name

                    # Avoid renaming if target already exists
                    if not new_path.exists():
                        print(f"  Renaming {original_name} -> {unique_name}")
                        item.rename(new_path)
                        renames[original_name] = unique_name
                    else:
                        print(
                            f"  Skipping {original_name} (target {unique_name}"
                            " already exists)"
                        )
                        renames[original_name] = unique_name
        elif item.is_file() and item.suffix == ".py" and not item.name.startswith("_"):
            # Handle single-file modules,
            # but skip test files and other non-package files
            original_name = item.stem

            # Skip test files and other non-package files
            if original_name.startswith("test_") or original_name in [
                "setup",
                "conftest",
            ]:
                continue

            unique_name = _create_unique_module_name(original_name, addon_id)

            if original_name != unique_name:
                new_path = vendor_path / f"{unique_name}.py"

                if not new_path.exists():
                    print(f"  Renaming {item.name} -> {unique_name}.py")
                    item.rename(new_path)
                    renames[original_name] = unique_name
                else:
                    print(
                        f"  Skipping {item.name} "
                        "(target {unique_name}.py already exists)"
                    )
                    renames[original_name] = unique_name

    return renames


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


class LibCSTImportTransformer(cst.CSTTransformer):
    """LibCST transformer to rewrite imports for vendored packages."""

    def __init__(
        self,
        vendored_packages: set[str],
        current_file_path: Path,
        vendor_path: Path,
        package_renames: dict[str, str] | None = None,
    ):
        self.vendored_packages = vendored_packages
        self.current_file_path = current_file_path
        self.vendor_path = vendor_path
        self.addon_id = _get_addon_identifier(addon_root)
        self.package_renames = package_renames or {}
        self.transformations_made = False

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

    def _get_renamed_module(self, module_name: str) -> str:
        """Get the renamed module name if it exists in package_renames."""
        return self.package_renames.get(module_name, module_name)

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

    def _add_aliases_for_renamed_packages(
        self, names: cst.ImportStar | Sequence[cst.ImportAlias], module_name: str
    ) -> Sequence[cst.ImportAlias] | cst.ImportStar:
        """Add aliases for renamed packages to maintain original names.

        For example, if importing 'h11_game_changer_ankiscripts'
        but it was originally 'h11_game_changer',
        this will add an alias:
        'from . import h11_game_changer_ankiscripts as h11_game_changer'
        """
        if isinstance(names, cst.ImportStar):
            return names

        if not names:
            return names

        # Create a reverse mapping from renamed to original
        reverse_renames = {v: k for k, v in self.package_renames.items()}

        # Get the package name from the module
        package_name = module_name.split(".")[0]

        # Debug logging
        import_logger.debug(
            "_add_aliases_for_renamed_packages: "
            f"module_name={module_name}, "
            f"package_name={package_name}"
        )
        import_logger.debug(f"reverse_renames={reverse_renames}")

        # Check if this is a renamed package (the module name is in the renamed values)
        if package_name in reverse_renames:
            original_name = reverse_renames[package_name]
            import_logger.debug(
                f"Found renamed package {package_name} -> original {original_name}"
            )

            # Process each import name
            new_names = []
            for name_item in names:
                if isinstance(name_item, cst.ImportAlias):
                    imported_name = get_full_name_for_node(name_item.name)
                    import_logger.debug(
                        f"Processing import: {imported_name}, "
                        "has_alias={name_item.asname is not None}"
                    )

                    # If importing the renamed package directly and no alias exists
                    if imported_name == package_name and name_item.asname is None:
                        # Add alias to original name:
                        # h11_addon_name -> h11
                        import_logger.debug(
                            f"Adding alias: {imported_name} as {original_name}"
                        )
                        new_names.append(
                            cst.ImportAlias(
                                name=name_item.name,
                                asname=cst.AsName(
                                    name=cst.Name(original_name),
                                    whitespace_before_as=cst.SimpleWhitespace(" "),
                                    whitespace_after_as=cst.SimpleWhitespace(" "),
                                ),
                                comma=cst.MaybeSentinel.DEFAULT,
                            )
                        )
                    else:
                        # Keep the original import as-is
                        new_names.append(
                            cst.ImportAlias(
                                name=name_item.name,
                                asname=name_item.asname,
                                comma=cst.MaybeSentinel.DEFAULT,
                            )
                        )

            return new_names
        else:
            import_logger.debug(f"No alias needed for {package_name}")

        # No renaming needed, return cleaned names
        return self._clean_import_names(names)

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
                            # First check for renamed packages
                            original_package = module_name.split(".")[0]
                            if original_package in self.package_renames:
                                # Apply renaming first
                                renamed_package = self.package_renames[original_package]
                                module_name = module_name.replace(
                                    original_package, renamed_package, 1
                                )

                            package_name = module_name.split(".")[0]
                            if package_name in self.vendored_packages:
                                vendored_imports.append((name_item, module_name))
                                # Log the import that will be transformed
                                original_import = (
                                    f"import {get_full_name_for_node(name_item.name)}"
                                )
                                if name_item.asname:
                                    alias_name = (
                                        get_full_name_for_node(name_item.asname.name)
                                        if name_item.asname.name
                                        else "unknown"
                                    )
                                    original_import += f" as {alias_name}"
                                import_logger.info(
                                    f"FILE: {self.current_file_path} - "
                                    f"WILL TRANSFORM: {original_import}"
                                )
                            else:
                                new_imports.append(name_item)

                statements_to_add: list[cst.CSTNode] = []

                # Add non-vendored imports
                if new_imports:
                    statements_to_add.append(cst.Import(names=new_imports))

                # Add vendored imports as ImportFrom statements (converted to relative)
                level = self._get_relative_import_level()
                for alias, module_name in vendored_imports:
                    original_import = f"import {get_full_name_for_node(alias.name)}"
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

                                if len(submodule_parts) == 1:
                                    # Simple submodule: sentry_sdk.client ->
                                    # from . import client
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
                            else:
                                # Simple package import: "import sentry_sdk"
                                # -> "from .. import sentry_sdk"
                                # (or sentry_sdk_game_changer as sentry_sdk)
                                dots = [cst.Dot()] * (level + 1)

                                # Check if we need to create
                                # an alias for a renamed package
                                # The original import was for the base name
                                # (e.g., sentry_sdk)
                                # but the package was renamed
                                # (e.g., to sentry_sdk_game_changer)
                                original_name = get_full_name_for_node(alias.name)
                                final_asname = alias.asname
                                # Default to the current (renamed) name
                                import_name = package_name

                                if (
                                    original_name != package_name
                                    and original_name in self.package_renames
                                    and alias.asname is None
                                ):
                                    # The original import name was renamed
                                    # to the current package
                                    # Import the renamed package
                                    # but alias it back to the original
                                    import_name = package_name  # Use renamed name
                                    final_asname = cst.AsName(
                                        name=cst.Name(original_name),
                                        whitespace_before_as=cst.SimpleWhitespace(" "),
                                        whitespace_after_as=cst.SimpleWhitespace(" "),
                                    )
                                    import_logger.info(
                                        "Creating same-package aliased import: "
                                        "from {'.' * len(dots)} "
                                        f"import {import_name} as {original_name}"
                                    )

                                statements_to_add.append(
                                    cst.ImportFrom(
                                        module=None,
                                        names=[
                                            cst.ImportAlias(
                                                name=cst.Name(import_name),
                                                asname=final_asname,
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
                            # or "import singlefile"
                            # -> "from .. import requests" or
                            # "from .. import singlefile_addon as singlefile"
                            dots = [cst.Dot()] * (level + 1)

                            # Check if this is a package/module that was renamed
                            # and needs an alias
                            final_asname = alias.asname
                            import_name = (
                                module_name  # The name to use in the import statement
                            )
                            original_name = get_full_name_for_node(alias.name)

                            # Check if the original package/module was renamed
                            if (
                                original_name in self.package_renames
                                and alias.asname is None
                            ):
                                # This original package/module was renamed
                                # (e.g., sentry_sdk -> sentry_sdk_game_changer or
                                #  typing_extensions -> typing_extensions_game_changer)
                                # We need to import the renamed package/module
                                # but alias it back to original
                                renamed_name = self.package_renames[original_name]
                                import_name = renamed_name
                                final_asname = cst.AsName(
                                    name=cst.Name(original_name),
                                    whitespace_before_as=cst.SimpleWhitespace(" "),
                                    whitespace_after_as=cst.SimpleWhitespace(" "),
                                )
                                import_logger.info(
                                    f"Creating aliased import: from {'.' * len(dots)} "
                                    f"import {renamed_name} as {original_name}"
                                )

                            statements_to_add.append(
                                cst.ImportFrom(
                                    module=None,
                                    names=[
                                        cst.ImportAlias(
                                            name=cst.Name(import_name),
                                            asname=final_asname,
                                        )
                                    ],
                                    relative=dots,
                                )
                            )

                if statements_to_add:
                    new_statements.extend(statements_to_add)

            elif isinstance(stmt, cst.ImportFrom):
                # Handle 'from package import x' statements
                if stmt.module and not stmt.relative:  # Handle absolute imports
                    module_name = get_full_name_for_node(stmt.module)
                    if module_name:
                        # First check for renamed packages
                        original_package = module_name.split(".")[0]
                        if original_package in self.package_renames:
                            # Apply renaming first
                            renamed_package = self.package_renames[original_package]
                            module_name = module_name.replace(
                                original_package, renamed_package, 1
                            )

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
                                        # "from sentry_sdk.int.dedupe import something"
                                        # -> "from .int.dedupe import something"
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

                                    # Check if we need to
                                    # add aliases for renamed packages
                                    names_with_aliases = (
                                        self._add_aliases_for_renamed_packages(
                                            stmt.names, module_name
                                        )
                                    )

                                    new_statements.append(
                                        cst.ImportFrom(
                                            module=self._create_module_for_import_from(
                                                module_name
                                            ),
                                            names=names_with_aliases,
                                            relative=dots,
                                        )
                                    )
                        else:
                            new_statements.append(stmt)
                    else:
                        new_statements.append(stmt)
                elif stmt.relative and not stmt.module:
                    # Handle relative imports like "from . import package_name"
                    # or "from .. import package_name"
                    # We need to check if any of the imported names
                    # are vendored packages that have been renamed
                    if isinstance(stmt.names, (list, tuple)):
                        # Check if any imported names are vendored packages
                        vendored_names = []
                        non_vendored_names = []

                        # Create reverse mapping from renamed to original names
                        reverse_renames = {
                            v: k for k, v in self.package_renames.items()
                        }

                        for name_item in stmt.names:
                            if isinstance(name_item, cst.ImportAlias):
                                imported_name = get_full_name_for_node(name_item.name)
                                if imported_name:
                                    # Check if this name is a renamed package
                                    # (exists in package_renames values)
                                    if imported_name in reverse_renames:
                                        # This is a renamed package,
                                        # get the original name
                                        original_name = reverse_renames[imported_name]
                                        import_logger.info(
                                            f"Found renamed import: {imported_name} "
                                            f"-> will alias as {original_name}"
                                        )
                                        vendored_names.append(
                                            (name_item, original_name, imported_name)
                                        )
                                    # Also check if this is a vendored package
                                    # that exists in our vendored packages set
                                    elif imported_name in self.vendored_packages:
                                        # This is a vendored package that
                                        # may need aliasing
                                        # Check if it's in our
                                        # vendored packages and was renamed
                                        if imported_name in reverse_renames:
                                            original_name = reverse_renames[
                                                imported_name
                                            ]
                                            vendored_names.append(
                                                (
                                                    name_item,
                                                    original_name,
                                                    imported_name,
                                                )
                                            )
                                        else:
                                            # Check if any original package
                                            # was renamed to this name
                                            found_original = None
                                            for (
                                                orig,
                                                renamed,
                                            ) in self.package_renames.items():
                                                if renamed == imported_name:
                                                    found_original = orig
                                                    break
                                            if found_original:
                                                vendored_names.append(
                                                    (
                                                        name_item,
                                                        found_original,
                                                        imported_name,
                                                    )
                                                )
                                            else:
                                                # It's a vendored package
                                                #  but doesn't need aliasing
                                                non_vendored_names.append(name_item)
                                    # Also check the old logic
                                    # for backward compatibility
                                    elif imported_name in self.package_renames:
                                        # This is a package that was renamed
                                        renamed_name = self.package_renames[
                                            imported_name
                                        ]
                                        vendored_names.append(
                                            (name_item, imported_name, renamed_name)
                                        )
                                    else:
                                        non_vendored_names.append(name_item)

                        # If we have vendored names, we need to transform them
                        if vendored_names:
                            # Add non-vendored imports as-is
                            if non_vendored_names:
                                new_statements.append(
                                    cst.ImportFrom(
                                        module=stmt.module,
                                        names=non_vendored_names,
                                        relative=stmt.relative,
                                    )
                                )

                            # Add vendored imports with renamed names and aliases
                            for (
                                original_alias,
                                original_name,
                                renamed_name,
                            ) in vendored_names:
                                # Check if the import already has an alias
                                if original_alias.asname is not None:
                                    # Import already has an alias, keep it as-is
                                    new_statements.append(
                                        cst.ImportFrom(
                                            module=stmt.module,
                                            names=[original_alias],
                                            relative=stmt.relative,
                                        )
                                    )
                                else:
                                    # Create import with renamed name
                                    # and alias back to original
                                    dots_str = "." * len(stmt.relative)
                                    import_logger.info(
                                        "Creating aliased import: "
                                        f"from {dots_str} import {renamed_name}"
                                        f" as {original_name}"
                                    )
                                    new_statements.append(
                                        cst.ImportFrom(
                                            module=stmt.module,
                                            names=[
                                                cst.ImportAlias(
                                                    name=cst.Name(renamed_name),
                                                    asname=cst.AsName(
                                                        name=cst.Name(original_name),
                                                        whitespace_before_as=cst.SimpleWhitespace(
                                                            " "
                                                        ),
                                                        whitespace_after_as=cst.SimpleWhitespace(
                                                            " "
                                                        ),
                                                    ),
                                                    comma=cst.MaybeSentinel.DEFAULT,
                                                )
                                            ],
                                            relative=stmt.relative,
                                        )
                                    )
                        else:
                            # No vendored packages, keep as-is
                            new_statements.append(stmt)
                    else:
                        # Not a list of names (maybe ImportStar), keep as-is
                        new_statements.append(stmt)
                else:
                    new_statements.append(stmt)
            else:
                new_statements.append(stmt)

        if new_statements != list(updated_node.body):
            return updated_node.with_changes(body=new_statements)

        return updated_node


def rewrite_imports_with_libcst(
    file_path: Path,
    vendored_packages: set[str],
    vendor_path: Path,
    package_renames: dict[str, str] | None = None,
) -> None:
    """Rewrite imports in a single Python file using LibCST."""
    if package_renames is None:
        package_renames = {}
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
        transformer = LibCSTImportTransformer(
            vendored_packages, file_path, vendor_path, package_renames
        )
        new_tree = tree.visit(transformer)

        # Generate new code
        new_code = new_tree.code

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
) -> dict[str, str]:
    """Rewrite imports in all Python files within the vendor directory using LibCST.

    Returns:
        Dictionary mapping original package names to unique names
    """
    # Reconfigure the global logger based on the enable_logging parameter
    global import_logger
    import_logger = setup_import_rewrite_logging(enable_logging)

    # First, rename vendored packages to have unique names
    package_renames = _rename_vendored_packages(vendor_path)

    # Get addon identifier for unique naming
    addon_id = _get_addon_identifier(addon_root)

    # Collect vendored packages (now with unique names)
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
    print(f"Package renames: {package_renames}")
    print(f"Using addon identifier: {addon_id}")

    python_files = list(vendor_path.rglob("*.py"))
    print(f"Rewriting imports in {len(python_files)} Python files...")

    for py_file in python_files:
        # Pass both renamed packages (for current directory structure)
        # and package_renames (for transformation)
        rewrite_imports_with_libcst(
            py_file, vendored_packages, vendor_path, package_renames
        )

    print("Import rewriting completed.")

    return package_renames


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
    assert python_exe is not None
    pip_install(str(reqs_path), str(vendor_path))
    reqs_path.unlink()
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
    scripts_dir = addon_root / "scripts"
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
        default=",".join(
            (
                *default_platforms_for_python_version("38"),
                *default_platforms_for_python_version("39"),
            )
        ),
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
        args.platforms.split(","),
        args.enable_logging,
    )
