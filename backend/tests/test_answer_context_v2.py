"""The v2 context for the answer layer (Milestone 14): ids E3b/E3c, the 72,000-character budget, and
the UNCHANGED validator accepting those ids (plan section 3, checked before any live run)."""

import hashlib
from pathlib import Path

import pytest
from answer_fixtures import FakeLLM, claim, make_evidence_v2, output

from app.answer.context import CONTEXT_BUDGET_CHARS_V2, build_context_v2
from app.answer.prompt import PROMPT_SHA256
from app.answer.service import AnswerService
from app.answer.validate import validate_output

QUESTION = "Where is the session cookie saved?"
ROOT = Path(__file__).resolve().parents[2]


def context(budget: int = CONTEXT_BUDGET_CHARS_V2):
    return build_context_v2(QUESTION, make_evidence_v2(), budget)


def test_the_budget_is_three_times_the_m13_budget() -> None:
    assert CONTEXT_BUDGET_CHARS_V2 == 72_000


def test_ids_follow_each_files_primary_block_in_rank_order() -> None:
    ids = [c.id for c in context().items]
    assert ids == ["E1", "E1b", "E1c", "E2", "E2b", "E3"]


def test_additional_blocks_use_the_same_layout_without_scores_or_structure() -> None:
    text = context().text
    assert "[E1b] file: src/pkg/sessions.py" in text
    assert "----- BEGIN TEXT E1b -----" in text and "----- END TEXT E1c -----" in text
    block = text.split("[E1b]")[1].split("----- END TEXT E1b -----")[0]
    assert "structure" not in block and "rrf" not in block and "score" not in block
    assert "Evidence items shown: 6. Items omitted for space: 0." in text


def test_the_primary_blocks_equal_the_v1_blocks() -> None:
    from answer_fixtures import make_evidence

    from app.answer.context import build_context

    v1 = build_context(QUESTION, make_evidence()).text
    v2 = context().text
    for block_id in ("E1", "E2", "E3"):
        v1_block = v1.split(f"[{block_id}]")[1].split(f"----- END TEXT {block_id} -----")[0]
        v2_block = v2.split(f"[{block_id}]")[1].split(f"----- END TEXT {block_id} -----")[0]
        assert v1_block == v2_block


def test_budget_omits_whole_blocks_and_everything_after_them() -> None:
    ctx = context()
    sizes = [len(c.item.text or "") for c in ctx.items]
    tight = build_context_v2(QUESTION, make_evidence_v2(), budget_chars=sum(sizes[:3]) + 1)
    assert [c.id for c in tight.items] == ["E1", "E1b", "E1c"]
    assert tight.omitted == 3
    assert "Items omitted for space: 3" in tight.text


def test_unchanged_validator_accepts_citations_of_additional_chunks() -> None:
    ctx = context()
    ok = output(claims=[claim("It is saved by SessionInterface.save_session.", "E1b", "E1c")])
    result = validate_output(ok, ctx)
    assert result.valid, result.reasons
    resolved = {c.id: c.item for c in ctx.items}
    assert resolved["E1b"].line_start != resolved["E1"].line_start


def test_unchanged_validator_still_rejects_fabrications_on_additional_ids() -> None:
    ctx = context()
    by_id = ctx.by_id()
    inside = by_id["E1b"].item.line_start
    cases = {
        "unknown id": output(claims=[claim("Saved.", "E1d")]),
        "line outside the cited range": output(claims=[claim("See line 9999.", "E1b")]),
        "duplicate id": output(claims=[claim("Saved.", "E1b", "E1b")]),
        "fabricated path": output(claims=[claim("It is in src/pkg/nowhere.py.", "E1c")]),
        "unknown symbol": output(claims=[claim("It calls `frobnicate()`.", "E1b")]),
    }
    for name, raw in cases.items():
        assert not validate_output(raw, ctx).valid, name
    assert validate_output(output(claims=[claim(f"See line {inside}.", "E1b")]), ctx).valid


def test_line_numbers_are_checked_against_the_additional_chunks_own_range() -> None:
    ctx = context()
    primary_line = ctx.by_id()["E1"].item.line_start
    extra = ctx.by_id()["E1b"].item
    assert not (extra.line_start <= primary_line <= extra.line_end)
    bad = output(claims=[claim(f"See line {primary_line}.", "E1b")])
    assert not validate_output(bad, ctx).valid  # the primary's line is outside E1b's range


def test_the_prompt_and_the_validator_are_the_frozen_ones() -> None:
    assert PROMPT_SHA256 == "26988fdf9914f8635435bd76a09853b131629c3e92622bcc6f717242e6914820"
    source = (Path(__file__).resolve().parents[1] / "app" / "answer" / "validate.py").read_bytes()
    assert hashlib.sha256(source.replace(b"\r\n", b"\n")).hexdigest() == (
        "909ab2133d33cfe5c6d676df5fe173f1b48b003f4ae60365956955d5241a5322"
    )


def test_the_service_uses_the_v2_context_for_v2_evidence_and_keeps_v1_for_v1() -> None:
    from answer_fixtures import make_evidence

    llm = FakeLLM(output(claims=[claim("Saved.", "E1b")]))
    answer = AnswerService(None, llm).answer_from_evidence(QUESTION, make_evidence_v2())
    assert answer.status == "answered" and answer.items_shown == 6
    assert "[E1b]" in llm.requests[0].user
    assert answer.claims[0].citations[0].evidence_id == "E1b"
    assert answer.evidence.schema_version == 2
    dumped = answer.model_dump(mode="json")["evidence"]["evidence"][0]
    assert dumped["additional_chunks"]  # not dropped by serialisation

    llm1 = FakeLLM(output())
    v1 = AnswerService(None, llm1).answer_from_evidence(QUESTION, make_evidence())
    assert v1.items_shown == 3 and "[E1b]" not in llm1.requests[0].user


def test_a_budget_is_carried_per_version() -> None:
    from answer_fixtures import make_evidence

    big = make_evidence_v2()
    service = AnswerService(None, FakeLLM(output()), budget_chars=1)  # the v1 budget only
    assert service.answer_from_evidence(QUESTION, big).items_shown == 6  # v2 uses its own budget
    assert service.answer_from_evidence(QUESTION, make_evidence()).items_shown == 0


@pytest.mark.parametrize("n", [0, 1, 2])
def test_extra_zero_context_equals_the_v1_context_text(n: int) -> None:
    from answer_fixtures import make_evidence, make_evidence_v2

    from app.answer.context import build_context

    v2 = make_evidence_v2(extra=n)
    assert [c.id for c in build_context_v2(QUESTION, v2).items][: 1 + n] == ["E1"] + [
        f"E1{'bc'[k]}" for k in range(n)
    ]
    if n == 0:
        assert build_context_v2(QUESTION, v2).text == build_context(QUESTION, make_evidence()).text
