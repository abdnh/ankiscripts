import shutil
import subprocess

from .._utils import pip_install

pip_install(shutil.which("python"), "requirements/base.txt")
subprocess.run([shutil.which("pip-sync"), "requirements/dev.txt"], check=True)
subprocess.run(
    "sudo apt install libxcb-xinerama0 libxcb-cursor0 libegl1", shell=True, check=True
)
