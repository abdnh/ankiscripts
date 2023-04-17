"""
Usage:

python -m ankiscripts.support --type github GITHUB_ISSUES https://github.com/abdnh/AnkiApp-importer/issues FORUMS_PAGE https://forums.ankiweb.net/t/ankiapp-importer/16734
"""
from argparse import ArgumentParser
from pathlib import Path


def format(type: str, fmt_args: dict[str, str]) -> str:
    fmt_args.setdefault(
        "GITHUB_ISSUES", "https://github.com/abdnh/anki-addon-template/issues"
    )
    fmt_args.setdefault("FORUMS_PAGE", "https://forums.ankiweb.net/c/add-ons/11")
    ext = ".md" if type == "github" else ".html"
    file = Path(__file__).parent / f"support{ext}"
    with open(file, "r", encoding="utf-8") as file:
        text = file.read().format(**fmt_args)
        return text


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument(
        "--type",
        help="Type of file to format",
        metavar="TYPE",
        choices=("github", "ankiweb"),
        default="github",
    )
    args, unknown = parser.parse_known_args()
    fmt_args = dict((unknown[i], unknown[i + 1]) for i in range(0, len(unknown), 2))
    print(format(args.type, fmt_args))
