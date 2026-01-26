"""
Initializes a new add-on using my add-on template.
"""

import argparse
import shutil
from pathlib import Path

import copier

from . import vendor
from ._utils import symlink_addon, uv


def replace_in_path(path: Path, old: str, new: str) -> None:
    if not path.exists():
        return
    encoding = "utf-8"
    text = path.read_text(encoding=encoding).replace(old, new)
    path.write_text(text, encoding=encoding)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--destination", help="folder to initialize add-on in", default="."
    )
    args = parser.parse_args()
    addon_root = Path(args.destination)
    copier.run_copy(
        "https://github.com/abdnh/anki-addon-template",
        data=None,
        vcs_ref="HEAD",
        unsafe=True,
    )

    uv("sync", "--dev")
    uv("run", "--", "prek", "install", "--install-hooks")
    vendor.install_libs()

    shutil.copytree(addon_root / ".vscode.dist", addon_root / ".vscode")

    symlink_addon(addon_root, args.package)


if __name__ == "__main__":
    main()
