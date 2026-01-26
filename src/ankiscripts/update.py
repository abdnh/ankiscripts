"""
Update add-on using the copier-based add-on template
"""

import argparse
from pathlib import Path

import copier


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", help="add-on folder", default=".")
    args = parser.parse_args()
    addon_root = Path(args.destination)
    copier.run_update(
        dst_path=addon_root,
        vcs_ref="HEAD",
        unsafe=True,
        skip_answered=True,
        overwrite=True,
    )


if __name__ == "__main__":
    main()
