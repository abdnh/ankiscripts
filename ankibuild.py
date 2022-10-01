import sys
import os
from pathlib import Path
import argparse
from time import time
from typing import Optional, Any, Dict
import json
import io
import subprocess
import shutil

import jsonschema


def read_addon_json() -> Dict[str, Any]:
    return json.load(open("addon.json"))


def write_manifest(buildtype: str) -> None:
    manifest = {
        "name": consts["name"],
    }
    if consts.get("homepage"):
        manifest["homepage"] = consts["homepage"]
    conflicts = consts.get("conflicts", [])
    if buildtype == "ankiweb":
        # `name` and `package` are not required for AnkiWeb builds, but it doesn't hurt to add them
        if consts.get("ankiweb_id"):
            manifest["package"] = consts["ankiweb_id"]
        conflicts.append(consts["package"])
    else:
        manifest["package"] = consts["package"]
        if consts.get("ankiweb_id"):
            conflicts.append(consts["ankiweb_id"])
    manifest["conflicts"] = conflicts

    open("src/manifest.json", "w", encoding="utf-8").write(
        json.dumps(manifest, ensure_ascii=False)
    )


def write_consts(noconsts: bool) -> None:
    if noconsts:
        return
    s = ""
    for name, val in consts.items():
        s += f"{name.upper()} = {repr(val)}\n"
    open("src/consts.py", "w", encoding="utf-8").write(s)


def generate_forms(qt_version: Optional[str], forms_dir: Path) -> None:
    if not qt_version:
        return
    forms = Path("./designer").glob("*.ui")
    if qt_version == "qt5":
        from PyQt5.uic import compileUi
    elif qt_version == "qt6":
        from PyQt6.uic import compileUi
    if qt_version != "all":
        for form in forms:
            buf = io.StringIO()
            compileUi(open(form), buf)
            name = form.stem + ".py"
            value = buf.getvalue()
            open(forms_dir / name, "w", encoding="utf-8").write(value)
    else:
        from PyQt5.uic import compileUi as compileUi5
        from PyQt6.uic import compileUi as compileUi6

        funcs = {"qt5": compileUi5, "qt6": compileUi6}
        for form in forms:
            for suffix, func in funcs.items():
                buf = io.StringIO()
                func(open(form), buf)
                name = form.stem + f"_{suffix}.py"
                value = buf.getvalue()
                open(forms_dir / name, "w", encoding="utf-8").write(value)


def get_package_name(buildtype: str, qt_version: Optional[str]) -> str:
    os.makedirs("build", exist_ok=True)
    name = f"build/{consts['package']}"
    if buildtype == "ankiweb":
        name += "_ankiweb"
    if qt_version and qt_version != "all":
        name += f"_{qt_version}"
    name += ".ankiaddon"

    return name


def dump_scripts(dump: bool) -> None:
    if not dump:
        return

    src_file = Path(__file__)
    dest_file = Path("./build.py").resolve()
    if src_file != dest_file:
        dest_file.write_text(src_file.read_text(), encoding="utf-8")

    src_file = Path(os.path.join(os.path.dirname(__file__), "ankirun.py"))
    dest_file = Path("./run.py").resolve()
    if src_file != dest_file:
        dest_file.write_text(src_file.read_text(), encoding="utf-8")


def most_recent_change(args: argparse.Namespace):
    excludes = args.exclude if args.exclude else []
    newest = 0
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
            for dirpath, dirs, fnames in os.walk(path):
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
                    p = os.path.join(dirpath, fname)
                    newest = max(newest, os.stat(p).st_mtime)

    return newest


def needs_build(args: argparse.Namespace, name: str):
    build_ts = last_build_time(name)
    mod_ts = most_recent_change(args)
    return mod_ts > build_ts


def last_build_time(name):
    try:
        return os.stat(name).st_mtime
    except:
        return 0


def validate_config(args: argparse.Namespace) -> None:
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
    "--noconsts",
    action="store_true",
    help="do not generate src/consts.py from addon.json",
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

args = parser.parse_args()
validate_config(args)
buildtype = args.type
qt_version = args.qt
dump = args.dump
forms_dir = Path(f"./src/{args.forms_dir}")
forms_dir.mkdir(exist_ok=True)

dump_scripts(dump)
consts = read_addon_json()
name = get_package_name(buildtype, qt_version)

if not needs_build(args, name):
    sys.exit(0)

to_remove = {"**/__pycache__"}
for pattern in to_remove:
    for path in Path("./src").glob(pattern):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            os.remove(path)

write_manifest(buildtype)
generate_forms(qt_version, forms_dir)
write_consts(args.noconsts)

excludes = args.exclude if args.exclude else []
for i, exclude in enumerate(excludes):
    excludes[i] = f"-xr!{exclude}"

subprocess.check_call(
    [
        "7z",
        "a",
        "-tzip",
        "-bso0",
        name,
        "-w",
        "src/.",
        *excludes,
    ]
)
