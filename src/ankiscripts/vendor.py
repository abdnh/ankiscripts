import shutil
import sys
from pathlib import Path

from ._utils import pip_install


def install_libs() -> None:
    addon_root = Path(".")
    reqs_path = addon_root / "requirements.txt"
    if not reqs_path.exists():
        print(
            "requirements.txt not found; skipping installation of vendored libraries",
            file=sys.stderr,
        )
        return
    vendor_path = addon_root / "src" / "vendor"
    bin_path = vendor_path / "bin"
    python_exe = shutil.which("python")

    pip_install(python_exe, str(reqs_path), str(vendor_path))
    shutil.rmtree(bin_path)


if __name__ == "__main__":
    install_libs()
