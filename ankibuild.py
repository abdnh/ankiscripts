import sys
import os
from pathlib import Path
import argparse
from typing import Optional, Any, Dict
import zipfile
import json
import io


def read_addon_json() -> Dict[str, Any]:
    return json.load(open("addon.json"))


def write_manifest(addon_zip: zipfile.ZipFile, buildtype: str) -> None:
    manifest = {
        "name": consts["name"],
    }
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

    addon_zip.writestr("manifest.json", json.dumps(manifest))


def write_consts(addon_zip: zipfile.ZipFile) -> None:
    s = ""
    for name, val in consts.items():
        s += f"{name.upper()} = {repr(val)}\n"
    addon_zip.writestr("consts.py", s)


def generate_forms(addon_zip: zipfile.ZipFile, qt_version: Optional[str]) -> None:
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
            addon_zip.writestr(form.stem + ".py", buf.getvalue())
    else:
        from PyQt5.uic import compileUi as compileUi5
        from PyQt6.uic import compileUi as compileUi6

        funcs = {"qt5": compileUi5, "qt6": compileUi6}
        for form in forms:
            for suffix, func in funcs.items():
                buf = io.StringIO()
                func(open(form), buf)
                addon_zip.writestr(form.stem + f"_{suffix}.py", buf.getvalue())


def get_package_name(buildtype: str, qt_version: Optional[str]) -> str:
    os.makedirs("build", exist_ok=True)
    name = f"build/{consts['package']}"
    if buildtype == "ankiweb":
        name += "_ankiweb"
    if qt_version:
        name += f"_{qt_version}"
    name += ".ankiaddon"

    return name


def dump_build_script(dump: bool) -> None:
    if not dump:
        return

    src_file = Path(__file__)
    dest_file = Path("./build.py").resolve()
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
    help="dump this build script to the current directory for stand-alone source distributions",
)
parser.add_argument(
    "--install",
    action="store_true",
    help="install in an Anki base folder assumed to be located at `ankiprofile` in the current directory",
)


args = parser.parse_args()
buildtype = args.type
qt_version = args.qt
dump = args.dump

dump_build_script(dump)
consts = read_addon_json()
name = get_package_name(buildtype, qt_version)
if not needs_build(args, name):
    sys.exit(0)

excluded = {"__pycache__"}

with zipfile.ZipFile(name, mode="w") as addon_zip:
    src_path = Path("./src")
    for file in src_path.glob("**/*"):
        if file.name in excluded or file.parent.name in excluded:
            continue
        addon_zip.write(file, arcname=file.relative_to(src_path))
    write_manifest(addon_zip, buildtype)
    generate_forms(addon_zip, qt_version)
    write_consts(addon_zip)
    if args.install:
        addon_zip.extractall(f'ankiprofile/addons21/{consts["package"]}')
    addon_zip.close()
