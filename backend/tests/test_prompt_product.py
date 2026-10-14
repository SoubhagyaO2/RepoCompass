"""The prompt the product (API and MCP server) uses: the frozen M13 prompt plus one rule about
what counts as evidence for questions about the code's language. The experiments keep the frozen
prompt; nothing measured is changed."""

import hashlib
from pathlib import Path

from app.answer.prompt import PROMPT_SHA256, SYSTEM_PROMPT
from app.answer.prompt_product import PRODUCT_PROMPT, PRODUCT_PROMPT_SHA256

ROOT = Path(__file__).resolve().parents[2]


def test_the_product_prompt_keeps_the_frozen_rules_and_response_shape_verbatim() -> None:
    head, tail = SYSTEM_PROMPT.split("\n7. Respond")
    assert PRODUCT_PROMPT.startswith(head)
    assert PRODUCT_PROMPT.endswith("\n7. Respond" + tail)


def test_the_one_added_rule_is_narrow() -> None:
    added = PRODUCT_PROMPT.replace(SYSTEM_PROMPT.split("\n7. Respond")[0], "").split(
        "\n7. Respond"
    )[0]
    assert ".py" in added and "Python" in added
    flat = " ".join(added.split())
    assert "only speak for the files shown" in flat  # no claim about the whole repository
    assert "framework" in flat and "names or imports" in flat  # frameworks need real evidence
    assert '"partial"' in flat  # a two-part question with one part settled is partial
    assert added.count("\n") <= 8  # one rule, not a rewrite


def test_the_product_prompt_is_a_different_text_with_its_own_recorded_hash() -> None:
    assert PRODUCT_PROMPT != SYSTEM_PROMPT and PRODUCT_PROMPT_SHA256 != PROMPT_SHA256
    assert PRODUCT_PROMPT_SHA256 == hashlib.sha256(PRODUCT_PROMPT.encode("utf-8")).hexdigest()


def test_the_frozen_prompt_file_is_untouched() -> None:
    source = (
        (ROOT / "backend" / "app" / "answer" / "prompt.py").read_bytes().replace(b"\r\n", b"\n")
    )
    assert hashlib.sha256(source).hexdigest().startswith("44c78edf")


def test_the_default_answer_service_still_uses_the_frozen_prompt() -> None:
    from answer_fixtures import FakeLLM, make_evidence, output

    from app.answer.service import AnswerService

    llm = FakeLLM(output())
    AnswerService(None, llm).answer_from_evidence("q", make_evidence())
    assert llm.requests[0].system == SYSTEM_PROMPT  # experiments and tests are unchanged


def test_connected_repositories_are_answered_with_the_product_prompt(tmp_path: Path) -> None:
    from answer_fixtures import FakeLLM, make_evidence, output

    from app.config import Settings
    from app.repositories import RepositoryRegistry

    llm = FakeLLM(output())
    registry = RepositoryRegistry(Settings(_env_file=None, data_dir=tmp_path), llm=llm)

    class Builder:
        index = None

        def build(self, query: str, top_k: int, include_text: bool = False):  # type: ignore[no-untyped-def]
            return make_evidence(query)

    service = registry._service_for(Builder())  # noqa: SLF001
    service.answer("q")
    assert llm.requests[0].system == PRODUCT_PROMPT
