import json
import textwrap

import pytest

from app.graph.builder import build_graph_from_parsed, module_name
from app.graph.model import CodeGraph
from app.parsing.python_parser import parse_python_source


def build(files: dict[str, str]) -> CodeGraph:
    parsed = {
        path: parse_python_source(textwrap.dedent(code).lstrip("\n").encode(), path)
        for path, code in files.items()
    }
    return build_graph_from_parsed(parsed)


def calls(graph: CodeGraph, source: str, target: str) -> bool:
    return graph.has_edge("calls", source, target)


# --- module names and imports --------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/flask/app.py", "flask.app"),
        ("src/flask/__init__.py", "flask"),
        ("src/flask/sansio/app.py", "flask.sansio.app"),
        ("tests/test_basic.py", "tests.test_basic"),
        ("conftest.py", "conftest"),
        ("pkg/__init__.py", "pkg"),
    ],
)
def test_module_name(path: str, expected: str) -> None:
    assert module_name(path) == expected


def test_import_edges_resolve_relative_absolute_and_submodule_imports() -> None:
    g = build(
        {
            "src/pkg/__init__.py": "from .core import run as run\n",
            "src/pkg/core.py": "def run(): ...\n",
            "src/pkg/util.py": "def helper(): ...\n",
            "src/pkg/sub/__init__.py": "",
            "src/pkg/sub/deep.py": "def deep(): ...\n",
            "src/pkg/main.py": """
                import os
                from . import util
                from .core import run
                from .sub import deep
                from ..outside import nothing
                import pkg.sub.deep as d
                from pkg import core
                if TYPE_CHECKING:
                    from .util import helper
            """,
        }
    )
    imported = {e.target for e in g.imports_of("src/pkg/main.py")}
    assert imported == {
        "src/pkg/util.py",  # `from . import util`: a submodule imported by name
        "src/pkg/core.py",  # `from .core import run` and `from pkg import core`
        "src/pkg/sub/deep.py",  # `from .sub import deep` and `import pkg.sub.deep as d`
    }
    # `import os` and the unresolvable `..outside` produce no edge; the TYPE_CHECKING import
    # (`from .util import helper`) is included, and re-exports in __init__.py are import edges too
    assert g.has_edge("imports", "src/pkg/__init__.py", "src/pkg/core.py")
    assert not g.has_edge("imports", "src/pkg/main.py", "os")


def test_self_imports_are_ignored_and_importers_are_found() -> None:
    g = build({"a.py": "import a\nfrom b import x\n", "b.py": "x = 1\n"})
    assert g.imports_of("a.py")[0].target == "b.py"
    assert not g.has_edge("imports", "a.py", "a.py")
    importers = g.importers_of("b.py")
    assert [(e.source, e.evidence.line) for e in importers] == [("a.py", 2)]


# --- nodes, definitions, inheritance ------------------------------------------------------


def test_defines_edges_and_overloads_share_one_node() -> None:
    g = build(
        {
            "m.py": """
                class A:
                    def f(self): ...
                    def f(self, x): ...

                def top(): ...
            """
        }
    )
    assert g.has_edge("defines", "m.py", "m.py::A")
    assert g.has_edge("defines", "m.py::A", "m.py::A.f")
    assert g.has_edge("defines", "m.py", "m.py::top")
    assert g.graph.nodes["m.py::A.f"]["ranges"] == [(2, 2), (3, 3)]  # both definitions, one node
    assert g.node_type("m.py::A.f") == "method" and g.node_type("m.py") == "file"
    assert g.definitions("f") == ["m.py::A.f"] and g.definitions("A.f") == ["m.py::A.f"]


def test_inheritance_follows_aliases_attributes_and_reexports() -> None:
    g = build(
        {
            "base.py": "class Base: ...\n",
            "pkg/__init__.py": "from base import Base as Base\n",
            "child.py": """
                import base as b
                from pkg import Base as Aliased
                class One(b.Base): ...
                class Two(Aliased): ...
                class Three(Unknown): ...
            """,
        }
    )
    assert g.has_edge("inherits", "child.py::One", "base.py::Base")
    assert g.has_edge("inherits", "child.py::Two", "base.py::Base")  # through the re-export
    assert not any(g.graph.out_edges("child.py::Three", keys=True))  # unresolved base: no edge


# --- call resolution rules ----------------------------------------------------------------


def test_plain_calls_resolve_locally_or_through_imports_and_aliases() -> None:
    g = build(
        {
            "lib.py": "def helper(): ...\nclass Widget: ...\n",
            "main.py": """
                from lib import helper as h, Widget
                def local(): ...
                def run():
                    local()
                    h()
                    Widget()
                    print(1)
            """,
        }
    )
    assert calls(g, "main.py::run", "main.py::local")
    assert calls(g, "main.py::run", "lib.py::helper")  # through the alias
    assert calls(g, "main.py::run", "lib.py::Widget")  # constructor call -> the class
    rules = {e.evidence.rule for e in g.callees_of("main.py::run")}
    assert rules == {"local", "import"}
    assert g.stats.calls_unresolved >= 1  # print()


def test_module_attribute_calls_resolve_through_the_imported_module() -> None:
    g = build(
        {
            "helpers.py": "def go(): ...\n",
            "main.py": """
                import helpers
                from . import helpers as h2
                def run():
                    helpers.go()
                    nothing.go()
            """,
        }
    )
    assert calls(g, "main.py::run", "helpers.py::go")
    assert [e.evidence.rule for e in g.callees_of("main.py::run")] == ["import-module"]


def test_self_calls_use_the_class_then_its_base_classes() -> None:
    g = build(
        {
            "base.py": """
                class Base:
                    def shared(self): ...
                    def overridden(self): ...
            """,
            "child.py": """
                from base import Base
                class Child(Base):
                    def overridden(self): ...
                    def run(self):
                        self.own()
                        self.shared()
                        self.overridden()
                        self.missing()
                    def own(self): ...
            """,
        }
    )
    assert calls(g, "child.py::Child.run", "child.py::Child.own")
    assert calls(g, "child.py::Child.run", "base.py::Base.shared")  # found on the base class
    assert calls(g, "child.py::Child.run", "child.py::Child.overridden")  # the subclass wins
    assert not calls(g, "child.py::Child.run", "base.py::Base.overridden")
    assert {e.evidence.rule for e in g.callees_of("child.py::Child.run")} == {"self"}


def test_super_calls_look_only_in_the_base_classes() -> None:
    g = build(
        {
            "m.py": """
                class Base:
                    def __init__(self): ...
                class Child(Base):
                    def __init__(self):
                        super().__init__()
            """
        }
    )
    assert calls(g, "m.py::Child.__init__", "m.py::Base.__init__")
    assert not calls(g, "m.py::Child.__init__", "m.py::Child.__init__")
    assert [e.evidence.rule for e in g.callees_of("m.py::Child.__init__")] == ["super"]


def test_class_attribute_calls_resolve_on_the_named_class() -> None:
    g = build(
        {
            "m.py": """
                class Factory:
                    @classmethod
                    def make(cls): ...
                def run():
                    Factory.make()
            """
        }
    )
    assert calls(g, "m.py::run", "m.py::Factory.make")
    assert [e.evidence.rule for e in g.callees_of("m.py::run")] == ["class-attr"]


def test_other_receivers_resolve_only_when_the_method_name_is_unique() -> None:
    g = build(
        {
            "m.py": """
                class A:
                    def unique(self): ...
                    def shared(self): ...
                class B:
                    def shared(self): ...
                def run(obj):
                    obj.unique()
                    obj.shared()
                    obj.nowhere()
            """
        }
    )
    assert calls(g, "m.py::run", "m.py::A.unique")
    assert [e.evidence.rule for e in g.callees_of("m.py::run")] == ["unique-name"]
    assert not calls(g, "m.py::run", "m.py::A.shared") and not calls(
        g, "m.py::run", "m.py::B.shared"
    )
    assert g.stats.calls_ambiguous == 1  # obj.shared()
    assert g.stats.calls_unresolved == 1  # obj.nowhere()


def test_receivers_from_outside_the_repository_are_never_name_matched() -> None:
    g = build(
        {
            "m.py": """
                import click
                from werkzeug.test import EnvironBuilder
                class Ours:
                    def command(self): ...
                    def from_environ(self): ...
                def run():
                    click.command()
                    EnvironBuilder.from_environ()
            """
        }
    )
    assert g.callees_of("m.py::run") == []  # both would match a repo method by name alone
    assert g.stats.calls_external == 2


def test_a_known_class_without_the_method_is_unresolved_not_name_matched() -> None:
    g = build(
        {
            "m.py": """
                class Base: ...
                class Builder(Base): ...
                class Other:
                    def from_environ(self): ...
                def run():
                    Builder.from_environ()
            """
        }
    )
    assert g.callees_of("m.py::run") == []
    assert g.stats.calls_unresolved >= 1


def test_method_names_that_exist_on_builtin_types_are_not_name_matched() -> None:
    g = build(
        {
            "m.py": """
                class Globals:
                    def setdefault(self, k, v): ...
                    def copy(self): ...
                    def unique_thing(self): ...
                def run(d):
                    d.setdefault("a", 1)
                    d.copy()
                    d.unique_thing()
            """
        }
    )
    assert [e.target for e in g.callees_of("m.py::run")] == ["m.py::Globals.unique_thing"]
    assert g.stats.calls_builtin_name == 2


def test_name_matching_never_links_a_method_to_itself() -> None:
    g = build(
        {
            "m.py": """
                class Loader:
                    def list_templates(self):
                        return [t for l in self.loaders for t in l.list_templates()]
                    def recurse(self):
                        self.recurse()
            """
        }
    )
    assert not calls(g, "m.py::Loader.list_templates", "m.py::Loader.list_templates")
    assert calls(g, "m.py::Loader.recurse", "m.py::Loader.recurse")  # a real self-call is kept


def test_a_module_level_function_is_not_a_candidate_for_attribute_calls() -> None:
    g = build({"m.py": "def make_response(): ...\ndef run(app):\n    app.make_response()\n"})
    assert not calls(g, "m.py::run", "m.py::make_response")


def test_decorators_and_module_level_calls() -> None:
    g = build(
        {
            "m.py": """
                def setup(f): return f
                @setup
                def view(): ...
                result = view()
            """
        }
    )
    assert calls(g, "m.py::view", "m.py::setup")  # decorator application, from the decorated symbol
    assert calls(g, "m.py", "m.py::view")  # module-level call: the source is the file node
    assert g.callers_of("m.py::view")[0].source == "m.py"


def test_call_edges_aggregate_lines_and_keep_evidence() -> None:
    g = build(
        {
            "m.py": """
                def a(): ...
                def b():
                    a()
                    a()
            """
        }
    )
    (edge,) = g.callers_of("m.py::a")
    assert (edge.source, edge.relationship, edge.target) == ("m.py::b", "calls", "m.py::a")
    assert (edge.evidence.file, edge.evidence.line, edge.evidence.rule) == ("m.py", 3, "local")
    assert g.graph.edges["m.py::b", "m.py::a", "calls"]["lines"] == [3, 4]


# --- file-level neighbours ----------------------------------------------------------------


def test_file_neighbors_combine_imports_and_calls_in_both_directions() -> None:
    g = build(
        {
            "a.py": "from b import f\nfrom c import g\ndef run():\n    f()\n",
            "b.py": "def f():\n    h()\ndef h(): ...\n",
            "c.py": "def g(): ...\n",
            "d.py": "import a\nimport e\n",
        }
    )
    neighbors = g.file_neighbors("a.py")
    assert set(neighbors) == {
        "b.py",
        "c.py",
        "d.py",
    }  # b, c imported; d imports a; b is also called
    b_relations = {e.relationship for e in neighbors["b.py"]}
    assert b_relations == {"imports", "calls"}
    assert {e.relationship for e in neighbors["d.py"]} == {"imported_by"}
    calls_edge = next(e for e in neighbors["b.py"] if e.relationship == "calls")
    assert (calls_edge.source, calls_edge.target) == ("a.py::run", "b.py::f")
    assert calls_edge.evidence.file == "a.py" and calls_edge.evidence.line == 4

    # from b.py's side the same relationship is "called_by", with the call site's evidence
    back = next(e for e in g.file_neighbors("b.py")["a.py"] if e.relationship == "called_by")
    assert (back.source, back.target) == ("b.py::f", "a.py::run")
    assert back.evidence.file == "a.py"
    # calls inside one file (b.h from b.f) never make a file its own neighbour
    assert "b.py" not in g.file_neighbors("b.py")
    assert g.file_neighbors("unknown.py") == {}


# --- stats and export ---------------------------------------------------------------------


def test_stats_and_json_export() -> None:
    g = build({"m.py": "def a(): ...\ndef b():\n    a()\n    unknown()\n"})
    assert g.stats.files == 1
    assert g.stats.nodes_by_type == {"file": 1, "function": 2}
    assert g.stats.edges_by_type == {"defines": 2, "calls": 1}
    assert (g.stats.calls_total, g.stats.calls_unresolved) == (2, 1)
    assert g.stats.calls_resolved_by_rule == {"local": 1}
    exported = json.loads(g.to_json())
    assert exported["stats"]["files"] == 1
    assert {n["id"] for n in exported["graph"]["nodes"]} == {"m.py", "m.py::a", "m.py::b"}


# --- function-local imports are scoped to their function (M10 follow-up, found in M12) ------------

ALIAS_MODULES = {
    "pkg/__init__.py": "",
    "pkg/client.py": "class Client:\n    pass\n",
    "pkg/runner.py": "class Runner:\n    pass\n",
    "pkg/util.py": "def helper(): ...\n",
}


def test_two_functions_reusing_an_alias_resolve_to_their_own_import() -> None:
    """The Flask case: `from .testing import FlaskClient as cls` in one method and
    `... import FlaskCliRunner as cls` in another."""
    g = build(
        {
            **ALIAS_MODULES,
            "pkg/app.py": """
                class App:
                    def make_client(self):
                        from .client import Client as cls
                        return cls()

                    def make_runner(self):
                        from .runner import Runner as cls
                        return cls()
            """,
        }
    )
    assert calls(g, "pkg/app.py::App.make_client", "pkg/client.py::Client")
    assert calls(g, "pkg/app.py::App.make_runner", "pkg/runner.py::Runner")
    assert not calls(g, "pkg/app.py::App.make_client", "pkg/runner.py::Runner")
    assert not calls(g, "pkg/app.py::App.make_runner", "pkg/client.py::Client")


def test_a_local_import_does_not_leak_into_other_functions() -> None:
    g = build(
        {
            **ALIAS_MODULES,
            "pkg/app.py": """
                def uses_it():
                    from .client import Client
                    return Client()

                def does_not():
                    return Client()
            """,
        }
    )
    assert calls(g, "pkg/app.py::uses_it", "pkg/client.py::Client")
    assert not calls(g, "pkg/app.py::does_not", "pkg/client.py::Client")


def test_a_local_import_shadows_the_module_level_binding_only_inside_its_function() -> None:
    g = build(
        {
            **ALIAS_MODULES,
            "pkg/app.py": """
                from .client import Client as Thing

                def top():
                    return Thing()

                def local():
                    from .runner import Runner as Thing
                    return Thing()
            """,
        }
    )
    assert calls(g, "pkg/app.py::top", "pkg/client.py::Client")
    assert calls(g, "pkg/app.py::local", "pkg/runner.py::Runner")
    assert not calls(g, "pkg/app.py::local", "pkg/client.py::Client")


def test_local_module_imports_and_class_receivers_are_scoped_too() -> None:
    g = build(
        {
            **ALIAS_MODULES,
            "pkg/client.py": "class Client:\n    @classmethod\n    def create(cls): ...\n",
            "pkg/app.py": """
                def f():
                    import pkg.util as u
                    from .client import Client
                    u.helper()
                    Client.create()

                def g():
                    u.helper()
            """,
        }
    )
    assert calls(g, "pkg/app.py::f", "pkg/util.py::helper")
    assert calls(g, "pkg/app.py::f", "pkg/client.py::Client.create")
    assert not calls(g, "pkg/app.py::g", "pkg/util.py::helper")


def test_module_level_imports_inside_if_and_try_stay_module_level() -> None:
    g = build(
        {
            **ALIAS_MODULES,
            "pkg/app.py": """
                try:
                    from .client import Client
                except ImportError:
                    Client = None

                def f():
                    return Client()
            """,
        }
    )
    assert calls(g, "pkg/app.py::f", "pkg/client.py::Client")


def test_parser_records_the_scope_of_each_import() -> None:
    code = (
        "import os\n\ndef f():\n    import json\n\nclass C:\n    import re\n\n"
        "    def m(self):\n        from a import b\n"
    )
    imports = {i.module: i.scope for i in parse_python_source(code.encode(), "x.py").imports}
    assert imports == {"os": None, "json": "f", "re": "C", "a": "C.m"}
