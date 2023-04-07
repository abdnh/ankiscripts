"""
Initializes a new add-on using my add-on template.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

addon_root = Path(".")

parser = argparse.ArgumentParser()

parser.add_argument(
    "--name",
    help="add-on name",
    required=True,
)
parser.add_argument(
    "--package",
    help="add-on package",
    required=True,
)

parser.add_argument(
    "--ankiweb_id",
    help="AnkiWeb ID",
    required=False,
)
parser.add_argument(
    "--homepage",
    help="add-on homepage",
    required=False,
)
parser.add_argument(
    "--min_point_version",
    help="minimum supported version",
    required=False,
)

args = parser.parse_args()

# addon.json
addon_json_path = addon_root / "addon.json"
with open(addon_json_path, "r", encoding="utf-8") as file:
    addon_meta = json.load(file)
addon_meta["name"] = args.name
addon_meta["package"] = args.package
if args.ankiweb_id:
    addon_meta["ankiweb_id"] = args.ankiweb_id
elif "ankiweb_id" in addon_meta:
    del addon_meta["ankiweb_id"]
if args.homepage:
    addon_meta["homepage"] = args.homepage
if args.min_point_version:
    addon_meta["min_point_version"] = args.min_point_version
with open(addon_json_path, "w", encoding="utf-8") as file:
    json.dump(addon_meta, file, ensure_ascii=False)

# Readme
readme_path = addon_root / "README.md"
readme = readme_path.read_text(encoding="utf-8")
readme = re.sub(
    r".*?\[BEGINNING OF TEMPLATE\]",
    f"# {args.name}\n\nTODO",
    readme,
    flags=re.MULTILINE | re.DOTALL,
)
readme_path.write_text(readme, encoding="utf-8")

# Symlinking
src_path = addon_root / "src"
install_path = addon_root / "ankidata" / "addons21" / str(args.package)
install_path.parent.mkdir(parents=True, exist_ok=True)
if sys.platform.startswith("win32"):
    subprocess.run(
        'mklink /J "{}" "{}"'.format(str(install_path), str(src_path)),
        shell=True,
        check=True,
    )
else:
    os.link(src_path, install_path)
