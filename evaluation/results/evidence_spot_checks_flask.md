# M12 hand-checked spot checks (plan section 10, check 9)

Ten queries fixed in `run_evidence.py` before any output was seen. For each, the top 3 items were
printed with the source text at the item's first line and at the first graph-edge citations
(`evidence_api_flask.json`, key `spot_checks`), and I compared them with the Flask source by hand.

**Result: 9 of 10 queries fully confirmed; 1 query contains one wrong graph edge.**

| Query | Verdict |
|---|---|
| save_session | confirmed (symbol, lines, cited call lines 20, 27, 161, 173 show the claimed calls) |
| Blueprint register | confirmed (decorator lines are part of the symbol, as the chunker defines) |
| dispatch_request | confirmed |
| url_for | confirmed |
| make_response | confirmed (called_by cited at tests/test_basic.py:1342 is the call) |
| teardown_appcontext | confirmed |
| render_template | confirmed |
| jsonify | confirmed |
| register_error_handler | confirmed |
| test_client | **one wrong edge**: `Flask.test_client -> testing.py::FlaskCliRunner` at app.py:812 |

**The miss, with its cause.** Line 812 is `return cls(...)` inside `Flask.test_client`, where
`cls` is bound by the function-local `from .testing import FlaskClient as cls` (line 811). The
method `test_cli_runner` further down has `from .testing import FlaskCliRunner as cls`. The M10
graph builder keys import bindings by alias per file, so the second binding replaced the first and
the call resolved to `FlaskCliRunner` (rule `import`). The evidence layer reported the graph's edge
faithfully (the cited line really contains `cls(...)`); the edge itself is wrong. This is a
limitation of the M10 call resolution (function-local imports that reuse an alias), found here by
hand checking; it was not caught by the M10 audit (30/30 for the `import` rule on its sample).
Recorded in `evaluation/BACKLOG.md`. It is exactly why graph facts carry their `rule` and why the
schema says they are structural annotations that may be imperfect.

## Follow-up (same day): the wrong edge was fixed

The miss led to the M10 follow-up (function-local imports are now scoped to their function; see
`plans/m10-code-graph.md`, addendum). After the fix `Flask.test_client -> FlaskClient` is the
edge at app.py:812 and `FlaskCliRunner` is no longer linked. The ten queries above were not
re-judged by hand after the fix; the automated validation was re-run (see the README).
