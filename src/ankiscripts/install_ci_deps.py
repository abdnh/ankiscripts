from pathlib import Path

from ._utils import run_bash_script

run_bash_script(Path(__file__).parent / "install_ci_deps.sh")
