from pathlib import Path

import pytest

from app.config import get_settings
from app.evaluation.dataset import (
    EvalDataset,
    EvalQuestion,
    RelevantSymbol,
    compute_questions_sha256,
    expected_difficulty,
    load_dataset,
)
from app.evaluation.validate import (
    check_against_repo,
    check_structure,
    freeze_dataset,
    get_head_commit,
)

DATASET_PATH = Path(__file__).resolve().parents[2] / "evaluation" / "datasets" / "flask.json"


def question(id: str = "q-1", **overrides: object) -> EvalQuestion:
    fields: dict[str, object] = {
        "id": id,
        "question": f"Where is thing {id}?",
        "category": "definition",
        "difficulty": "easy",
        "primary": ["pkg/a.py"],
        "supporting": [],
        "relevant_symbols": [],
        "evidence": "read pkg/a.py",
    }
    fields.update(overrides)
    return EvalQuestion.model_validate(fields)


def dataset(*questions: EvalQuestion) -> EvalDataset:
    return EvalDataset(
        name="t",
        repository="acme/widget",
        commit="0" * 40,
        description="",
        methodology={},
        questions=list(questions),
    )


# --- the committed dataset ------------------------------------------------------------


def test_committed_dataset_is_well_formed_and_frozen() -> None:
    ds = load_dataset(DATASET_PATH)
    assert check_structure(ds) == []  # includes the rubric and fingerprint checks
    assert ds.frozen and ds.frozen_on and ds.questions_sha256
    assert 50 <= len(ds.questions) <= 100
    assert {q.category for q in ds.questions} == {
        "definition",
        "concept",
        "caller",
        "dependency",
        "flow",
    }
    assert {q.difficulty for q in ds.questions} == {"easy", "medium", "hard"}
    assert len(ds.commit) == 40
    for q in ds.questions:
        assert all(f.startswith("src/flask/") for f in q.relevant_files), q.id


def test_committed_dataset_is_true_of_the_pinned_checkout() -> None:
    ds = load_dataset(DATASET_PATH)
    owner, name = ds.repository.split("/")
    repo = get_settings().repos_dir / owner / name
    if get_head_commit(repo) != ds.commit:
        pytest.skip(
            f"no checkout of {ds.repository} at {ds.commit[:10]} (see evaluation/README.md)"
        )
    assert check_against_repo(ds, repo) == []


# --- schema ---------------------------------------------------------------------------


def test_relevant_files_is_primary_then_supporting() -> None:
    q = question(primary=["a.py"], supporting=["b.py", "c.py"])
    assert q.relevant_files == ["a.py", "b.py", "c.py"]


def test_question_needs_a_primary_file() -> None:
    with pytest.raises(ValueError):
        question(primary=[])


@pytest.mark.parametrize(
    ("category", "n_files", "expected"),
    [
        ("definition", 1, "easy"),
        ("definition", 2, "medium"),
        ("caller", 3, "medium"),
        ("dependency", 4, "hard"),
        ("dependency", 5, "hard"),
        ("concept", 1, "medium"),
        ("concept", 2, "hard"),
        ("flow", 1, "medium"),
        ("flow", 3, "hard"),
    ],
)
def test_difficulty_rubric(category: str, n_files: int, expected: str) -> None:
    assert expected_difficulty(category, n_files) == expected


# --- the validator catches broken ground truth ---------------------------------------


def test_structure_check_flags_duplicates_and_inconsistencies() -> None:
    ds = dataset(
        question("a"),
        question("a", question="Something else?"),  # duplicate id
        question("b", question="Where is thing a?"),  # duplicate text (case-insensitive)
        question("c", primary=["pkg/a.py"], supporting=["pkg/a.py"]),
        question("d", evidence="  "),
        question("e", relevant_symbols=[RelevantSymbol(file="pkg/other.py", name="f")]),
        question("f", difficulty="hard"),  # one file named question is easy
    )
    problems = "\n".join(check_structure(ds))
    assert "duplicate id: 'a'" in problems
    assert "duplicate question: 'where is thing a?'" in problems
    assert "c: a file appears more than once in primary/supporting" in problems
    assert "d: evidence is empty" in problems
    assert "e: symbol f is in pkg/other.py, which is not a primary/supporting file" in problems
    assert "f: difficulty is 'hard' but the rubric gives 'easy'" in problems


def test_supporting_files_count_towards_difficulty_and_symbols() -> None:
    ok = question(
        category="concept",
        difficulty="hard",
        primary=["pkg/a.py"],
        supporting=["pkg/b.py"],
        relevant_symbols=[RelevantSymbol(file="pkg/b.py", name="f")],
    )
    assert check_structure(dataset(ok)) == []


# --- freezing --------------------------------------------------------------------------


def test_freeze_records_fingerprint_and_detects_later_edits(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(dataset(question("a"), question("b")).model_dump_json(), encoding="utf-8")

    frozen = freeze_dataset(path, "2026-10-06")
    assert frozen.frozen and frozen.frozen_on == "2026-10-06"
    assert frozen.questions_sha256 == compute_questions_sha256(frozen.questions)
    assert check_structure(load_dataset(path)) == []

    # Quietly "fixing" a question after the fact must be caught.
    edited = load_dataset(path)
    edited.questions[0].primary = ["pkg/other.py"]
    problems = check_structure(edited)
    assert len(problems) == 1
    assert "frozen but its questions no longer match" in problems[0]


def test_fingerprint_changes_with_any_field() -> None:
    base = [question("a")]
    for change in (
        {"question": "Different?"},
        {"primary": ["pkg/z.py"]},
        {"difficulty": "hard"},
        {"evidence": "x"},
    ):
        assert compute_questions_sha256([question("a", **change)]) != compute_questions_sha256(base)


def test_repo_check_flags_missing_files_and_symbols(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("class A:\n    def run(self):\n        pass\n")
    ds = dataset(
        question("ok", relevant_symbols=[RelevantSymbol(file="pkg/a.py", name="A.run")]),
        question("no-file", primary=["pkg/missing.py"]),
        question("no-supporting", supporting=["pkg/gone.py"], category="flow", difficulty="hard"),
        question("no-symbol", relevant_symbols=[RelevantSymbol(file="pkg/a.py", name="A.nope")]),
    )
    assert check_against_repo(ds, tmp_path) == [
        "no-file: file does not exist: pkg/missing.py",
        "no-supporting: file does not exist: pkg/gone.py",
        "no-symbol: symbol 'A.nope' not found in pkg/a.py",
    ]
