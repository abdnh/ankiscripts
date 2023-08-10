import argparse
import io
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

import jsonschema


def with_fixes_for_qt6(code: str) -> str:
    outlines = []
    qt_bad_types = [
        ".connect(",
    ]
    for line in code.splitlines():
        for substr in qt_bad_types:
            if substr in line:
                line = line + "  # type: ignore"
                break
        line = line.replace(
            "QAction.PreferencesRole", "QAction.MenuRole.PreferencesRole"
        )
        line = line.replace("QAction.AboutRole", "QAction.MenuRole.AboutRole")
        outlines.append(line)
    return "\n".join(outlines)


def with_fixes_for_qt5(code: str) -> str:
    code = code.replace("Qt6", "Qt5")
    code = code.replace("QtGui.QAction", "QtWidgets.QAction")
    return code


class Builder:
    def __init__(self) -> None:
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--root",
            help="specify the root directory to use. defaults to the current directory.",
            default=".",
        )
        parser.add_argument(
            "--type",
            choices=("ankiweb", "package"),
            default="package",
            help="the type of the build zip to produce",
        )
        parser.add_argument(
            "--qt",
            choices=("qt5", "qt6", "all"),
            help="build Qt designer forms of the specified version",
        )
        parser.add_argument(
            "--consts",
            action="store_true",
            help="generate src/consts.py from addon.json",
        )
        parser.add_argument(
            "--forms-dir",
            help="generate forms in the specified path (relative to src)",
            default="forms",
        )
        parser.add_argument(
            "--exclude",
            "-e",
            help="Exclude paths relative to src/ matching given glob from package",
            action="append",
            metavar="PATTERN",
        )
        parser.add_argument(
            "--out",
            help="The output filename to use. If not specified, \
        the name will depend on the package name, the build type, and the Qt version",
            required=False,
        )
        parser.add_argument(
            "--manifest",
            help="Extra key-value pairs to add to manifest.json, which will override the same values in addon.json",
            metavar="JSON",
            required=False,
        )
        args = parser.parse_args()

        self.root_dir = Path(args.root).resolve()
        self.src_dir = self.root_dir / "src"
        self.forms_dir = self.src_dir / str(args.forms_dir)
        self.build_dir = self.root_dir / "build"
        self.dist_dir = self.build_dir / "dist"
        self.build_type = args.type
        self.qt_version = args.qt
        self.extra_manifest: Dict[str, Any] = (
            json.loads(args.manifest) if args.manifest else {}
        )
        self.consts = self._read_addon_json()
        self.package_path = Path(args.out) if args.out else self._get_package_path()
        self.excludes: List[str] = list(args.exclude) if args.exclude else []
        self.excludes.append("meta.json")

    def _get_package_path(self) -> Path:
        name = self.consts["package"]
        if self.build_type == "ankiweb":
            name += "_ankiweb"
        if self.qt_version and self.qt_version != "all":
            name += f"_{self.qt_version}"
        name += ".ankiaddon"

        return self.build_dir / name

    def _read_addon_json(self) -> Dict[str, Any]:
        with open(self.root_dir / "addon.json", encoding="utf-8") as file:
            data = json.load(file)
            for k, v in self.extra_manifest.items():
                data[k] = v
            return data

    def _validate_config(self) -> None:
        instance_path = self.src_dir / "config.json"
        schema_path = self.src_dir / "config.schema.json"
        if not instance_path.exists() or not schema_path.exists():
            return
        instance = json.loads(instance_path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.validate(instance=instance, schema=schema)

    def _write_manifest(self) -> None:
        consts_copy = self.consts.copy()
        manifest = {
            "name": consts_copy["name"],
        }
        if consts_copy.get("homepage"):
            manifest["homepage"] = consts_copy["homepage"]
        conflicts = consts_copy.get("conflicts", [])
        if self.build_type == "ankiweb":
            # `name` and `package` are not required for AnkiWeb builds, but it doesn't hurt to add them
            if consts_copy.get("ankiweb_id"):
                manifest["package"] = consts_copy["ankiweb_id"]
            conflicts.append(consts_copy["package"])
        else:
            manifest["package"] = consts_copy["package"]
            if consts_copy.get("ankiweb_id"):
                conflicts.append(consts_copy["ankiweb_id"])
        manifest["conflicts"] = conflicts
        manifest["mod"] = int(self.src_dir.stat().st_mtime)

        # Remove values we copied so far from consts_copy and add the rest as they are to the manifest
        copied = ("name", "package", "homepage", "conflicts", "ankiweb_id")
        for key in copied:
            consts_copy.pop(key, None)

        for key, value in consts_copy.items():
            manifest[key] = value

        with open(self.src_dir / "manifest.json", "w", encoding="utf-8") as file:
            file.write(json.dumps(manifest, ensure_ascii=False))

    def _generate_forms(self) -> None:
        if not self.qt_version:
            return
        forms = list((self.root_dir / "designer").glob("*.ui"))
        if not forms:
            return
        self.forms_dir.mkdir(exist_ok=True)
        if self.qt_version != "all":
            if self.qt_version == "qt5":
                from PyQt5.uic import compileUi
            elif self.qt_version == "qt6":
                from PyQt6.uic import compileUi  # type: ignore[no-redef]
            for form in forms:
                buf = io.StringIO()
                with open(form, encoding="utf-8") as file:
                    compileUi(file, buf)
                name = form.stem + ".py"
                value = buf.getvalue()
                (self.forms_dir / name).write_text(value, encoding="utf-8")
        else:
            from PyQt6.uic import compileUi  # type: ignore[no-redef]

            for form in forms:
                buf = io.StringIO()
                with open(form, encoding="utf-8") as file:
                    compileUi(file, buf)
                stock = buf.getvalue()
                for_qt6 = with_fixes_for_qt6(stock)
                for_qt5 = with_fixes_for_qt5(for_qt6)
                outpath = str(self.forms_dir / form.name)
                with open(
                    outpath.replace(".ui", "_qt5.py"), "w", encoding="utf-8"
                ) as file:
                    file.write(for_qt5)
                with open(
                    outpath.replace(".ui", "_qt6.py"), "w", encoding="utf-8"
                ) as file:
                    file.write(for_qt6)
                with open(outpath.replace(".ui", ".py"), "w", encoding="utf-8") as file:
                    file.write(
                        f"""\
    from aqt.qt import qtmajor

    if qtmajor > 5:
        from .{form.stem}_qt6 import *
    else:
        from .{form.stem}_qt5 import *  # type: ignore
    """
                    )

    def _write_consts(self) -> None:
        if not self.consts:
            return
        s = ""
        for name, val in self.consts.items():
            s += f"{name.upper()} = {repr(val)}\n"
        with open(self.src_dir / "consts.py", "w", encoding="utf-8") as file:
            file.write(s)

    def _copy_support_files(self) -> None:
        for filename in ["README.md", "LICENSE", "CHANGELOG.md"]:
            try:
                with open(self.root_dir / filename, "r", encoding="utf-8") as srcfile:
                    (self.dist_dir / filename).write_text(
                        srcfile.read(), encoding="utf-8"
                    )
            except FileNotFoundError:
                pass

    def build(self) -> None:
        self._validate_config()
        if self.dist_dir.is_dir():
            shutil.rmtree(self.dist_dir)
        to_remove = {"**/__pycache__"}
        for pattern in to_remove:
            for path in self.src_dir.glob(pattern):
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    os.remove(path)
        self._write_manifest()
        self._generate_forms()
        self._write_consts()
        shutil.copytree(
            self.src_dir, self.dist_dir, ignore=shutil.ignore_patterns(*self.excludes)
        )
        self._copy_support_files()
        subprocess.check_call(
            [
                "7z",
                "a",
                "-tzip",
                "-bso0",
                str(self.package_path),
                "-w",
                f"{str(self.dist_dir)}/.",
            ]
        )


if __name__ == "__main__":

    builder = Builder()
    builder.build()
