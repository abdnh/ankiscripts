from __future__ import annotations

import argparse
import difflib
import filecmp
import os
import re
import shutil
import tempfile
import urllib
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import questionary


@dataclass
class DirCmpReport:
    left_only: list[Path]
    diff_files: list[Path]


def _collect_report(
    report: DirCmpReport, parent: Path, dir_cmp: filecmp.dircmp
) -> None:
    report.left_only.extend(parent / p for p in dir_cmp.left_only)
    report.diff_files.extend(parent / p for p in dir_cmp.diff_files)
    for subdir, subdir_cmp in dir_cmp.subdirs.items():
        _collect_report(report, parent / subdir, subdir_cmp)


def sorted_paths(paths: Iterable[Path]) -> list[Path]:
    return sorted(paths, key=lambda p: (len(p.parents), str(p)))


def print_tree(root: Path, paths: list[Path]) -> None:
    for path in paths:
        print("  ●", str(path.relative_to(root)))


def compare(a: str | Path, b: str | Path) -> None:
    ignore = filecmp.DEFAULT_IGNORES
    ignore.extend(
        [
            ".vscode",
            "venv",
            ".mypy_cache",
            "ankidata",
            "build",
            "manifest.json",
            "meta.json",
            "forms",
            "user_files",
            "vendor",
            "node_modules",
            "TODO.md",
        ]
    )
    result = filecmp.dircmp(a, b, ignore)
    report = DirCmpReport([], [])
    template_root = Path(result.left)
    addon_root = Path(result.right)
    _collect_report(report, template_root, result)
    if report.left_only:
        report.left_only = sorted_paths(report.left_only)
        chosen_paths: list[Path] = questionary.checkbox(
            "These files don't exist in the add-on. Which to copy?",
            choices=[
                questionary.Choice(
                    title=str(p.relative_to(template_root)), value=p, checked=False
                )
                for p in report.left_only
            ],
        ).ask()
        for path in chosen_paths:
            dest_path = str(result.right) / path.relative_to(template_root)
            if path.is_dir():
                shutil.copytree(path, dest_path, dirs_exist_ok=True)
            else:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "r", encoding="utf-8") as file:
                    dest_path.write_text(file.read(), encoding="utf-8")

    if report.diff_files:
        report.diff_files = sorted_paths(report.diff_files)
        print("Differing files:")
        print_tree(template_root, report.diff_files)
        yes = questionary.confirm("Write diff files?").ask()
        if not yes:
            return
        for path in report.diff_files:
            path = path.relative_to(template_root)
            with open((template_root / path), "r", encoding="utf-8") as file:
                lines1 = file.readlines()
            with open((addon_root / path), "r", encoding="utf-8") as file:
                lines2 = file.readlines()
            diff_path = addon_root / path.with_suffix(f"{path.suffix}.diff")
            with open(diff_path, "w", encoding="utf-8") as file:
                file.writelines(
                    difflib.unified_diff(
                        lines1, lines2, f"template/{path}", f"{addon_root.name}/{path}"
                    )
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", help="Add-on root", required=False, default=".")
    parser.add_argument(
        "--template",
        help="URL or file path to get add-on template from",
        required=False,
        default="https://github.com/abdnh/anki-addon-template/archive/refs/heads/master.zip",
    )
    args = parser.parse_args()
    addon_path = Path(args.root)
    template_location = str(args.template)

    if re.match("https?://", template_location):
        with tempfile.TemporaryDirectory() as tempdir:
            print(f"Downloading template from {template_location}...")
            file, msg = urllib.request.urlretrieve(template_location)
            with zipfile.ZipFile(file) as archive:
                archive.extractall(tempdir)
            files = os.listdir(tempdir)
            if len(files) == 1 and os.path.isdir(os.path.join(tempdir, files[0])):
                template_location = os.path.join(tempdir, files[0])
            else:
                template_location = tempdir

            compare(template_location, addon_path)
    else:
        compare(template_location, addon_path)
