import subprocess
import tempfile
import zipfile
from pathlib import Path
from shutil import which

from ._utils import read_addon_json

addon_root = Path.cwd()
build_dir = addon_root / "build"
package = read_addon_json(addon_root).get("package") or addon_root.name
git_exe = which("git")

with tempfile.TemporaryDirectory() as tempdir:
    zip_path = build_dir / (package + "_sources.zip")
    bundle_path = Path(tempdir) / "repo.bundle"
    archive_path = Path(tempdir) / "repo.zip"
    subprocess.run(
        [git_exe, "bundle", "create", str(bundle_path), "master"], check=True
    )
    subprocess.run([git_exe, "archive", "HEAD", "-o", str(archive_path)], check=True)
    with zipfile.ZipFile(zip_path, "w") as file:
        file.write(bundle_path, bundle_path.name)
        file.write(archive_path, archive_path.name)
