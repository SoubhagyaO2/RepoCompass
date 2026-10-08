import textwrap

from app.evaluation.dataset import EvalQuestion
from app.evaluation.graph_answers import compare_with_benchmark, graph_answer
from app.graph.builder import build_graph_from_parsed
from app.parsing.python_parser import parse_python_source

FILES = {
    "src/flask/app.py": """
        from .helpers import go
        from .sessions import S
        class Flask:
            def run(self):
                go()
                self.step()
            def step(self): ...
    """,
    "src/flask/helpers.py": "def go(): ...\n",
    "src/flask/sessions.py": "class S: ...\n",
    "src/flask/cli.py": "from .app import Flask\nfrom .helpers import go\ndef main():\n    go()\n",
    "tests/test_x.py": "from flask.helpers import go\ndef test():\n    go()\n",
}
GRAPH = build_graph_from_parsed(
    {p: parse_python_source(textwrap.dedent(c).encode(), p) for p, c in FILES.items()}
)


def q(id: str, category: str, question: str, primary: list[str]) -> EvalQuestion:
    return EvalQuestion.model_validate(
        {
            "id": id, "question": question, "category": category, "difficulty": "easy",
            "primary": primary, "evidence": "e",
        }
    )  # fmt: skip


def test_caller_questions_use_the_callers_of_every_matching_definition_inside_the_library() -> None:
    question = q("c1", "caller", "What calls `go`?", ["src/flask/app.py"])
    # the test file's call to go() is outside src/flask/ and so out of the benchmark's scope
    assert graph_answer(GRAPH, question) == {"src/flask/app.py", "src/flask/cli.py"}
    qualified = q("c2", "caller", "What calls `Flask.step`?", ["src/flask/app.py"])
    assert graph_answer(GRAPH, qualified) == {"src/flask/app.py"}


def test_dependency_questions_pick_the_direction_from_the_wording() -> None:
    importers = q("d1", "dependency", "Which files import `flask.helpers`?", ["x"])
    assert graph_answer(GRAPH, importers) == {"src/flask/app.py", "src/flask/cli.py"}
    forward = q("d2", "dependency", "Which other modules does `flask/app.py` import?", ["x"])
    assert graph_answer(GRAPH, forward) == {"src/flask/helpers.py", "src/flask/sessions.py"}
    depend = q("d3", "dependency", "Which other modules does `flask/cli.py` depend on?", ["x"])
    assert graph_answer(GRAPH, depend) == {"src/flask/app.py", "src/flask/helpers.py"}


def test_other_categories_have_no_graph_answer() -> None:
    assert graph_answer(GRAPH, q("f", "flow", "How does `go` work?", ["a"])) is None
    assert graph_answer(GRAPH, q("n", "caller", "What calls it?", ["a"])) is None


def test_precision_recall_and_exact_match() -> None:
    questions = [
        q("c1", "caller", "What calls `go`?", ["src/flask/app.py", "src/flask/cli.py"]),
        q("c2", "caller", "What calls `go`?", ["src/flask/app.py", "src/flask/other.py"]),
        q("d1", "dependency", "Which files import `flask.helpers`?", ["src/flask/app.py"]),
        q("f1", "flow", "How does `go` work?", ["src/flask/app.py"]),
    ]
    result = compare_with_benchmark(GRAPH, questions)
    assert [r["id"] for r in result["per_question"]] == ["c1", "c2", "d1"]  # flow is skipped
    c1, c2, d1 = result["per_question"]
    assert c1["exact"] and c1["precision"] == 1.0 and c1["recall"] == 1.0
    assert (c2["precision"], c2["recall"]) == (0.5, 0.5)
    assert c2["missing"] == ["src/flask/other.py"] and c2["extra"] == ["src/flask/cli.py"]
    assert (d1["precision"], d1["recall"]) == (0.5, 1.0)
    summary = result["summary"]
    assert summary["caller"] == {
        "n": 2,
        "exact_match": 1,
        "mean_precision": 0.75,
        "mean_recall": 0.75,
    }
    assert summary["dependency"]["exact_match"] == 0
