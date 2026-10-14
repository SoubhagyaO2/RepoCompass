"""A scripted demo against a running RepoCompass API.

    python -m app.demo [--api http://127.0.0.1:8000]

It asks a fixed set of questions (answerable, structural, and a false premise) and prints each
answer with its citations. Outputs are single runs of a nondeterministic model, not measurements.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any

QUESTIONS = [
    "Where is the session cookie saved?",
    "What calls url_for?",
    "How does Flask handle errors raised in a view?",
    "How does Flask send analytics events for each request?",
]


def format_answer(answer: dict[str, Any]) -> str:
    lines = [f"status: {answer['status']}"]
    for claim in answer["claims"]:
        lines.append(f"  - {claim['text']}")
        for c in claim["citations"]:
            symbol = f" ({c['symbol']})" if c["symbol"] else ""
            lines.append(
                f"      [{c['evidence_id']}] {c['file']}:{c['line_start']}-{c['line_end']}{symbol}"
            )
    if answer["missing"]:
        lines.append(f"  not covered by the evidence: {answer['missing']}")
    if not answer["validation"]["valid"]:
        lines.append("  validation failed: " + "; ".join(answer["validation"]["reasons"]))
    files = ", ".join(i["file"] for i in answer["evidence"]["evidence"][:5])
    lines.append(f"  evidence files: {files}")
    return "\n".join(lines)


def post(api: str, question: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{api}/v1/answer",
        data=json.dumps({"question": question}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.demo", description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)
    try:
        with urllib.request.urlopen(f"{args.api}/health", timeout=10) as response:
            health = json.load(response)
    except (urllib.error.URLError, TimeoutError) as error:
        print(f"error: cannot reach the API at {args.api} ({error})", file=sys.stderr)
        return 2
    if not (health["index_loaded"] and health["answer_enabled"]):
        print(f"error: the API needs an index and a model key: {health}", file=sys.stderr)
        return 2
    for question in QUESTIONS:
        print(f"\nQ: {question}")
        try:
            print(format_answer(post(args.api, question)))
        except urllib.error.HTTPError as error:
            print(f"  request failed: HTTP {error.code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
