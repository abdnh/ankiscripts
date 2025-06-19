from argparse import ArgumentParser
from pathlib import Path

DEFAULTS = {
    "GITHUB_ISSUES": "https://github.com/abdnh/anki-addon-template/issues",
    "FORUMS_PAGE": "https://forums.ankiweb.net/c/add-ons/11",
}


def format(text: str, fmt_args: dict[str, str]) -> str:
    copy = DEFAULTS.copy()
    copy.update(fmt_args)
    fmt_args = copy
    for key, value in DEFAULTS.items():
        text = text.replace(value, fmt_args[key])
    text = text.format(**fmt_args)
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
    ext = ".md" if args.type == "github" else ".html"
    file = Path(__file__).parent / f"support{ext}"
    with open(file, encoding="utf-8") as f:
        text = f.read()
        print(format(text, fmt_args))
