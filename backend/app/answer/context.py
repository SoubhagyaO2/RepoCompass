"""The user message: the question and the numbered evidence items, as a deterministic template.

Repository text appears only inside the BEGIN/END TEXT blocks. Raw retrieval scores are not shown
(they are not comparable and invite over-trust). Items are added in rank order while the evidence
text fits the budget; an item that does not fit is omitted whole, with every later item, and the
omission is stated to the model and recorded.
"""

from dataclasses import dataclass

from app.evidence.schema import EvidenceItem, EvidenceResponse

CONTEXT_BUDGET_CHARS = 24_000


@dataclass(frozen=True)
class ContextItem:
    id: str  # "E<rank>"
    item: EvidenceItem


@dataclass(frozen=True)
class Context:
    question: str
    items: tuple[ContextItem, ...]  # the items shown to the model, in rank order
    omitted: int  # items left out because of the budget
    text: str  # the exact user message

    def by_id(self) -> dict[str, ContextItem]:
        return {c.id: c for c in self.items}


class ContextError(ValueError):
    """The evidence cannot be turned into a context (for example it has no chunk text)."""


def _block(evidence_id: str, item: EvidenceItem) -> str:
    symbol = item.symbol.qualified_name if item.symbol else "none (module-level code)"
    lines = [
        f"[{evidence_id}] file: {item.file}",
        f"lines: {item.line_start}-{item.line_end}",
        f"symbol: {symbol}",
        f"category: {item.category}",
    ]
    relationships = item.graph.relationships if item.graph else []
    if relationships:
        lines.append("structure (how this code is connected; NOT evidence of relevance):")
        lines += [f"  - {r.relationship} {r.other} (rule: {r.rule})" for r in relationships]
    lines.append(f"----- BEGIN TEXT {evidence_id} -----")
    lines.append(item.text or "")
    lines.append(f"----- END TEXT {evidence_id} -----")
    return "\n".join(lines)


def build_context(
    question: str, evidence: EvidenceResponse, budget_chars: int = CONTEXT_BUDGET_CHARS
) -> Context:
    shown: list[ContextItem] = []
    used = 0
    for item in evidence.evidence:
        if item.text is None:
            raise ContextError(f"{item.file}:{item.line_start} has no text; request include_text")
        if used + len(item.text) > budget_chars:
            break
        used += len(item.text)
        shown.append(ContextItem(f"E{item.rank}", item))
    omitted = len(evidence.evidence) - len(shown)

    header = [
        f"Question: {question}",
        "",
        f"Evidence items shown: {len(shown)}. Items omitted for space: {omitted}.",
        "",
    ]
    blocks = [_block(c.id, c.item) for c in shown]
    text = "\n".join(header) + "\n" + "\n\n".join(blocks) + ("\n" if blocks else "")
    return Context(question=question, items=tuple(shown), omitted=omitted, text=text)
