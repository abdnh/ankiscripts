import subprocess
import os

env = os.environ.copy()
# Run debugger on uncaught exceptions (https://addon-docs.ankiweb.net/debugging.html#pdb)
env["DEBUG"] = "1"
# For debugging webviews (https://addon-docs.ankiweb.net/debugging.html#webviews)
env["QTWEBENGINE_REMOTE_DEBUGGING"] = "8080"
# TODO: more vars to define? see dev docs

subprocess.check_call(["anki-console", "-b", "ankiprofile"], env=env)
