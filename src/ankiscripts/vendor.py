import shutil
import subprocess
from pathlib import Path

from ._utils import pip_install


def install_libs() -> None:
    addon_root = Path(".")
    reqs_path = addon_root / "requirements" / "bundle.txt"
    if reqs_path.exists():
        vendor_path = addon_root / "src" / "vendor"
        bin_path = vendor_path / "bin"
        python_exe = shutil.which("python")
        pip_install(python_exe, str(reqs_path), str(vendor_path))
        if bin_path.exists():
            shutil.rmtree(bin_path)

    # Additional vendoring logic (e.g. installing node modules) can be specified in scripts/vendor.sh
    vendor_script_path = addon_root / "scripts" / "vendor.sh"
    bash_exe = shutil.which("bash")
    if vendor_script_path.exists():
        # Seems like Bash on Windows expects POSIX paths
        subprocess.check_call([bash_exe, str(vendor_script_path.as_posix())])


if __name__ == "__main__":
    install_libs()
