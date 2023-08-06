"""
This script runs Anki with the base folder `ankidata` in the current directory and some useful env variables set for debugging.
"""
import json
import os
import subprocess
from pathlib import Path

from ._utils import symlink_addon

addon_root = Path.cwd()
with open(addon_root / "addon.json", "r", encoding="utf-8") as file:
    package = json.load(file)["package"]
    symlink_addon(addon_root, package)

env = os.environ.copy()
# Run debugger on uncaught exceptions (https://addon-docs.ankiweb.net/debugging.html#pdb)
env["DEBUG"] = "1"
# For debugging webviews (https://addon-docs.ankiweb.net/debugging.html#webviews)
env["QTWEBENGINE_REMOTE_DEBUGGING"] = "8080"
# Set static port to access pages served over localhost
env["ANKI_API_PORT"] = "40000"
# Logging
env["ANKIDEV"] = "1"
# Disable Qt5 compatibility
env["DISABLE_QT5_COMPAT"] = "1"
# Print SQL statements
# env["TRACESQL"] = "1"
subprocess.check_call(["anki", "-b", "ankidata"], env=env)
