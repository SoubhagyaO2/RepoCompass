import textwrap

import pytest

from app.chunking.ast_chunker import MODULE_NAME, chunk_python_source
from app.chunking.categories import classify_file
from app.models.chunk import Chunk


def chunk(code: str, path: str = "pkg/mod.py") -> list[Chunk]:
    source = textwrap.dedent(code).lstrip("\n").encode("utf-8")
    return chunk_python_source(source, path, "acme/widget")[1]


def check_invariants(chunks: list[Chunk], source: str) -> None:
    """Every chunk is the exact text of its line range; chunks never overlap."""
    lines = source.split("\n")
    for c in chunks:
        assert c.content == "\n".join(lines[c.start_line - 1 : c.end_line]).removesuffix("\r")
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert a.end_line < b.start_line


def test_function_chunk_has_full_metadata() -> None:
    chunks = chunk(
        """
        import os
        from .models import User as U


        def authenticate_user(name):
            return name
        """,
        path="auth/service.py",
    )
    func = next(c for c in chunks if c.symbol_type == "function")
    assert func.id == "acme/widget:auth/service.py:5"
    assert func.repository == "acme/widget"
    assert func.file_path == "auth/service.py"
    assert func.language == "Python"
    assert func.symbol_name == func.qualified_name == "authenticate_user"
    assert (func.start_line, func.end_line) == (5, 6)
    assert func.content == "def authenticate_user(name):\n    return name"
    assert func.imports == ["import os", "from .models import User as U"]
    assert func.category == "source"
    assert func.parent is None


def test_class_chunk_is_header_only_and_methods_are_separate() -> None:
    source = textwrap.dedent(
        '''
        class Service(Base):
            """Does things."""

            retries = 3

            def run(self):
                return 1

            def stop(self):
                return 2
        '''
    ).lstrip("\n")
    chunks = chunk_python_source(source.encode(), "svc.py", "acme/widget")[1]

    assert [(c.qualified_name, c.symbol_type) for c in chunks] == [
        ("Service", "class"),
        ("Service.run", "method"),
        ("Service.stop", "method"),
    ]
    header, run, stop = chunks
    assert header.content == 'class Service(Base):\n    """Does things."""\n\n    retries = 3'
    assert (header.start_line, header.end_line) == (1, 4)  # trailing blank line trimmed
    assert run.parent == "Service"
    assert run.content == "    def run(self):\n        return 1"  # indentation is preserved
    assert (run.start_line, run.end_line) == (6, 7)
    assert (stop.start_line, stop.end_line) == (9, 10)
    check_invariants(chunks, source)


def test_decorated_method_starts_at_decorator_and_ends_class_header_before_it() -> None:
    chunks = chunk(
        """
        class A:
            x = 1

            @property
            def y(self):
                return 2
        """
    )
    header, method = chunks
    assert header.content == "class A:\n    x = 1"
    assert method.content == "    @property\n    def y(self):\n        return 2"
    assert method.start_line == 4


def test_class_without_methods_is_one_whole_chunk() -> None:
    chunks = chunk(
        """
        class Config:
            debug = True
            name = "x"
        """
    )
    (only,) = chunks
    assert only.symbol_type == "class"
    assert only.content == 'class Config:\n    debug = True\n    name = "x"'


def test_nested_class_is_its_own_chunk_after_outer_header() -> None:
    chunks = chunk(
        """
        class Outer:
            a = 1

            class Inner:
                b = 2

                def go(self):
                    pass
        """
    )
    assert [(c.qualified_name, c.symbol_type) for c in chunks] == [
        ("Outer", "class"),
        ("Outer.Inner", "class"),
        ("Outer.Inner.go", "method"),
    ]
    assert chunks[0].content == "class Outer:\n    a = 1"
    assert chunks[1].content == "    class Inner:\n        b = 2"


def test_module_level_code_becomes_module_chunks() -> None:
    source = textwrap.dedent(
        '''
        """Module docstring."""

        import os

        DEBUG = False


        def helper():
            pass


        app = create_app()
        '''
    ).lstrip("\n")
    chunks = chunk_python_source(source.encode(), "app.py", "acme/widget")[1]

    assert [(c.symbol_type, c.start_line, c.end_line) for c in chunks] == [
        ("module", 1, 5),
        ("function", 8, 9),
        ("module", 12, 12),
    ]
    assert chunks[0].symbol_name == MODULE_NAME
    assert chunks[0].content == '"""Module docstring."""\n\nimport os\n\nDEBUG = False'
    assert chunks[2].content == "app = create_app()"
    check_invariants(chunks, source)


def test_file_with_only_module_code_and_empty_files() -> None:
    (only,) = chunk("from .app import App as App\n", path="pkg/__init__.py")
    assert only.symbol_type == "module"
    assert only.content == "from .app import App as App"

    assert chunk("") == []
    assert chunk("\n\n   \n") == []


def test_crlf_files_keep_exact_text_and_exact_line_numbers() -> None:
    source = "class A:\r\n    x = 1\r\n\r\n    def f(self):\r\n        pass\r\n"
    chunks = chunk_python_source(source.encode(), "a.py", "acme/widget")[1]
    header, method = chunks
    assert header.content == "class A:\r\n    x = 1"
    assert method.content == "    def f(self):\r\n        pass"
    assert (method.start_line, method.end_line) == (4, 5)
    check_invariants(chunks, source)


def test_form_feed_does_not_shift_line_numbers() -> None:
    # str.splitlines() would treat \x0c as a line break and misnumber everything after it.
    source = "X = 1\n\x0c\ndef f():\n    pass\n"
    chunks = chunk_python_source(source.encode(), "a.py", "acme/widget")[1]
    func = next(c for c in chunks if c.symbol_type == "function")
    assert func.start_line == 3
    check_invariants(chunks, source)


def test_overloads_get_distinct_ids() -> None:
    chunks = chunk(
        """
        @overload
        def f(x: int) -> int: ...
        @overload
        def f(x: str) -> str: ...
        def f(x):
            return x
        """
    )
    assert len({c.id for c in chunks}) == len(chunks) == 3


def test_syntax_errors_still_produce_chunks() -> None:
    chunks = chunk(
        """
        def good():
            return 1

        def broken(:
            pass
        """
    )
    assert "good" in [c.symbol_name for c in chunks]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("fastapi/routing.py", "source"),
        ("src/flask/testing.py", "source"),  # a library module, not a test
        ("tests/test_routing.py", "test"),
        ("src/pkg/tests/helpers.py", "test"),
        ("pkg/test_utils.py", "test"),
        ("pkg/utils_test.py", "test"),
        ("conftest.py", "test"),
        ("docs_src/tutorial/app.py", "docs"),
        ("examples/tutorial/flaskr/db.py", "docs"),
        ("docs/tests/x.py", "test"),
        ("scripts/build.py", "source"),
    ],
)
def test_classify_file(path: str, expected: str) -> None:
    assert classify_file(path) == expected
