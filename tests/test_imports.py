from pathlib import Path

from src.ankiscripts.rewrite_imports import (
    rewrite_imports_with_libcst,
)


def assert_rewritten_imports(
    base_path: Path,
    vendored_packages: set[str],
    src_package: str,
    src: str,
    expected_rewritten_src: str,
    src_is_in_vendor: bool = True,
) -> None:
    vendor_path = base_path / "vendor"
    vendor_path.mkdir(exist_ok=True)
    src_module_parts = src_package.split(".")
    if src_is_in_vendor:
        src_dir = vendor_path
    else:
        src_dir = base_path / "src"
    src_dir = src_dir.joinpath(*src_module_parts[:-1])
    src_dir.mkdir(parents=True, exist_ok=True)
    src_path = src_dir / f"{src_module_parts[-1]}.py"
    src_path.write_text(src, encoding="utf-8")
    rewrite_imports_with_libcst(src_path, vendored_packages, vendor_path)
    assert src_path.read_text(encoding="utf-8") == expected_rewritten_src


def test_simple_import(tmp_path: Path) -> None:
    assert_rewritten_imports(tmp_path, {"foo"}, "a", "import foo", "from . import foo")
    assert_rewritten_imports(
        tmp_path, {"foo"}, "a.b", "import foo", "from .. import foo"
    )
    assert_rewritten_imports(
        tmp_path, {"foo"}, "a", "import foo", "from ..vendor import foo", False
    )
    # This is not supported yet because we assume
    # source is one level deep in a sibling directory
    # assert_rewritten_imports(
    #     tmp_path, {"foo"}, "a.b", "import foo", "from ...vendor import foo", False
    # )


def test_simple_import_no_libs(tmp_path: Path) -> None:
    assert_rewritten_imports(tmp_path, set(), "a", "import foo", "import foo")


def test_package_import(tmp_path: Path) -> None:
    assert_rewritten_imports(
        tmp_path,
        {"foo"},
        "a",
        "import foo.bar",
        # "from . import foo; "
        "from .foo import bar",
    )
    assert_rewritten_imports(
        tmp_path,
        {"foo"},
        "a",
        "import foo.bar.spam",
        # "from . import foo; "
        "from .foo.bar import spam",
    )

    assert_rewritten_imports(
        tmp_path,
        {"foo"},
        "a.b",
        "import foo.bar",
        # "from .. import foo; "
        "from ..foo import bar",
    )
    assert_rewritten_imports(
        tmp_path,
        {"foo"},
        "a.b",
        "import foo.bar.spam",
        # "from .. import foo; "
        "from ..foo.bar import spam",
    )

    assert_rewritten_imports(
        tmp_path,
        {"foo"},
        "a",
        "import foo.bar",
        # "from ..vendor import foo; "
        "from ..vendor.foo import bar",
        False,
    )
    assert_rewritten_imports(
        tmp_path,
        {"foo"},
        "a",
        "import foo.bar.spam",
        # "from ..vendor import foo; "
        "from ..vendor.foo.bar import spam",
        False,
    )


def test_package_import_no_libs(tmp_path: Path) -> None:
    assert_rewritten_imports(tmp_path, set(), "a", "import foo.bar", "import foo.bar")


def test_import_from_same_package(tmp_path: Path) -> None:
    assert_rewritten_imports(
        tmp_path, {"foo"}, "foo.bar", "import foo", "from .. import foo"
    )


def test_package_import_from_init_module(tmp_path: Path) -> None:
    assert_rewritten_imports(
        tmp_path,
        {"foo"},
        "foo.__init__",
        "import foo.bar",
        "from . import bar; import sys; foo = sys.modules[__name__]",
    )


def test_simple_from_import(tmp_path: Path) -> None:
    assert_rewritten_imports(
        tmp_path, {"foo"}, "a", "from foo import bar", "from .foo import bar"
    )
    assert_rewritten_imports(
        tmp_path, {"foo"}, "a.b", "from foo import bar", "from ..foo import bar"
    )

    assert_rewritten_imports(
        tmp_path,
        {"foo"},
        "a",
        "from foo import bar",
        "from ..vendor.foo import bar",
        False,
    )


def test_simple_from_import_no_libs(tmp_path: Path) -> None:
    assert_rewritten_imports(
        tmp_path, set(), "a", "from foo import bar", "from foo import bar"
    )


def test_package_from_import(tmp_path: Path) -> None:
    assert_rewritten_imports(
        tmp_path, {"foo"}, "a", "from foo.bar import spam", "from .foo.bar import spam"
    )
    assert_rewritten_imports(
        tmp_path,
        {"foo"},
        "a.b",
        "from foo.bar import spam",
        "from ..foo.bar import spam",
    )

    assert_rewritten_imports(
        tmp_path,
        {"foo"},
        "a",
        "from foo.bar import spam",
        "from ..vendor.foo.bar import spam",
        False,
    )


def test_from_import_same_package(tmp_path: Path) -> None:
    assert_rewritten_imports(
        tmp_path,
        {"foo"},
        "foo.bar",
        "from foo.bar import spam",
        "from .bar import spam",
    )
