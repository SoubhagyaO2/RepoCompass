"""The deterministic output validator (plan section 4a and test group 1 of section 6)."""

import json

import pytest
from answer_fixtures import claim, make_context, output

from app.answer.validate import validate_output

CONTEXT = make_context()


def check(raw: str) -> tuple[bool, list[str]]:
    result = validate_output(raw, CONTEXT)
    return result.valid, result.reasons


# --- malformed outputs are rejected, one test per rule -------------------------------------------


@pytest.mark.parametrize(
    ("name", "raw", "expected"),
    [
        ("invalid json", "{not json", "not a single JSON object"),
        ("text around the json", "Sure! " + output(), "not a single JSON object"),
        ("code fence", "```json\n" + output() + "\n```", "not a single JSON object"),
        ("json list", json.dumps([1]), "not a JSON object"),
        ("empty reply", "", "not a single JSON object"),
        (
            "missing field",
            json.dumps({"status": "answered", "claims": []}),
            "fields must be exactly",
        ),
        (
            "extra top-level field",
            json.dumps(
                {"status": "answered", "claims": [claim("x.", "E1")], "missing": "", "why": "x"}
            ),
            "fields must be exactly",
        ),
        ("invalid status", output(status="maybe"), "invalid status"),
        (
            "non-string status",
            json.dumps({"status": 1, "claims": [], "missing": ""}),
            "invalid status",
        ),
        ("answered without claims", output(claims=[]), "needs at least one claim"),
        ("claim without citations", output(claims=[claim("Saved in sessions.")]), "no citations"),
        (
            "unknown citation id",
            output(claims=[claim("Saved in sessions.", "E99")]),
            "unknown citation id 'E99'",
        ),
        (
            "duplicate citation",
            output(claims=[claim("Saved in sessions.", "E1", "E1")]),
            "duplicate citation ids",
        ),
        (
            "extra claim field",
            output(claims=[{"text": "Saved.", "citations": ["E1"], "confidence": 0.9}]),
            "fields must be exactly",
        ),
        ("claim missing text", output(claims=[{"citations": ["E1"]}]), "fields must be exactly"),
        ("empty claim text", output(claims=[claim("   ", "E1")]), "non-empty string"),
        (
            "citations not strings",
            output(claims=[{"text": "Saved.", "citations": [1]}]),
            "list of strings",
        ),
        (
            "citations not a list",
            output(claims=[{"text": "Saved.", "citations": "E1"}]),
            "list of strings",
        ),
        (
            "claims not a list",
            json.dumps({"status": "answered", "claims": "x", "missing": ""}),
            "claims must be a list",
        ),
        (
            "missing not a string",
            json.dumps({"status": "answered", "claims": [claim("A.", "E1")], "missing": 3}),
            "missing must be a string",
        ),
        (
            "partial without missing",
            output(status="partial", claims=[claim("Saved in sessions.", "E1")]),
            "partial needs claims and a non-empty missing",
        ),
        (
            "partial without claims",
            output(status="partial", claims=[], missing="x"),
            "partial needs claims",
        ),
        (
            "insufficient with claims",
            output(status="insufficient_evidence", claims=[claim("A.", "E1")], missing="nothing"),
            "needs no claims",
        ),
        (
            "insufficient without missing",
            output(status="insufficient_evidence", claims=[], missing=""),
            "needs no claims and a non-empty missing",
        ),
    ],
)
def test_malformed_output_is_rejected(name: str, raw: str, expected: str) -> None:
    valid, reasons = check(raw)
    assert not valid, name
    assert any(expected in r for r in reasons), (name, reasons)


# --- fabricated references ----------------------------------------------------------------------


def test_fabricated_path_is_rejected() -> None:
    valid, reasons = check(output(claims=[claim("It lives in src/pkg/other.py.", "E1")]))
    assert not valid and "src/pkg/other.py" in reasons[0]


def test_path_of_a_cited_item_is_accepted_by_full_path_suffix_or_filename() -> None:
    for mention in ("src/pkg/sessions.py", "pkg/sessions.py", "sessions.py"):
        assert check(output(claims=[claim(f"See {mention}.", "E1")])) == (True, [])


def test_path_of_an_uncited_context_item_is_accepted_but_counted() -> None:
    result = validate_output(output(claims=[claim("See app.py for the caller.", "E1")]), CONTEXT)
    assert result.valid and result.paths_only_in_uncited_items == 1


def test_line_outside_the_cited_range_is_rejected() -> None:
    valid, reasons = check(output(claims=[claim("It happens on line 99.", "E1")]))
    assert not valid and "line 99" in reasons[0]


def test_valid_evidence_id_with_a_wrong_line_range_is_rejected() -> None:
    # E2 exists, but its range is 100-110, not 12
    valid, reasons = check(output(claims=[claim("See sessions.py:12 and line 12.", "E2")]))
    assert not valid and any("line 12" in r for r in reasons)


def test_lines_inside_the_cited_range_are_accepted() -> None:
    assert check(output(claims=[claim("Lines 10-20 define it; see line 12.", "E1")])) == (True, [])
    assert check(output(claims=[claim("It is at sessions.py:15.", "E1")])) == (True, [])


def test_unknown_symbol_is_rejected() -> None:
    valid, reasons = check(output(claims=[claim("It then calls `frobnicate()`.", "E1")]))
    assert not valid and "frobnicate" in reasons[0]
    valid, reasons = check(output(claims=[claim("It then calls frobnicate(x).", "E1")]))
    assert not valid and "frobnicate" in reasons[0]


def test_known_symbols_and_exempt_builtins_are_accepted() -> None:
    text = "It calls `dump_cookie` and `get_cookie_name()`; the caller uses isinstance() checks."
    assert check(output(claims=[claim(text, "E1")])) == (True, [])
    assert check(output(claims=[claim("`SessionInterface.save_session` saves it.", "E1")])) == (
        True,
        [],
    )


def test_dotted_symbol_with_an_unknown_segment_is_rejected() -> None:
    valid, _ = check(output(claims=[claim("It uses `SessionInterface.fabricated_method`.", "E1")]))
    assert not valid


# --- valid outputs ------------------------------------------------------------------------------


def test_valid_answered_partial_and_insufficient_outputs() -> None:
    assert check(output()) == (True, [])
    assert check(
        output(status="answered", claims=[claim("A.", "E1"), claim("B.", "E1", "E2")])
    ) == (True, [])
    partial = output(status="partial", claims=[claim("A.", "E1")], missing="Callers are not shown.")
    assert check(partial) == (True, [])
    none = output(
        status="insufficient_evidence", claims=[], missing="The evidence has no GraphQL code."
    )
    assert check(none) == (True, [])


def test_the_result_carries_the_parsed_claims() -> None:
    result = validate_output(output(status="answered", claims=[claim("A.", "E2", "E1")]), CONTEXT)
    assert result.status == "answered"
    assert [(c.text, c.citations) for c in result.claims] == [("A.", ["E2", "E1"])]
