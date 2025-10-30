"""
This script outputs the add-on's pinned/minimum/maximum
supported Python/Anki versions for CI testing.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

from ._utils import (
    MAX_ANKI_VERSION,
    get_min_max_anki_versions,
    get_supported_python_versions,
    read_dependency_version,
    read_python_version,
)


@dataclass
class VersionPair:
    python: tuple[int, ...]
    anki: tuple[int, ...]

    def to_dict(self) -> dict[str, str]:
        dct = asdict(self)
        for k, v in dct.items():
            dct[k] = ".".join(str(p) for p in v)
        return dct


def get_latest_anki_version() -> tuple[int, ...]:
    response = requests.get("https://pypi.org/pypi/aqt/json", timeout=30)
    response.raise_for_status()
    data = response.json()
    releases = data.get("releases", {})
    version_times = []
    for version, files in releases.items():
        if files:
            upload_time = files[0].get("upload_time_iso_8601")
            if upload_time:
                version_times.append((version, upload_time))

    version_times.sort(key=lambda x: x[1])
    versions = [version for version, _ in version_times]
    return tuple(int(p) for p in versions[-1].split("."))


def dot_version_from_int_version(int_version: int) -> tuple[int, ...]:
    patch = int_version % 100
    int_version -= patch
    if int_version == 0:
        return (2, 1, patch)
    else:
        # calendar versioning
        int_version //= 100
        minor = int_version % 100
        int_version -= minor
        major = int_version // 100
        return (major, minor, patch)


addon_root = Path.cwd()
min_point, max_point = get_min_max_anki_versions(addon_root)
python_versions = sorted(get_supported_python_versions(min_point, max_point))
pinned_python_version = read_python_version(addon_root)
pinned_anki_version = read_dependency_version(addon_root, "aqt")
assert pinned_anki_version is not None
latest_anki_version = get_latest_anki_version()

pairs: list[VersionPair] = [
    VersionPair(
        python=python_versions[0], anki=dot_version_from_int_version(min_point)
    ),
    VersionPair(
        python=python_versions[-1],
        anki=get_latest_anki_version()
        if max_point == MAX_ANKI_VERSION
        else dot_version_from_int_version(max_point),
    ),
]
found_pinned_version = False
for pair in pairs:
    # Skip pinned version if already included
    if pinned_anki_version == pair.anki:
        found_pinned_version = True
        break
if not found_pinned_version:
    pairs.append(VersionPair(python=pinned_python_version, anki=pinned_anki_version))

pairs.sort(key=lambda p: p.python, reverse=True)
print(json.dumps([p.to_dict() for p in pairs]))
