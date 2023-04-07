"""
This script runs Anki with the base folder `ankidata` in the current directory and some useful env variables set for debugging.
This is intended for testing the add-on after building and copying src/ to ankidata/addons21 or symlinking it.
"""
import os
import subprocess

env = os.environ.copy()
# Run debugger on uncaught exceptions (https://addon-docs.ankiweb.net/debugging.html#pdb)
env["DEBUG"] = "1"
# For debugging webviews (https://addon-docs.ankiweb.net/debugging.html#webviews)
env["QTWEBENGINE_REMOTE_DEBUGGING"] = "8080"
# Logging
env["ANKIDEV"] = "1"
# Disable Qt5 compatibility
env["DISABLE_QT5_COMPAT"] = "1"
# Print SQL statements
# env["TRACESQL"] = "1"
subprocess.check_call(["anki", "-b", "ankidata"], env=env)
