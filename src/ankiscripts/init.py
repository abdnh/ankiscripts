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
    parser.add_argument(
        "--source",
        help="source URL/folder to copy template from",
        default="https://github.com/abdnh/anki-addon-template",
    )
    args = parser.parse_args()
    addon_root = Path(args.destination)
    worker = copier.run_copy(
        args.source,
        data=None,
        vcs_ref="HEAD",
        unsafe=True,
        dst_path=addon_root,
    )

    uv("sync", "--dev", cwd=addon_root)
    uv(
        "run",
        "--",
        "prek",
        "install",
        "--install-hooks",
        "--config",
        ".pre-commit-config.yaml",
        cwd=addon_root,
    )
    vendor.install_libs(addon_root=addon_root)
    shutil.copytree(
        addon_root / ".vscode.dist", addon_root / ".vscode", dirs_exist_ok=True
    )
    symlink_addon(addon_root, worker.answers.user["package_name"])


if __name__ == "__main__":
    main()
