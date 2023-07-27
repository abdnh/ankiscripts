import argparse
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from time import time
from typing import Any, Dict, Optional

import jsonschema


def read_addon_json(args: argparse.Namespace) -> Dict[str, Any]:
    with open("addon.json", encoding="utf-8") as file:
        data = json.load(file)
        cmd_manifest = {}
        if args.manifest:
            cmd_manifest = json.loads(args.manifest)
        for k, v in cmd_manifest.items():
            data[k] = v
        return data


def write_manifest(buildtype: str, mod: int) -> None:
    consts_copy = consts.copy()
    manifest = {
        "name": consts_copy["name"],
    }
    if consts_copy.get("homepage"):
        manifest["homepage"] = consts_copy["homepage"]
    conflicts = consts_copy.get("conflicts", [])
    if buildtype == "ankiweb":
        # `name` and `package` are not required for AnkiWeb builds, but it doesn't hurt to add them
        if consts_copy.get("ankiweb_id"):
            manifest["package"] = consts_copy["ankiweb_id"]
        conflicts.append(consts_copy["package"])
    else:
        manifest["package"] = consts_copy["package"]
        if consts_copy.get("ankiweb_id"):
            conflicts.append(consts_copy["ankiweb_id"])
    manifest["conflicts"] = conflicts
    manifest["mod"] = mod

    # Remove values we copied so far from consts_copy and add the rest as they are to the manifest
    copied = ("name", "package", "homepage", "conflicts", "ankiweb_id")
    for key in copied:
        consts_copy.pop(key, None)

    for key, value in consts_copy.items():
        manifest[key] = value

    with open("src/manifest.json", "w", encoding="utf-8") as file:
        file.write(json.dumps(manifest, ensure_ascii=False))


def write_consts() -> None:
    s = ""
    for name, val in consts.items():
        s += f"{name.upper()} = {repr(val)}\n"
    with open("src/consts.py", "w", encoding="utf-8") as file:
        file.write(s)


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


def generate_forms(qt_version: Optional[str], forms_dir: Path) -> None:
    if not qt_version:
        return
    forms = list(Path("./designer").glob("*.ui"))
    if not forms:
        return
    forms_dir.mkdir(exist_ok=True)
    if qt_version != "all":
        if qt_version == "qt5":
            from PyQt5.uic import compileUi
        elif qt_version == "qt6":
            from PyQt6.uic import compileUi  # type: ignore[no-redef]
        for form in forms:
            buf = io.StringIO()
            with open(form, encoding="utf-8") as file:
                compileUi(file, buf)
            name = form.stem + ".py"
            value = buf.getvalue()
            (forms_dir / name).write_text(value, encoding="utf-8")
    else:
        from PyQt6.uic import compileUi  # type: ignore[no-redef]

        for form in forms:
            buf = io.StringIO()
            with open(form, encoding="utf-8") as file:
                compileUi(file, buf)
            stock = buf.getvalue()
            for_qt6 = with_fixes_for_qt6(stock)
            for_qt5 = with_fixes_for_qt5(for_qt6)
            outpath = str(forms_dir / form.name)
            with open(outpath.replace(".ui", "_qt5.py"), "w", encoding="utf-8") as file:
                file.write(for_qt5)
            with open(outpath.replace(".ui", "_qt6.py"), "w", encoding="utf-8") as file:
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


def get_package_name(args: argparse.Namespace) -> str:
    os.makedirs("build", exist_ok=True)
    if args.out:
        return args.out
    name = f"build/{consts['package']}"
    if args.type == "ankiweb":
        name += "_ankiweb"
    if args.qt and args.qt != "all":
        name += f"_{args.qt}"
    name += ".ankiaddon"

    return name


def dump_scripts() -> None:
    src_file = Path(__file__)
    dest_file = Path("./build.py").resolve()
    if src_file != dest_file:
        dest_file.write_text(src_file.read_text(encoding="utf-8"), encoding="utf-8")

    src_file = Path(os.path.join(os.path.dirname(__file__), "ankirun.py"))
    dest_file = Path("./run.py").resolve()
    if src_file != dest_file:
        dest_file.write_text(src_file.read_text(encoding="utf-8"), encoding="utf-8")


def most_recent_change(args: argparse.Namespace) -> float:
    excludes = args.exclude if args.exclude else []
    newest = 0.0
    paths = ["src", "addon.json"]
    if args.qt:
        paths.append("designer")
        for ui in Path("designer").glob("*.ui"):
            form_file = ui.with_suffix(".py")
            form_files = []
            if args.qt in ("qt5", "all"):
                form_files.append(form_file.with_stem(f"{form_file.stem}_qt5"))
            if args.qt in ("qt6", "all"):
                form_files.append(form_file.with_stem(f"{form_file.stem}_qt6"))
            for file in form_files:
                if not file.exists():
                    newest = time()
                    break
    for path in paths:
        if os.path.isfile(path):
            newest = max(newest, os.stat(path).st_mtime)
        else:
            for dirpath, dirs, fnames in os.walk(path, topdown=True):
                if path == "src":
                    # Apply exclude list
                    new_dirs = []
                    for d in dirs:
                        p = dirpath / Path(d)
                        if not any(p.match(e) for e in excludes):
                            new_dirs.append(d)
                    dirs[:] = new_dirs
                    new_fnames = []
                    for f in fnames:
                        p = Path(f)
                        if not any(p.match(e) for e in excludes):
                            new_fnames.append(f)
                    fnames[:] = new_fnames
                for fname in fnames:
                    p = Path(dirpath) / fname
                    newest = max(newest, os.stat(p).st_mtime)

    return newest


def needs_build(args: argparse.Namespace, name: str) -> bool:
    build_ts = last_build_time(name)
    mod_ts = most_recent_change(args)
    return mod_ts > build_ts


def last_build_time(name: str) -> float:
    try:
        return os.stat(name).st_mtime
    except Exception:
        return 0.0


def validate_config() -> None:
    instance_path = Path("src/config.json")
    schema_path = Path("src/config.schema.json")
    if not instance_path.exists() or not schema_path.exists():
        return
    instance = json.loads(instance_path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=instance, schema=schema)


parser = argparse.ArgumentParser()

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
    "--dump",
    action="store_true",
    help="dump this build script and the run script to the current directory for stand-alone source distributions",
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
validate_config()
buildtype = args.type
qt_version = args.qt
forms_dir = Path(f"./src/{args.forms_dir}")

if args.dump:
    dump_scripts()
consts = read_addon_json(args)
name = get_package_name(args)

if not needs_build(args, name):
    sys.exit(0)

to_remove = {"**/__pycache__"}
for pattern in to_remove:
    for path in Path("./src").glob(pattern):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            os.remove(path)

write_manifest(buildtype, int(Path("./src").stat().st_mtime))
generate_forms(qt_version, forms_dir)
if args.consts:
    write_consts()

excludes = args.exclude if args.exclude else []
excludes.append("meta.json")

dist_path = Path("build/dist")
if dist_path.is_dir():
    shutil.rmtree(dist_path)
shutil.copytree("src", dist_path, ignore=shutil.ignore_patterns(*excludes))

subprocess.check_call(
    [
        "7z",
        "a",
        "-tzip",
        "-bso0",
        name,
        "-w",
        "build/dist/.",
    ]
)
