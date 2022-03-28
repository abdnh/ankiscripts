import sys
import os
from pathlib import Path
import argparse
from typing import Optional, Any, Dict
import json
import io
import subprocess
import shutil


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


def generate_forms(qt_version: Optional[str]) -> None:
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
            open(f"src/{name}", "w", encoding="utf-8").write(value)
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
                open(f"src/{name}", "w", encoding="utf-8").write(value)


def get_package_name(buildtype: str, qt_version: Optional[str]) -> str:
    os.makedirs("build", exist_ok=True)
    name = f"build/{consts['package']}"
    if buildtype == "ankiweb":
        name += "_ankiweb"
    if qt_version:
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
    dest_file = Path("./ankirun.py").resolve()
    if src_file != dest_file:
        dest_file.write_text(src_file.read_text(), encoding="utf-8")


def most_recent_change(args: argparse.Namespace):
    newest = 0
    paths = ["src", "addon.json"]
    if args.qt:
        paths.append("designer")
    for path in paths:
        if os.path.isfile(path):
            newest = max(newest, os.stat(path).st_mtime)
        else:
            for dirpath, _, fnames in os.walk(path):
                for fname in fnames:
                    path = os.path.join(dirpath, fname)
                    newest = max(newest, os.stat(path).st_mtime)

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
    "--install",
    action="store_true",
    help="install in an Anki base folder assumed to be located at `ankiprofile` in the current directory",
)
parser.add_argument(
    "--noconsts",
    action="store_true",
    help="do not generate src/consts.py from addon.json",
)


args = parser.parse_args()
buildtype = args.type
qt_version = args.qt
dump = args.dump

dump_scripts(dump)
consts = read_addon_json()
name = get_package_name(buildtype, qt_version)
if args.install:
    shutil.copytree(
        "src", f'ankiprofile/addons21/{consts["package"]}', dirs_exist_ok=True
    )

if not needs_build(args, name):
    sys.exit(0)

to_remove = {
    "src/__pycache__",
    "src/*_qt5.py",
    "src/*_qt6.py",
    "src/*_all.py",
}
if not args.noconsts:
    to_remove.add("src/consts.py")

for path in to_remove:
    if os.path.exists(path):
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)

write_manifest(buildtype)
generate_forms(qt_version)
write_consts(args.noconsts)

subprocess.check_call(
    [
        "7z",
        "a",
        "-tzip",
        "-bso0",
        name,
        "-w",
        "src/.",
    ]
)
