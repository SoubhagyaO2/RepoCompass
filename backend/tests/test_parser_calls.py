import textwrap

from app.models.symbols import Call
from app.parsing.python_parser import parse_python_source


def parse(code: str):  # type: ignore[no-untyped-def]
    return parse_python_source(textwrap.dedent(code).lstrip("\n").encode("utf-8"), "pkg/mod.py")


def calls_of(parsed, caller: str | None):  # type: ignore[no-untyped-def]
    return [(c.receiver, c.name, c.kind) for c in parsed.calls if c.caller == caller]


def test_calls_are_attributed_to_the_enclosing_function_or_method() -> None:
    parsed = parse(
        """
        def helper():
            return other(1)

        class Service:
            def run(self):
                self.step()
                self.client.send(x)
                helper()
        """
    )
    assert calls_of(parsed, "helper") == [(None, "other", "call")]
    assert calls_of(parsed, "Service.run") == [
        ("self", "step", "call"),
        ("self.client", "send", "call"),
        (None, "helper", "call"),
    ]
    assert Call(caller="helper", name="other", receiver=None, line=2) in parsed.calls


def test_module_level_calls_have_no_caller_and_class_body_calls_belong_to_the_class() -> None:
    parsed = parse(
        """
        app = create_app()

        class A:
            x = make()

            def m(self):
                inner()
        """
    )
    assert calls_of(parsed, None) == [(None, "create_app", "call")]
    assert calls_of(parsed, "A") == [(None, "make", "call")]
    assert calls_of(parsed, "A.m") == [(None, "inner", "call")]


def test_calls_inside_nested_functions_and_comprehensions_belong_to_the_enclosing_symbol() -> None:
    parsed = parse(
        """
        def outer():
            def inner():
                deep()
            return [f(x) for x in inner()]
        """
    )
    assert sorted(n for _, n, _ in calls_of(parsed, "outer")) == ["deep", "f", "inner"]


def test_decorators_are_calls_of_the_decorated_symbol() -> None:
    parsed = parse(
        """
        class A:
            @setupmethod
            def one(self): ...

            @app.route("/x")
            def two(self): ...
        """
    )
    assert ("None", "setupmethod", "decorator") in [
        (str(r), n, k) for r, n, k in calls_of(parsed, "A.one")
    ]
    assert ("app", "route", "decorator") in calls_of(parsed, "A.two")


def test_super_receiver_is_normalised_and_uncallable_targets_are_skipped() -> None:
    parsed = parse(
        """
        class B(A):
            def __init__(self):
                super().__init__()
                handlers[0]()
                (lambda: 1)()
        """
    )
    # the outer call (receiver normalised to "super()") and the builtin super() call inside it;
    # `handlers[0]()` and the lambda call have no static name and are skipped
    assert calls_of(parsed, "B.__init__") == [
        ("super()", "__init__", "call"),
        (None, "super", "call"),
    ]


def test_overloads_share_a_caller_name() -> None:
    parsed = parse(
        """
        @overload
        def f(x: int): ...
        def f(x):
            g(x)
        """
    )
    assert calls_of(parsed, "f") == [(None, "overload", "decorator"), (None, "g", "call")]


def test_base_classes_are_recorded_as_written() -> None:
    parsed = parse(
        """
        class A(Base, mixins.Mixin, metaclass=Meta): ...
        class B: ...
        class C(Generic[T]): ...
        """
    )
    bases = {s.name: s.bases for s in parsed.symbols}
    assert bases == {"A": ["Base", "mixins.Mixin"], "B": [], "C": []}
