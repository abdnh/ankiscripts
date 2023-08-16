import shutil
import subprocess
import sys
from pathlib import Path

from ._utils import pip_install


def install_libs() -> None:
    addon_root = Path(".")
    reqs_path = addon_root / "requirements" / "bundle.txt"
    if not reqs_path.exists():
        print(
            "requirements/bundle.txt not found; skipping installation of vendored libraries",
            file=sys.stderr,
        )
        return
    vendor_path = addon_root / "src" / "vendor"
    bin_path = vendor_path / "bin"
    # Additional vendoring logic (e.g. installing node modules) can be specified in scripts/vendor.sh
    vendor_script_path = addon_root / "scripts" / "vendor.sh"
    python_exe = shutil.which("python")
    bash_exe = shutil.which("bash")

    pip_install(python_exe, str(reqs_path), str(vendor_path))
    if bin_path.exists():
        shutil.rmtree(bin_path)
    if vendor_script_path.exists():
        # Seems like Bash on Windows expects POSIX paths
        subprocess.check_call([bash_exe, str(vendor_script_path.as_posix())])


if __name__ == "__main__":
    install_libs()
