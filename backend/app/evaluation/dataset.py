"""The evaluation dataset: questions with manually verified ground truth."""

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

QuestionCategory = Literal["definition", "concept", "caller", "dependency", "flow"]
Difficulty = Literal["easy", "medium", "hard"]

# Categories whose question names the exact identifier/module being asked about. The others
# ("concept", "flow") must be found from a description of behaviour.
NAMED_CATEGORIES: frozenset[str] = frozenset({"definition", "caller", "dependency"})


class RelevantSymbol(BaseModel):
    file: str  # repo-relative path; must be one of the question's primary/supporting files
    name: str  # qualified name as the parser reports it, e.g. "Flask.wsgi_app"


class EvalQuestion(BaseModel):
    id: str
    question: str
    category: QuestionCategory
    difficulty: Difficulty
    # Where the behaviour the question asks about is implemented: the one-line answer.
    primary: list[str] = Field(min_length=1)
    # Other files a complete answer must also cite (triggers, hand-offs, base implementations).
    # A file a correct answer could omit is not listed at all.
    supporting: list[str] = []
    relevant_symbols: list[RelevantSymbol] = []
    evidence: str  # how the answer was verified (lines read at the pinned commit)

    @property
    def relevant_files(self) -> list[str]:
        """Every file that counts as required evidence (primary first)."""
        return [*self.primary, *self.supporting]


class EvalDataset(BaseModel):
    name: str
    repository: str  # "pallets/flask"
    commit: str  # full SHA the ground truth was verified against
    description: str
    methodology: dict[str, str]
    frozen: bool = False
    frozen_on: str | None = None
    questions_sha256: str | None = None  # fingerprint of `questions`, checked once frozen
    questions: list[EvalQuestion]


def expected_difficulty(category: str, n_files: int) -> Difficulty:
    """The difficulty rubric, a pure function of the ground truth's shape.

    - Named questions (definition / caller / dependency) give the identifier, so difficulty is
      how many files the answer aggregates: 1 easy, 2-3 medium, 4+ hard.
    - Described questions (concept / flow) must be found from behaviour: one file is medium,
      two or more is hard.

    This is *structural* difficulty. Whether it predicts retrieval difficulty is something the
    benchmark reports; it is never adjusted afterwards.
    """
    if category in NAMED_CATEGORIES:
        return "easy" if n_files == 1 else "medium" if n_files <= 3 else "hard"
    return "medium" if n_files == 1 else "hard"


def compute_questions_sha256(questions: list[EvalQuestion]) -> str:
    canonical = json.dumps(
        [q.model_dump() for q in questions],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_dataset(path: Path) -> EvalDataset:
    # utf-8-sig: tolerate the byte-order mark some Windows editors add when saving.
    return EvalDataset.model_validate_json(path.read_text(encoding="utf-8-sig"))
