class AnkiScriptsException(Exception):
    pass


class NoCCompilerException(AnkiScriptsException):
    def __init__(self) -> None:
        super().__init__(
            "No C compiler was found. "
            "Looked for `CC`, `x86_64-w64-mingw32-gcc`, `clang`"
        )
