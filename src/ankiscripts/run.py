"""
This script runs Anki with some useful env variables set for debugging.
Anki arguments can be passed.
"""

import os
from pathlib import Path

from ._utils import read_addon_json, run_npm, symlink_addon


def run_sveltekit_server(package: str) -> None:
    if not os.environ.get(f"{package.upper()}_HMR", ""):
        return

    run_npm("run", "dev", wait=False, cwd=Path.cwd() / "ts")


def main() -> None:
    addon_root = Path.cwd()
    package = read_addon_json(addon_root).get("package") or addon_root.name
    symlink_addon(addon_root, package)
    run_sveltekit_server(package)

    env = os.environ
    # Run debugger on uncaught exceptions (https://addon-docs.ankiweb.net/debugging.html#pdb)
    env["DEBUG"] = "1"
    # For debugging webviews (https://addon-docs.ankiweb.net/debugging.html#webviews)
    env["QTWEBENGINE_REMOTE_DEBUGGING"] = "8080"
    # https://github.com/ankitects/anki/commit/db031424c28ecbb84ae7f30564719aca0c07a354
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = "--remote-allow-origins=http://localhost:8080"
    # Set static port to access pages served over localhost
    env["ANKI_API_PORT"] = "40000"
    # Logging
    env["ANKIDEV"] = "1"
    # Disable Qt5 compatibility
    env["DISABLE_QT5_COMPAT"] = "1"
    # Print SQL statements
    # env["TRACESQL"] = "1"
    # Sentry
    env["SENTRY_ENVIRONMENT"] = "development"

    import aqt  # noqa: E402, PLC0415

    aqt.run()


if __name__ == "__main__":
    main()
