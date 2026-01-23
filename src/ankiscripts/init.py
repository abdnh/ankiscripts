"""
Initializes a new add-on using my add-on template.
"""

import argparse
import re
import shutil
from pathlib import Path

from . import support, vendor
from ._utils import read_addon_json, symlink_addon, uv, write_addon_json

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
    default="",
)
parser.add_argument(
    "--min_point_version",
    help="minimum supported version",
    required=False,
)
parser.add_argument(
    "--github",
    help="link to github issues",
    required=False,
)
parser.add_argument(
    "--forums",
    help="link to the add-on page in Anki forums",
    required=False,
)

args = parser.parse_args()

support_links = {}
github_issues = None
if args.github:
    github_issues = args.github
elif args.homepage:
    github_issues = args.homepage
if github_issues:
    support_links["GITHUB_ISSUES"] = github_issues
if args.forums:
    support_links["FORUMS_PAGE"] = args.forums

# addon.json
addon_meta = read_addon_json(addon_root)
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
support_channels = {}
if args.github:
    support_channels["github"] = args.github
if args.forums:
    support_channels["forums"] = args.forums
addon_meta["support_channels"] = support_channels
write_addon_json(addon_root, addon_meta)

# pyproject.toml
pyproject_toml_path = addon_root / "pyproject.toml"
if pyproject_toml_path.exists():
    pyproject_toml = pyproject_toml_path.read_text(encoding="utf-8").replace(
        "anki_addon_template", args.package
    )
    pyproject_toml_path.write_text(pyproject_toml, encoding="utf-8")


# Readme
readme_path = addon_root / "README.md"
readme = readme_path.read_text(encoding="utf-8")
readme = re.sub(
    r".*?\[BEGINNING OF TEMPLATE\]",
    f"# {args.name}",
    readme,
    flags=re.MULTILINE | re.DOTALL,
)
readme = support.format(readme, support_links)
readme_path.write_text(readme, encoding="utf-8")

ankiweb_page_path = addon_root / "ankiweb_page.html"
ankiweb_readme = ankiweb_page_path.read_text(encoding="utf-8")
ankiweb_readme = support.format(ankiweb_readme, support_links)
ankiweb_page_path.write_text(ankiweb_readme, encoding="utf-8")

# Symlinking
symlink_addon(addon_root, args.package)

# Set up virtual environment and install dependencies
uv("sync")
uv("run", "--", "pre-commit", "install")
vendor.install_libs()

# Copy VS Code settings
vsode_dist = addon_root / ".vscode.dist"
vscode_path = addon_root / ".vscode"
shutil.copytree(vsode_dist, vscode_path)
