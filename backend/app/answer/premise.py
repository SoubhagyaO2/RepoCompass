"""Contract v3 of Milestone 15 (plan section 1): the model first states the premise of the question
and how the evidence relates to it, then answers. This validator wraps the unchanged baseline
validator; it checks that the output is *consistent*, not that the assessment is *true*."""

import json
from dataclasses import dataclass

from app.answer.context import Context
from app.answer.validate import ValidationResult, validate_output

V3_FIELDS = {"premise", "premise_supported", "evidence_relation", "status", "claims", "missing"}
RELATIONS = ("direct", "related_only", "none")


@dataclass(frozen=True)
class Assessment:
    premise: str
    premise_supported: bool
    evidence_relation: str


def validate_output_v3(raw: str, context: Context) -> tuple[ValidationResult, Assessment | None]:
    def fail(*reasons: str) -> tuple[ValidationResult, None]:
        return ValidationResult(valid=False, reasons=list(reasons)), None

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return fail("the output is not a single JSON object")
    if not isinstance(data, dict):
        return fail("the output is not a JSON object")
    if set(data) != V3_FIELDS:
        return fail(f"fields must be exactly {sorted(V3_FIELDS)}, got {sorted(data)}")

    premise, supported, relation = (
        data["premise"],
        data["premise_supported"],
        data["evidence_relation"],
    )
    if not isinstance(premise, str) or not premise.strip():
        return fail("premise must be a non-empty string")
    if not isinstance(supported, bool):
        return fail("premise_supported must be a boolean")
    if not isinstance(relation, str) or relation not in RELATIONS:
        return fail(f"evidence_relation must be one of {list(RELATIONS)}")

    base = {key: data[key] for key in ("status", "claims", "missing")}
    result = validate_output(json.dumps(base), context)
    reasons = list(result.reasons)
    if result.status in ("answered", "partial"):
        if not supported:
            reasons.append(f"status {result.status} needs premise_supported true")
        if relation != "direct":
            reasons.append(f"status {result.status} needs evidence_relation 'direct'")
    result.reasons, result.valid = reasons, not reasons
    return result, (Assessment(premise, supported, relation) if result.valid else None)
