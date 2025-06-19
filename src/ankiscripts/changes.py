import re
import sys
from pathlib import Path

changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")

version = sys.argv[1].replace("refs/tags/", "")
m = re.search(rf"\[{re.escape(version)}\].*?\n(.*)", changelog, re.DOTALL)
if not m:
    sys.exit(0)
else:
    changes = m.group(1).split("\n## ")[0]
    print(changes)
