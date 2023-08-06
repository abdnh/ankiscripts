import os
import subprocess
import sys
from pathlib import Path


def symlink_addon(addon_root: Path, addon_package: str) -> None:
    src_path = addon_root / "src"
    install_path = addon_root / "ankidata" / "addons21" / str(addon_package)
    if not install_path.exists():
        install_path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win32"):
            subprocess.run(
                'mklink /J "{}" "{}"'.format(str(install_path), str(src_path)),
                shell=True,
                check=True,
            )
        else:
            os.link(src_path, install_path)
