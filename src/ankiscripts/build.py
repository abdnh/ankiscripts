import argparse
import contextlib
import enum
import io
import json
import os
import shutil
import subprocess
from pathlib import Path
from textwrap import dedent
from typing import Any

import jsonschema

from ._utils import read_addon_json, run_script


def with_fixes_for_qt6(code: str) -> str:
    outlines = []
    qt_bad_types = [
        ".connect(",
    ]
    for original_line in code.splitlines():
        line = original_line
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


class QtVersion(enum.Enum):
    NONE = "none"
    ALL = "all"
    QT5 = "qt5"
    QT6 = "qt6"


class Builder:
    def __init__(self) -> None:
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--root",
            help="specify the root directory to use. defaults "
            "to the current directory.",
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
            choices=("qt5", "qt6", "all", "none"),
            help="build Qt designer forms of the specified version",
            default="all",
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
            help="Extra key-value pairs to add to manifest.json, which will override "
            "the same values in addon.json",
            metavar="JSON",
            required=False,
        )
        parser.add_argument(
            "--copy",
            "-c",
            help="Copy specified additional files/directories matching PATTERNS to"
            " the distribution. Patterns are relative to the root directory.",
            metavar="PATTERNS",
        )
        parser.add_argument(
            "--build-restart-script",
            help="Build the restart_anki.py script "
            "to the bin/restart_anki subdirectory."
            " Used to work around Windows permission issues when updating add-on "
            "that rely on some modules (see the abdnh/ankiutils project).",
            action="store_true",
        )

        args = parser.parse_args()

        self.root_dir = Path(args.root).resolve()
        self.src_dir = self.root_dir / "src"
        self.forms_dir = self.src_dir / str(args.forms_dir)
        self.build_dir = self.root_dir / "build"
        self.dist_dir = self.build_dir / "dist"
        self.build_type = args.type
        self.qt_version = QtVersion(str(args.qt).lower()) if args.qt else QtVersion.NONE
        self.extra_manifest: dict[str, Any] = (
            json.loads(args.manifest) if args.manifest else {}
        )
        self.consts = self._read_addon_json()
        self.should_write_consts = bool(args.consts)
        self.package_path = Path(args.out) if args.out else self._get_package_path()
        self.excludes: list[str] = list(args.exclude) if args.exclude else []
        self.excludes.extend(["meta.json", "py.typed", ".version"])
        self.copy_patterns = ["README.md", "LICENSE*", "CHANGELOG.md"]
        if args.copy:
            self.copy_patterns.extend(args.copy.split())
        self.should_build_restart_script = bool(args.build_restart_script)

    def _get_package_path(self) -> Path:
        name = self.consts["package"]
        if self.build_type == "ankiweb":
            name += "_ankiweb"
        if self.qt_version in (QtVersion.QT5, QtVersion.QT6):
            name += f"_{self.qt_version.value}"
        name += ".ankiaddon"

        return self.build_dir / name

    def _read_addon_json(self) -> dict[str, Any]:
        data = read_addon_json(self.root_dir)
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
            # `name` and `package` are not required for AnkiWeb builds,
            # but it doesn't hurt to add them
            if consts_copy.get("ankiweb_id"):
                manifest["package"] = consts_copy["ankiweb_id"]
            conflicts.append(consts_copy["package"])
        else:
            manifest["package"] = consts_copy["package"]
            if consts_copy.get("ankiweb_id"):
                conflicts.append(consts_copy["ankiweb_id"])
            manifest["mod"] = int(self.src_dir.stat().st_mtime)
        manifest["conflicts"] = conflicts

        # Remove values we copied so far from consts_copy
        # and add the rest as they are to the manifest
        copied = ("name", "package", "homepage", "conflicts", "ankiweb_id")
        for key in copied:
            consts_copy.pop(key, None)

        for key, value in consts_copy.items():
            manifest[key] = value

        with open(self.src_dir / "manifest.json", "w", encoding="utf-8") as file:
            file.write(json.dumps(manifest, ensure_ascii=False))

    def _generate_forms(self) -> None:
        if self.qt_version is QtVersion.NONE:
            return
        forms = list((self.root_dir / "designer").glob("*.ui"))
        if not forms:
            return
        self.forms_dir.mkdir(exist_ok=True)
        if self.qt_version is not QtVersion.ALL:
            if self.qt_version is QtVersion.QT5:
                from PyQt5.uic import compileUi  # noqa: PLC0415
            else:
                from PyQt6.uic import compileUi  # noqa: PLC0415
            for form in forms:
                buf = io.StringIO()
                with open(form, encoding="utf-8") as file:
                    compileUi(file, buf)
                name = form.stem + ".py"
                value = buf.getvalue()
                (self.forms_dir / name).write_text(value, encoding="utf-8")
        else:
            from PyQt6.uic import compileUi  # noqa: PLC0415

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
                        dedent(
                            f"""\
                            from typing import TYPE_CHECKING

                            from aqt.qt import qtmajor

                            if qtmajor > 5 or TYPE_CHECKING:
                                from .{form.stem}_qt6 import *
                            else:
                                from .{form.stem}_qt5 import *  # type: ignore
                            """
                        )
                    )

    def _write_consts(self) -> None:
        if not self.should_write_consts or not self.consts:
            return
        s = ""
        for name, val in self.consts.items():
            s += f"{name.upper()} = {repr(val)}\n"
        with open(self.src_dir / "consts.py", "w", encoding="utf-8") as file:
            file.write(s)

    def _write_version(self) -> None:
        try:
            version = subprocess.check_output(["git", "describe", "--tags"]).decode(
                "utf-8"
            )
        except subprocess.CalledProcessError:
            return
        with open(self.dist_dir / ".version", "w", encoding="utf-8") as file:
            file.write(version)

    def _copy_package(self) -> None:
        for dirpath, _, filenames in os.walk(self.src_dir):
            for filename in filenames:
                src_path = Path(dirpath) / filename
                if not any(src_path.match(pattern) for pattern in self.excludes):
                    dst_path = self.dist_dir / src_path.relative_to(self.src_dir)
                    dst_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_path, dst_path)

    def _copy_additional_files(self) -> None:
        for pattern in self.copy_patterns:
            for path in self.root_dir.glob(pattern):
                rel_path = self.dist_dir / path.relative_to(self.root_dir)
                if path.is_dir():
                    rel_path.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(path, rel_path, dirs_exist_ok=True)
                else:
                    with open(path, encoding="utf-8") as srcfile:
                        rel_path.write_text(srcfile.read(), encoding="utf-8")

    def _run_web_build(self) -> None:
        ts_dir = self.root_dir / "ts"
        if not ts_dir.exists():
            return
        with contextlib.chdir(ts_dir):
            subprocess.check_output([shutil.which("npm"), "run", "build"])

    def _run_custom_build_script(self) -> None:
        # Additional build logic can be specified in scripts/build.(sh|ps1)
        scripts_dir = self.root_dir / "scripts"
        run_script(scripts_dir, "build")

    def _build_restart_script(self) -> None:
        if not self.should_build_restart_script:
            return

        bin_dir = self.src_dir / "bin"
        bin_dir.mkdir(exist_ok=True)
        c_source = Path(__file__).parent / "restart_anki.c"
        exe_output = bin_dir / "restart_anki.exe"
        subprocess.check_output(
            [
                os.environ.get("CC", "clang"),
                "-O2",
                "-Wall",
                "-Wextra",
                "-o",
                str(exe_output),
                str(c_source),
                "-lshell32",
                "-ladvapi32",
            ]
        )

    def build(self) -> None:
        self._validate_config()
        if self.dist_dir.is_dir():
            shutil.rmtree(self.dist_dir)
        self.dist_dir.mkdir(exist_ok=True, parents=True)
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
        self._write_version()
        self._copy_additional_files()
        self._run_web_build()
        self._run_custom_build_script()
        self._build_restart_script()
        self._copy_package()
        self.package_path.unlink(missing_ok=True)
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


def main() -> None:
    builder = Builder()
    builder.build()


if __name__ == "__main__":
    main()
