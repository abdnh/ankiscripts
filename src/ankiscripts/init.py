"""
Initializes a new add-on using my add-on template.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import types
import venv
from pathlib import Path

from . import support, vendor
from ._utils import add_exe_suffix, pip_install, symlink_addon
from .update_deps import update_deps

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
    json.dump(addon_meta, file, ensure_ascii=False, indent=4)

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
    f"# {args.name}\n\nTODO",
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

# Create venv and install deps


class MyEnvBuilder(venv.EnvBuilder):
    def post_setup(self, context: types.SimpleNamespace) -> None:
        update_deps(context.bin_path)
        precommit_exe = add_exe_suffix(os.path.join(context.bin_path, "pre-commit"))
        subprocess.check_call(
            [
                precommit_exe,
                "install",
            ]
        )
        vendor.install_libs()
        return super().post_setup(context)


venv_path = addon_root / "venv"
env_builder = MyEnvBuilder(with_pip=True, clear=True)
env_builder.create(venv_path)

# Copy VS Code settings
vsode_dist = addon_root / ".vscode.dist"
vscode_path = addon_root / ".vscode"
shutil.copytree(vsode_dist, vscode_path)
