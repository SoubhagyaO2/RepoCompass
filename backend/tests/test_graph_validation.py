import json

import pytest

from app.config import get_settings
from app.evaluation.validate import get_head_commit
from app.graph.builder import build_graph, build_graph_from_parsed
from app.graph.validate import (
    DEFAULT_FACTS,
    ast_import_edges,
    check_facts,
    graph_import_edges,
)
from app.ingestion.discovery import discover_files
from app.models.repository import IngestedRepository, RepoRef
from app.parsing.python_parser import parse_python_source

FACTS = json.loads(DEFAULT_FACTS.read_text(encoding="utf-8"))
KINDS = {"calls", "imports", "inherits", "defines"}
EXPECTATIONS = {"present", "absent", "known_gap"}


def test_the_facts_file_is_well_formed() -> None:
    facts = FACTS["facts"]
    assert len({f["id"] for f in facts}) == len(facts)
    assert {f["kind"] for f in facts} <= KINDS
    assert {f["expect"] for f in facts} <= EXPECTATIONS
    assert all(f["source"] and f["target"] and f["evidence"] for f in facts)
    assert len(FACTS["commit"]) == 40


def test_the_flask_graph_matches_the_ast_module_and_every_hand_verified_fact() -> None:
    repo = get_settings().repos_dir / "pallets" / "flask"
    if get_head_commit(repo) != FACTS["commit"]:
        pytest.skip("no checkout of pallets/flask at the pinned commit")
    files = discover_files(repo)
    ingested = IngestedRepository(
        ref=RepoRef(owner="pallets", name="flask"), local_path=repo, files=files
    )
    graph = build_graph(ingested)

    python_files = [f.path for f in files if f.language == "Python"]
    assert graph_import_edges(graph) == ast_import_edges(repo, python_files)  # V1

    failures = [r for r in check_facts(graph, FACTS["facts"]) if not r["ok"]]  # V2
    assert failures == []


def test_check_facts_reports_each_kind_of_failure() -> None:
    parsed = {"m.py": parse_python_source(b"def a(): ...\ndef b():\n    a()\n", "m.py")}
    graph = build_graph_from_parsed(parsed)
    facts = [
        {
            "id": "ok-present",
            "kind": "calls",
            "expect": "present",
            "source": "m.py::b",
            "target": "m.py::a",
        },
        {
            "id": "missing",
            "kind": "calls",
            "expect": "present",
            "source": "m.py::a",
            "target": "m.py::b",
        },
        {
            "id": "false-positive",
            "kind": "calls",
            "expect": "absent",
            "source": "m.py::b",
            "target": "m.py::a",
        },
        {
            "id": "gap-closed",
            "kind": "calls",
            "expect": "known_gap",
            "source": "m.py::b",
            "target": "m.py::a",
        },
        {
            "id": "no-node",
            "kind": "calls",
            "expect": "present",
            "source": "m.py::zzz",
            "target": "m.py::a",
        },
        {
            "id": "ok-absent",
            "kind": "calls",
            "expect": "absent",
            "source": "m.py::a",
            "target": "m.py::b",
        },
    ]
    results = {r["id"]: r for r in check_facts(graph, facts)}
    assert [i for i, r in results.items() if r["ok"]] == ["ok-present", "ok-absent"]
    assert "not in the graph" in results["missing"]["problem"]
    assert "false positive" in results["false-positive"]["problem"]
    assert "known gap" in results["gap-closed"]["problem"]
    assert "missing node" in results["no-node"]["problem"]
