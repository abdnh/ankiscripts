"""
This script runs Anki with some useful env variables set for debugging.
Anki arguments can be passed.
"""

import os
from pathlib import Path

import aqt

from ._utils import read_addon_json, symlink_addon

addon_root = Path.cwd()
package = read_addon_json(addon_root).get("package") or addon_root.name
symlink_addon(addon_root, package)

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

aqt.run()
