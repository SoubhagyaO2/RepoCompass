"""The deterministic output validator (plan section 4a). It proves citation *validity* only.

Python builtins and keywords are not repository symbols and are exempt from the symbol check
(deviation 1 of the plan, recorded before the live run).
"""

import builtins
import json
import keyword
import re
from dataclasses import dataclass, field

from app.answer.context import Context

STATUSES = ("answered", "partial", "insufficient_evidence")
TOP_LEVEL_FIELDS = {"status", "claims", "missing"}
CLAIM_FIELDS = {"text", "citations"}

PATH_RE = re.compile(r"(?<![\w./-])(?:[\w.-]+/)*[\w.-]+\.py\b")
LINE_RE = re.compile(r"\blines?\s+(\d+)(?:\s*(?:-|–|to)\s*(\d+))?", re.IGNORECASE)
PYLINE_RE = re.compile(r"\.py:(\d+)(?:-(\d+))?")
BACKTICK_RE = re.compile(r"`([^`\n]+)`")
CALL_RE = re.compile(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\(")
IDENT_RE = re.compile(r"[A-Za-z_]\w*")
DOTTED_RE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")

EXEMPT_NAMES = frozenset(dir(builtins)) | frozenset(keyword.kwlist) | {"self", "cls"}


@dataclass
class ParsedClaim:
    text: str
    citations: list[str]


@dataclass
class ValidationResult:
    valid: bool
    reasons: list[str]
    status: str | None = None
    claims: list[ParsedClaim] = field(default_factory=list)
    missing: str = ""
    paths_only_in_uncited_items: int = 0


def known_identifiers(context: Context) -> set[str]:
    """Every identifier visible in the context: chunk text, symbols, relationships, file paths."""
    names: set[str] = set()
    for c in context.items:
        item = c.item
        names.update(IDENT_RE.findall(item.text or ""))
        names.update(IDENT_RE.findall(item.file))
        if item.symbol:
            names.update(IDENT_RE.findall(item.symbol.qualified_name))
        for r in item.graph.relationships if item.graph else []:
            names.update(IDENT_RE.findall(r.other))
    return names


def _line_numbers(match: re.Match[str]) -> list[int]:
    return [int(g) for g in match.groups() if g is not None]


def _check_claim_references(
    number: int, claim: ParsedClaim, context: Context, known: set[str]
) -> tuple[list[str], int]:
    reasons: list[str] = []
    by_id = context.by_id()
    cited = [by_id[i].item for i in claim.citations if i in by_id]
    cited_files = {i.file for i in cited}
    all_files = {c.item.file for c in context.items}
    only_uncited = 0

    def matches(path: str, files: set[str]) -> bool:
        return any(f == path or f.endswith("/" + path) for f in files)

    for path in PATH_RE.findall(claim.text):
        if matches(path, cited_files):
            continue
        if matches(path, all_files):
            only_uncited += 1
        else:
            reasons.append(f"claim {number}: path {path!r} is not in the evidence")

    refs = [_line_numbers(m) for m in LINE_RE.finditer(claim.text)]
    refs += [_line_numbers(m) for m in PYLINE_RE.finditer(claim.text)]
    for numbers in refs:
        for n in numbers:
            if not any(i.line_start <= n <= i.line_end for i in cited):
                reasons.append(f"claim {number}: line {n} is outside the cited ranges")

    tokens: set[str] = set()
    for raw in BACKTICK_RE.findall(claim.text):
        token = raw.strip().removesuffix("()")
        if DOTTED_RE.fullmatch(token):
            tokens.add(token)
    tokens.update(CALL_RE.findall(claim.text))
    for token in sorted(tokens):
        unknown = [seg for seg in token.split(".") if seg not in known and seg not in EXEMPT_NAMES]
        if unknown:
            reasons.append(f"claim {number}: symbol {token!r} is not in the evidence")
    return reasons, only_uncited


def validate_output(raw: str, context: Context) -> ValidationResult:
    def fail(*reasons: str) -> ValidationResult:
        return ValidationResult(valid=False, reasons=list(reasons))

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return fail("the output is not a single JSON object")
    if not isinstance(data, dict):
        return fail("the output is not a JSON object")
    if set(data) != TOP_LEVEL_FIELDS:
        return fail(f"fields must be exactly {sorted(TOP_LEVEL_FIELDS)}, got {sorted(data)}")

    status, claims_raw, missing = data["status"], data["claims"], data["missing"]
    if status not in STATUSES:
        return fail(f"invalid status {status!r}")
    if not isinstance(missing, str):
        return fail("missing must be a string")
    if not isinstance(claims_raw, list):
        return fail("claims must be a list")

    known_ids = set(context.by_id())
    reasons: list[str] = []
    claims: list[ParsedClaim] = []
    for n, entry in enumerate(claims_raw, 1):
        if not isinstance(entry, dict) or set(entry) != CLAIM_FIELDS:
            reasons.append(f"claim {n}: fields must be exactly {sorted(CLAIM_FIELDS)}")
            continue
        text, citations = entry["text"], entry["citations"]
        if not isinstance(text, str) or not text.strip():
            reasons.append(f"claim {n}: text must be a non-empty string")
            continue
        if not isinstance(citations, list) or not all(isinstance(c, str) for c in citations):
            reasons.append(f"claim {n}: citations must be a list of strings")
            continue
        if not citations:
            reasons.append(f"claim {n}: no citations")
        if len(set(citations)) != len(citations):
            reasons.append(f"claim {n}: duplicate citation ids")
        for c in citations:
            if c not in known_ids:
                reasons.append(f"claim {n}: unknown citation id {c!r}")
        claims.append(ParsedClaim(text=text, citations=citations))

    if status == "answered" and not claims_raw:
        reasons.append("status answered needs at least one claim")
    if status == "partial" and (not claims_raw or not missing.strip()):
        reasons.append("status partial needs claims and a non-empty missing")
    if status == "insufficient_evidence" and (claims_raw or not missing.strip()):
        reasons.append("status insufficient_evidence needs no claims and a non-empty missing")

    only_uncited = 0
    if not reasons:
        known = known_identifiers(context)
        for n, claim in enumerate(claims, 1):
            claim_reasons, uncited = _check_claim_references(n, claim, context, known)
            reasons += claim_reasons
            only_uncited += uncited

    return ValidationResult(
        valid=not reasons,
        reasons=reasons,
        status=status,
        claims=claims,
        missing=missing,
        paths_only_in_uncited_items=only_uncited,
    )
