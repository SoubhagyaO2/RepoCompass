"""Build the code graph from parsed Python files.

The call-resolution rules are those pre-registered in evaluation/plans/m10-code-graph.md.
Names are resolved statically and never by guessing between alternatives:

1. foo()            -> a top-level function/class `foo` in this file, else a name bound by an
                       import (re-exports followed up to MAX_REEXPORT_DEPTH levels)
2. mod.foo()        -> `foo` defined in the imported module `mod`
3. self.foo() /     -> method `foo` on the enclosing class, else on its base classes (depth-first,
   cls.foo()          left to right); super().foo() looks in the base classes only
   Class.foo()     -> the same lookup on the named class
4. obj.foo()        -> the method `foo` only if exactly ONE class in the repository defines a
                       method with that name; several -> skipped as ambiguous

Not modelled: dynamic dispatch to subclass overrides, methods passed as values, getattr/strings.
"""

import time
from collections import Counter
from dataclasses import dataclass
from typing import Literal

import networkx as nx

from app.chunking.categories import classify_file
from app.graph.model import BuildStats, CodeGraph
from app.models.repository import IngestedRepository
from app.models.symbols import Call, Import, ParsedFile, Symbol
from app.parsing.python_parser import parse_python_file

MAX_REEXPORT_DEPTH = 5


def _builtin_method_names() -> frozenset[str]:
    """Public method names of builtin container/string/file types: `x.setdefault(...)` may be a
    dict however many repository classes define a method of that name."""
    import io

    types = (dict, list, set, frozenset, tuple, str, bytes, bytearray, int, float)
    types += (io.StringIO, io.BytesIO)
    return frozenset(n for t in types for n in dir(t) if not n.startswith("_"))


BUILTIN_METHOD_NAMES = _builtin_method_names()


def module_name(path: str) -> str:
    """Dotted module name of a file: src/flask/app.py -> flask.app; pkg/__init__.py -> pkg."""
    parts = path.removesuffix(".py").split("/")
    if parts[0] == "src" and len(parts) > 1:
        parts = parts[1:]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


@dataclass(frozen=True)
class _Binding:
    kind: Literal["module", "name"]  # a module object, or a name defined in `path`
    path: str
    name: str | None = None


def build_graph(ingested: IngestedRepository) -> CodeGraph:
    parsed = {
        f.path: parse_python_file(ingested.local_path, f.path)
        for f in ingested.files
        if f.language == "Python"
    }
    return build_graph_from_parsed(parsed)


def build_graph_from_parsed(parsed: dict[str, ParsedFile]) -> CodeGraph:
    return _Builder(parsed).build()


class _Builder:
    def __init__(self, parsed: dict[str, ParsedFile]) -> None:
        self.parsed = parsed
        self.paths = sorted(parsed)
        self.module_to_path: dict[str, str] = {}
        self.path_to_module: dict[str, str] = {}
        for path in self.paths:
            self.path_to_module[path] = module_name(path)
            self.module_to_path.setdefault(self.path_to_module[path], path)

        # per file: top-level symbols by name, and all symbols by qualified name (first wins)
        self.top_level: dict[str, dict[str, Symbol]] = {}
        self.members: dict[str, dict[str, Symbol]] = {}
        self.methods_by_name: dict[str, list[tuple[str, str]]] = {}
        for path in self.paths:
            self.top_level[path], self.members[path] = {}, {}
            for s in parsed[path].symbols:
                self.members[path].setdefault(s.qualified_name, s)
                if s.parent is None:
                    self.top_level[path].setdefault(s.name, s)
        for path in self.paths:
            seen: set[str] = set()
            for s in parsed[path].symbols:
                if s.kind == "method" and s.qualified_name not in seen:
                    seen.add(s.qualified_name)
                    self.methods_by_name.setdefault(s.name, []).append((path, s.qualified_name))

        self.bindings: dict[str, dict[str, _Binding]] = {p: {} for p in self.paths}
        # names a file binds to code outside the repository (`import click`, `from x import Y`)
        self.external: dict[str, set[str]] = {p: set() for p in self.paths}
        self._bases_cache: dict[tuple[str, str], list[tuple[str, str]]] = {}

    # --- import resolution ---------------------------------------------------------------

    def _target_module(self, path: str, imp: Import) -> str | None:
        """Dotted name of the module an import statement refers to, or None."""
        if imp.level == 0:
            return imp.module or None
        package = self.path_to_module[path].split(".")
        if not path.endswith("__init__.py"):
            package = package[:-1]
        if imp.level - 1 > len(package):
            return None
        base = package[: len(package) - (imp.level - 1)]
        name = ".".join([*base, *([imp.module] if imp.module else [])])
        return name or None

    def _resolve_import(self, path: str, imp: Import) -> str | None:
        """The repository file an import statement points at (submodule first), or None."""
        module = self._target_module(path, imp)
        if module is None:
            return None
        if imp.name and imp.name != "*":
            submodule = self.module_to_path.get(f"{module}.{imp.name}")
            if submodule:
                return submodule
        return self.module_to_path.get(module)

    def _build_bindings(self, path: str) -> None:
        for imp in self.parsed[path].imports:
            module = self._target_module(path, imp)
            if module is None:
                continue
            if imp.name is None:  # import a.b [as c]
                target = self.module_to_path.get(module)
                if target is None:
                    self.external[path].add(imp.alias or module.split(".")[0])
                    continue
                if imp.alias:
                    self.bindings[path][imp.alias] = _Binding("module", target)
                else:
                    self.bindings[path][module] = _Binding("module", target)
                    first = module.split(".")[0]
                    if first in self.module_to_path:
                        self.bindings[path].setdefault(
                            first, _Binding("module", self.module_to_path[first])
                        )
            elif imp.name != "*":  # from m import n [as a]
                bound = imp.alias or imp.name
                submodule = self.module_to_path.get(f"{module}.{imp.name}")
                if submodule:
                    self.bindings[path][bound] = _Binding("module", submodule)
                elif module in self.module_to_path:
                    self.bindings[path][bound] = _Binding(
                        "name", self.module_to_path[module], imp.name
                    )
                else:
                    self.external[path].add(bound)

    # --- symbol resolution ---------------------------------------------------------------

    def _resolve_exported(self, path: str, name: str, depth: int = 0) -> tuple[str, str] | None:
        """A top-level symbol visible as `name` in `path`: defined there or re-exported."""
        if depth > MAX_REEXPORT_DEPTH:
            return None
        if name in self.top_level[path]:
            return path, name
        binding = self.bindings[path].get(name)
        if binding and binding.kind == "name" and binding.name:
            return self._resolve_exported(binding.path, binding.name, depth + 1)
        return None

    def _is_class(self, ref: tuple[str, str] | None) -> bool:
        return ref is not None and self.members[ref[0]][ref[1]].kind == "class"

    def _resolve_base(self, path: str, text: str) -> tuple[str, str] | None:
        if "." not in text:
            ref = self._resolve_exported(path, text)
        else:
            left, _, right = text.rpartition(".")
            binding = self.bindings[path].get(left)
            ref = (
                self._resolve_exported(binding.path, right)
                if binding and binding.kind == "module"
                else None
            )
        return ref if self._is_class(ref) else None

    def _bases(self, path: str, class_q: str) -> list[tuple[str, str]]:
        key = (path, class_q)
        if key not in self._bases_cache:
            resolved = [
                ref
                for base in self.members[path][class_q].bases
                if (ref := self._resolve_base(path, base)) is not None
            ]
            self._bases_cache[key] = resolved
        return self._bases_cache[key]

    def _find_method(
        self, path: str, class_q: str, method: str, seen: set[tuple[str, str]] | None = None
    ) -> tuple[str, str] | None:
        seen = seen if seen is not None else set()
        if (path, class_q) in seen:
            return None
        seen.add((path, class_q))
        candidate = self.members[path].get(f"{class_q}.{method}")
        if candidate is not None and candidate.kind in ("method", "function"):
            return path, candidate.qualified_name
        for base_path, base_q in self._bases(path, class_q):
            found = self._find_method(base_path, base_q, method, seen)
            if found:
                return found
        return None

    def _enclosing_class(self, path: str, caller: str | None) -> str | None:
        if caller is None:
            return None
        q = caller
        while "." in q:
            q = q.rsplit(".", 1)[0]
            symbol = self.members[path].get(q)
            if symbol is not None and symbol.kind == "class":
                return q
        return None

    # --- calls ---------------------------------------------------------------------------

    def _resolve_call(self, path: str, call: Call) -> tuple[tuple[str, str] | None, str]:
        """Returns (target, rule). Without a target, rule says why: 'ambiguous', 'unresolved',
        'external' (the receiver comes from outside the repository), 'builtin-name' (the method
        name also exists on builtin types) or 'self-recursion'."""
        name, receiver = call.name, call.receiver
        if receiver is None:
            target = self._resolve_exported(path, name)
            if target is None:
                return None, "unresolved"
            return target, "local" if name in self.top_level[path] else "import"

        if receiver in ("self", "cls", "super()"):
            class_q = self._enclosing_class(path, call.caller)
            if class_q is None:
                return None, "unresolved"
            if receiver == "super()":
                for base_path, base_q in self._bases(path, class_q):
                    found = self._find_method(base_path, base_q, name)
                    if found:
                        return found, "super"
                return None, "unresolved"
            found = self._find_method(path, class_q, name)
            return (found, "self") if found else (None, "unresolved")

        binding = self.bindings[path].get(receiver)
        if binding is not None and binding.kind == "module":
            target = self._resolve_exported(binding.path, name)
            return (target, "import-module") if target else (None, "unresolved")

        if receiver.split(".")[0] in self.external[path]:
            return None, "external"  # click.command(), werkzeug classes, ...: not our code

        if "." not in receiver:  # a class named directly: Class.method()
            class_ref = self._resolve_exported(path, receiver)
            if class_ref is not None and self._is_class(class_ref):
                # The receiver's type is known, so never guess by name: a method that is not
                # on the class or its resolvable bases is inherited from outside the repo.
                found = self._find_method(class_ref[0], class_ref[1], name)
                return (found, "class-attr") if found else (None, "unresolved")

        # The receiver's type is unknown (a variable or attribute chain): match by method name,
        # but only when that is unambiguous.
        if name in BUILTIN_METHOD_NAMES:
            return None, "builtin-name"
        candidates = self.methods_by_name.get(name, [])
        if len(candidates) == 1:
            if call.caller is not None and candidates[0] == (path, call.caller):
                return None, "self-recursion"  # `other.m()` inside `m` is delegation, not recursion
            return candidates[0], "unique-name"
        return None, "ambiguous" if candidates else "unresolved"

    # --- assembling the graph --------------------------------------------------------------

    def build(self) -> CodeGraph:
        start = time.perf_counter()
        graph: nx.MultiDiGraph[str] = nx.MultiDiGraph()

        for path in self.paths:
            self._build_bindings(path)
            graph.add_node(
                path, type="file", path=path, name=path, qualified_name=path,
                category=classify_file(path),
            )  # fmt: skip
            for s in self.parsed[path].symbols:
                node = f"{path}::{s.qualified_name}"
                if node in graph:
                    graph.nodes[node]["ranges"].append((s.start_line, s.end_line))
                    continue
                graph.add_node(
                    node, type=s.kind, path=path, name=s.name, qualified_name=s.qualified_name,
                    category=classify_file(path), ranges=[(s.start_line, s.end_line)],
                )  # fmt: skip
                owner = path if s.parent is None else f"{path}::{s.parent}"
                graph.add_edge(owner, node, key="defines", line=s.start_line, rule="definition")

        resolved: Counter[str] = Counter()
        skipped: Counter[str] = Counter()
        total = 0
        for path in self.paths:
            parsed = self.parsed[path]
            for imp in parsed.imports:
                target = self._resolve_import(path, imp)
                if target is not None and target != path:
                    _add_lines(graph, path, target, "imports", imp.line, "import")
            for s in parsed.symbols:
                if s.kind != "class":
                    continue
                for base in s.bases:
                    ref = self._resolve_base(path, base)
                    if ref is not None:
                        graph.add_edge(
                            f"{path}::{s.qualified_name}", f"{ref[0]}::{ref[1]}",
                            key="inherits", line=s.start_line, rule="base-class",
                        )  # fmt: skip
            for call in parsed.calls:
                total += 1
                target_ref, rule = self._resolve_call(path, call)
                if target_ref is None:
                    skipped[rule] += 1
                    continue
                resolved[rule] += 1
                source = path if call.caller is None else f"{path}::{call.caller}"
                _add_lines(
                    graph, source, f"{target_ref[0]}::{target_ref[1]}", "calls", call.line, rule
                )

        nodes = Counter(d["type"] for _, d in graph.nodes(data=True))
        edges = Counter(k for _, _, k in graph.edges(keys=True))
        stats = BuildStats(
            files=len(self.paths),
            nodes_by_type=dict(nodes),
            edges_by_type=dict(edges),
            calls_total=total,
            calls_resolved_by_rule=dict(resolved),
            calls_ambiguous=skipped["ambiguous"],
            calls_unresolved=skipped["unresolved"] + skipped["self-recursion"],
            calls_external=skipped["external"],
            calls_builtin_name=skipped["builtin-name"],
            build_seconds=time.perf_counter() - start,
        )
        return CodeGraph(graph, stats)


def _add_lines(
    graph: "nx.MultiDiGraph[str]", source: str, target: str, kind: str, line: int, rule: str
) -> None:
    """Add (or extend) one aggregated edge, keeping every line and rule that produced it."""
    if graph.has_edge(source, target, key=kind):
        data = graph.edges[source, target, kind]
        data["lines"].append(line)
        if rule not in data["rules"]:
            data["rules"].append(rule)
    else:
        graph.add_edge(source, target, key=kind, lines=[line], rules=[rule])
