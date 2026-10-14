"""The v1 answer contract as a provider response schema (Gemini `responseSchema`).

Used only when structured output is enabled (the API and MCP server). The evaluation runners do
not use it: every published result was measured with prompt-only JSON, and the format failures in
those results (plain-string claims, extra fields) are exactly what this removes.
"""

from typing import Any

ANSWER_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "status": {"type": "STRING", "enum": ["answered", "partial", "insufficient_evidence"]},
        "claims": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "text": {"type": "STRING"},
                    "citations": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
                "required": ["text", "citations"],
            },
        },
        "missing": {"type": "STRING"},
    },
    "required": ["status", "claims", "missing"],
}
