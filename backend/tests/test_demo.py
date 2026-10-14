from app.demo import format_answer


def answer(**over: object) -> dict:
    base = {
        "status": "answered",
        "claims": [
            {
                "text": "Saved on the response.",
                "citations": [
                    {
                        "evidence_id": "E1",
                        "file": "a.py",
                        "line_start": 3,
                        "line_end": 9,
                        "symbol": "A.save",
                    }
                ],
            }
        ],
        "missing": "",
        "validation": {"valid": True, "reasons": []},
        "evidence": {"evidence": [{"file": "a.py"}, {"file": "b.py"}]},
    }
    return {**base, **over}


def test_answer_is_printed_with_citations_and_evidence() -> None:
    text = format_answer(answer())
    assert "status: answered" in text and "[E1] a.py:3-9 (A.save)" in text
    assert "evidence files: a.py, b.py" in text


def test_abstention_and_invalid_output_are_visible() -> None:
    text = format_answer(
        answer(status="insufficient_evidence", claims=[], missing="Nothing about analytics.")
    )
    assert "not covered by the evidence: Nothing about analytics." in text
    bad = format_answer(answer(validation={"valid": False, "reasons": ["claim 1: no citations"]}))
    assert "validation failed: claim 1: no citations" in bad
