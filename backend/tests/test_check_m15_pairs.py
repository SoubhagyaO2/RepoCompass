"""The pure parts of the M15 authoring checks, and the shape of the frozen-to-be datasets."""

import json
from pathlib import Path

from app.evaluation import check_m15_pairs as c

DATA = Path(__file__).resolve().parents[2] / "evaluation" / "datasets"


def test_content_tokens_ignore_stopwords_flask_plurals_and_possessives() -> None:
    assert c.content_tokens("Where does Flask store the user's sessions?") == {
        "store",
        "user",
        "session",
    }


def test_symmetry_needs_two_shared_words_and_similar_lengths() -> None:
    assert (
        c.check_symmetry(
            "Where does Flask render PDF templates?", "Where does Flask render templates?"
        )
        == []
    )
    assert c.check_symmetry(
        "Where does Flask render PDF?", "How does Flask send files?"
    )  # shares nothing
    long_q = "Where does Flask render templates " + "from many places " * 6 + "?"
    assert any("ratio" in x for x in c.check_symmetry("Where does Flask render templates?", long_q))


def test_format_check() -> None:
    assert c.check_format("Where does Flask render templates?") == []
    assert c.check_format("Where does Flask render templates.")
    assert c.check_format("Why?")


def test_absence_reports_the_files_that_match() -> None:
    texts = {"src/a.py": "import gzip\n", "tests/b.py": "x = 1\n"}
    assert c.check_absence(["gzip"], texts) and "src/a.py" in c.check_absence(["gzip"], texts)[0]
    assert c.check_absence(["zlib"], texts) == []


def test_selection_takes_the_first_passing_per_subtype_in_order() -> None:
    results = [{"id": f"s1-{i:02d}", "subtype": "S1", "passes": i != 3} for i in range(1, 15)]
    results += [{"id": f"s2-{i:02d}", "subtype": "S2", "passes": True} for i in range(1, 12)]
    results += [{"id": f"s3-{i:02d}", "subtype": "S3", "passes": True} for i in range(1, 11)]
    test, dev, short = c.select(results)
    assert len(test) == 24 and len(dev) == 8 and short == {}
    assert "s1-03" not in test and test[:2] == ["s1-01", "s1-02"]  # a failing candidate is skipped
    assert not set(test) & set(dev)


def test_selection_reports_a_shortfall() -> None:
    results = [{"id": f"s1-{i:02d}", "subtype": "S1", "passes": True} for i in range(1, 6)]
    _, _, short = c.select(results)
    assert "S1" in short and "S2" in short


def test_cue_balance_flags_a_one_sided_cue() -> None:
    pairs = [
        {"p": {"question": "Flask's built-in X?"}, "q": {"question": "Flask's Y?"}}
        for _ in range(3)
    ]
    assert c.cue_balance(pairs)["built-in"]["balanced"] is False
    pairs += [{"p": {"question": "A?"}, "q": {"question": "built-in B?"}} for _ in range(2)]
    assert c.cue_balance(pairs)["built-in"]["balanced"] is True


def test_the_authored_datasets_have_the_frozen_shape() -> None:
    test = json.loads((DATA / "m15_pairs_test.json").read_text(encoding="utf-8"))["pairs"]
    dev = json.loads((DATA / "m15_pairs_dev.json").read_text(encoding="utf-8"))["pairs"]
    assert len(test) == 24 and len(dev) == 8
    for subtype in ("S1", "S2", "S3"):
        assert sum(1 for x in test if x["subtype"] == subtype) == 8
    questions = [x[k]["question"] for x in test + dev for k in ("p", "q")]
    assert len(set(map(c.normalise, questions))) == len(questions)  # no duplicates anywhere
    assert not {x["candidate"] for x in test} & {x["candidate"] for x in dev}
    assert all(x["decoy"] for x in test if x["subtype"] != "S1")
    assert c.cue_balance(test)["built-in"]["balanced"]
