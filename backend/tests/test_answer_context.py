"""Prompt, context builder, import boundary (plan section 6, tests 3-4)."""

import ast
import re
from pathlib import Path

import pytest
from answer_fixtures import QUESTION, make_context, make_evidence

from app.answer.context import ContextError, build_context
from app.answer.prompt import PROMPT_SHA256, SYSTEM_PROMPT

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "evaluation" / "plans" / "m13-grounded-answers.md"


def test_system_prompt_is_the_one_frozen_in_the_plan() -> None:
    plan = PLAN.read_text(encoding="utf-8")
    start = plan.index("**System prompt (frozen verbatim")
    block = re.search(r"```\n(.*?)\n```", plan[start:], re.DOTALL)
    assert block is not None
    assert block.group(1).replace("\r\n", "\n") == SYSTEM_PROMPT
    assert len(PROMPT_SHA256) == 64


def test_context_golden_text() -> None:
    expected = """\
Question: Where is the session cookie saved?

Evidence items shown: 3. Items omitted for space: 0.

[E1] file: src/pkg/sessions.py
lines: 10-20
symbol: SessionInterface.save_session
category: source
structure (how this code is connected; NOT evidence of relevance):
  - calls src/pkg/helpers.py::dump_cookie (rule: import)
----- BEGIN TEXT E1 -----
def save_session(self, app, session, response):
    name = self.get_cookie_name(app)
    response.set_cookie(name, dump_cookie(session))

----- END TEXT E1 -----

[E2] file: src/pkg/app.py
lines: 100-110
symbol: App.process_response
category: source
----- BEGIN TEXT E2 -----
def process_response(self, response):
    ctx = request_ctx
    if not self.session_interface.is_null_session(ctx.session):
        self.session_interface.save_session(self, ctx.session, response)
    return response

----- END TEXT E2 -----

[E3] file: tests/test_sessions.py
lines: 5-9
symbol: test_save
category: test
----- BEGIN TEXT E3 -----
def test_save():
    assert True

----- END TEXT E3 -----
"""
    assert make_context().text == expected


def test_scores_are_not_shown() -> None:
    text = make_context().text
    assert "rrf" not in text and "score" not in text and "5.0" not in text


def test_budget_omits_whole_items_never_truncates() -> None:
    first = len(make_evidence().evidence[0].text or "")
    second = len(make_evidence().evidence[1].text or "")
    context = make_context(budget=first + second)
    assert [c.id for c in context.items] == ["E1", "E2"]
    assert context.omitted == 1
    assert "Evidence items shown: 2. Items omitted for space: 1." in context.text
    assert "E3" not in context.text
    assert (make_evidence().evidence[1].text or "") in context.text  # complete, not cut


def test_an_item_that_does_not_fit_also_omits_every_later_item() -> None:
    first = len(make_evidence().evidence[0].text or "")
    context = make_context(budget=first + 1)  # E2 does not fit, E3 would
    assert [c.id for c in context.items] == ["E1"]
    assert context.omitted == 2


def test_nothing_fits() -> None:
    context = make_context(budget=1)
    assert context.items == () and context.omitted == 3


def test_ids_follow_ranks_and_resolve() -> None:
    context = make_context()
    assert list(context.by_id()) == ["E1", "E2", "E3"]
    assert context.by_id()["E2"].item.file == "src/pkg/app.py"


def test_items_without_text_are_an_error() -> None:
    evidence = make_evidence()
    evidence.evidence[0].text = None
    with pytest.raises(ContextError):
        build_context(QUESTION, evidence)


def test_repository_text_appears_only_inside_text_blocks() -> None:
    text = make_context().text
    outside = re.sub(
        r"----- BEGIN TEXT (E\d) -----.*?----- END TEXT \1 -----", "", text, flags=re.DOTALL
    )
    for snippet in ("get_cookie_name", "is_null_session", "assert True"):
        assert snippet in text and snippet not in outside


# --- boundary (rules R1, R2) --------------------------------------------------------------------

FORBIDDEN_PREFIXES = (
    "app.indexing", "app.retrieval", "app.graph", "app.parsing", "app.chunking", "app.ingestion",
    "app.evaluation", "app.evidence.builder", "app.evidence.service", "app.evidence.validate",
    "app.evidence.smoke", "app.config", "os", "pathlib", "shutil", "subprocess", "socket", "urllib",
    "http", "requests", "httpx", "qdrant_client", "networkx", "sentence_transformers", "io",
    "tempfile",
)  # fmt: skip


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def test_the_answer_layer_cannot_reach_the_repository_or_the_network() -> None:
    package = Path(__file__).resolve().parents[1] / "app" / "answer"
    files = sorted(package.glob("*.py"))
    assert len(files) >= 5
    for file in files:
        for module in _imports(file):
            bad = [p for p in FORBIDDEN_PREFIXES if module == p or module.startswith(p + ".")]
            assert not bad, f"{file.name} imports {module}"


def test_answer_layer_does_not_open_files() -> None:
    package = Path(__file__).resolve().parents[1] / "app" / "answer"
    for file in package.glob("*.py"):
        source = file.read_text(encoding="utf-8")
        assert "open(" not in source and "read_text" not in source, file.name
