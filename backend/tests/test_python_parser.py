import textwrap
from pathlib import Path

from app.models.symbols import Import, ParsedFile, Symbol
from app.parsing.python_parser import parse_python_file, parse_python_source


def parse(code: str) -> ParsedFile:
    return parse_python_source(textwrap.dedent(code).lstrip("\n").encode("utf-8"), "pkg/mod.py")


def by_name(parsed: ParsedFile) -> dict[str, Symbol]:
    return {s.qualified_name: s for s in parsed.symbols}


# --- symbols -------------------------------------------------------------------------


def test_function_with_lines_and_exact_content() -> None:
    parsed = parse(
        '''
        import os


        def authenticate_user(name, password):
            """Check credentials."""
            return name == password
        '''
    )
    (sym,) = parsed.symbols
    assert sym.name == "authenticate_user"
    assert sym.kind == "function"
    assert sym.file == "pkg/mod.py"
    assert (sym.start_line, sym.end_line) == (4, 6)
    assert sym.content == (
        'def authenticate_user(name, password):\n    """Check credentials."""\n'
        "    return name == password"
    )
    assert sym.parent is None


def test_classes_methods_and_nested_classes() -> None:
    parsed = parse(
        """
        class UserService(Base):
            def get_user(self, user_id):
                return user_id

            async def save(self, user):
                pass

            class Config:
                def validate(self):
                    pass
        """
    )
    syms = by_name(parsed)
    assert list(syms) == [
        "UserService",
        "UserService.get_user",
        "UserService.save",
        "UserService.Config",
        "UserService.Config.validate",
    ]
    assert syms["UserService"].kind == "class"
    assert (syms["UserService"].start_line, syms["UserService"].end_line) == (1, 10)
    assert syms["UserService.get_user"].kind == "method"
    assert syms["UserService.get_user"].parent == "UserService"
    assert syms["UserService.save"].kind == "method"  # async is still a method
    assert syms["UserService.Config"].kind == "class"
    assert syms["UserService.Config.validate"].parent == "UserService.Config"


def test_decorators_are_included_in_range_and_content() -> None:
    parsed = parse(
        """
        @app.route("/login")
        @login_required
        def login():
            pass
        """
    )
    (sym,) = parsed.symbols
    assert sym.name == "login"
    assert (sym.start_line, sym.end_line) == (1, 4)
    assert sym.content.startswith('@app.route("/login")\n@login_required\ndef login():')


def test_nested_functions_stay_inside_their_parent() -> None:
    parsed = parse(
        """
        def outer():
            def inner():
                pass
            return inner
        """
    )
    assert [s.qualified_name for s in parsed.symbols] == ["outer"]
    assert "def inner():" in parsed.symbols[0].content


def test_definitions_inside_if_and_try_blocks_are_found() -> None:
    parsed = parse(
        """
        if sys.version_info >= (3, 11):
            def helper():
                pass
        else:
            def helper():
                pass

        try:
            class Fast:
                pass
        except ImportError:
            class Fast:
                pass
        """
    )
    assert [(s.name, s.start_line) for s in parsed.symbols] == [
        ("helper", 2),
        ("helper", 5),
        ("Fast", 9),
        ("Fast", 12),
    ]


def test_content_is_correct_after_multibyte_characters() -> None:
    # 'é' and '🚀' are multi-byte in UTF-8; Tree-sitter offsets are bytes, not characters.
    parsed = parse(
        """
        GREETING = "héllo 🚀"


        def after():
            return "ünïcode"
        """
    )
    (sym,) = parsed.symbols
    assert sym.content == 'def after():\n    return "ünïcode"'


def test_crlf_line_endings_are_preserved() -> None:
    source = b"def f():\r\n    return 1\r\n"
    (sym,) = parse_python_source(source, "f.py").symbols
    assert sym.content == "def f():\r\n    return 1"


# --- imports -------------------------------------------------------------------------


def test_all_import_forms() -> None:
    parsed = parse(
        """
        import os
        import os.path as osp, sys
        from collections import OrderedDict as OD, defaultdict
        from .models import User
        from .. import utils
        from typing import *
        from __future__ import annotations
        from pkg import (
            a,
            b,
        )
        """
    )
    assert parsed.imports == [
        Import(module="os", line=1),
        Import(module="os.path", alias="osp", line=2),
        Import(module="sys", line=2),
        Import(module="collections", name="OrderedDict", alias="OD", line=3),
        Import(module="collections", name="defaultdict", line=3),
        Import(module="models", name="User", level=1, line=4),
        Import(module="", name="utils", level=2, line=5),
        Import(module="typing", name="*", line=6),
        Import(module="__future__", name="annotations", line=7),
        Import(module="pkg", name="a", line=8),
        Import(module="pkg", name="b", line=8),
    ]


def test_imports_inside_functions_and_conditionals_are_found() -> None:
    parsed = parse(
        """
        if TYPE_CHECKING:
            from app.models import User

        def load():
            import json
            return json
        """
    )
    assert [(i.module, i.line) for i in parsed.imports] == [("app.models", 2), ("json", 5)]


# --- robustness ----------------------------------------------------------------------


def test_empty_file() -> None:
    parsed = parse("")
    assert parsed.symbols == []
    assert parsed.imports == []
    assert parsed.has_errors is False


def test_syntax_errors_are_tolerated() -> None:
    parsed = parse(
        """
        def good():
            return 1

        def broken(:
            pass

        print "python 2"
        """
    )
    assert parsed.has_errors is True
    assert "good" in [s.name for s in parsed.symbols]


def test_parse_python_file_reads_raw_bytes(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_bytes(b"class A:\r\n    pass\r\n")
    parsed = parse_python_file(tmp_path, "pkg/mod.py")
    assert parsed.path == "pkg/mod.py"
    assert parsed.symbols[0].content == "class A:\r\n    pass"
