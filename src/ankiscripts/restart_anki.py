import argparse
import shutil
import subprocess
import time
from pathlib import Path

import psutil
from send2trash import send2trash


# Credit: Adapted from Anki
def send_to_trash(path: Path) -> None:
    "Place file/folder in recycling bin, or delete permanently on failure."
    if not path.exists():
        return
    try:
        send2trash(path)
    except Exception as exc:
        print("trash failure:", path, exc)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Wait for Anki process to exit, "
        "then restart with optional package installation or add-on deletion"
    )
    parser.add_argument("pid", type=int, help="Process ID to wait for")
    parser.add_argument("anki_exe", help="Path to Anki executable")
    parser.add_argument("anki_base", help="Anki base data directory")
    parser.add_argument(
        "addon_dir_or_package",
        help="Add-on directory to delete or package file to install",
    )

    args = parser.parse_args()

    package_args = []
    if args.addon_dir_or_package.endswith(".ankiaddon"):
        print(f"Installing addon from package {args.addon_dir_or_package}...")
        package_args = [args.addon_dir_or_package]

    def is_running(pid: int) -> bool:
        print(f"Checking if PID {pid} is running...")
        exists = psutil.pid_exists(pid)
        return exists

    while is_running(args.pid):
        print(f"PID {args.pid} still running, sleeping...")
        time.sleep(0.5)

    print(f"PID {args.pid} is no longer running, proceeding to launch Anki.")
    if not package_args:
        print(f"Deleting addon directory {args.addon_dir_or_package}...")
        send_to_trash(Path(args.addon_dir_or_package))

    subprocess.Popen(
        ["cmd", "/c", "start", "/B", args.anki_exe, "-b", args.anki_base, *package_args]
    )


if __name__ == "__main__":
    main()
