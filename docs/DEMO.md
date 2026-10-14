# Demo

A real run of `python -m app.demo` against the Flask index (`flask_evidence`, Flask commit
`d73fa1c`), model `gemini-3.5-flash-lite`, structured output on, captured on 2026-10-14. Every
answer below is **one run of a nondeterministic model**, not a measurement; the measured results
are in [`PROJECT_REPORT.md`](PROJECT_REPORT.md).

## Reproduce it

```bash
# one-time: ingest and index (see the README), then in backend/
uvicorn app.main:app                    # terminal 1
python -m app.demo                      # terminal 2
# or open the UI:  cd frontend && npm run build && npm run start   ->  http://127.0.0.1:3000
```

## The run

```text
﻿
Q: Where is the session cookie saved?
status: answered
  - The session cookie is saved onto the response using response.set_cookie() or deleted using response.delete_cookie() within SecureCookieSessionInterface.save_session().
      [E1] src/flask/sessions.py:337-385 (SecureCookieSessionInterface.save_session)
  evidence files: src/flask/sessions.py, tests/test_basic.py, tests/test_reqctx.py, src/flask/testing.py, tests/test_testing.py

Q: What calls url_for?
status: answered
  - url_for calls current_app.url_for().
      [E1] src/flask/helpers.py:200-251 (url_for)
  evidence files: src/flask/helpers.py, tests/test_helpers.py, src/flask/app.py, tests/test_basic.py, src/flask/sansio/app.py

Q: How does Flask handle errors raised in a view?
status: answered
  - When a user exception occurs, Flask calls ``handle_user_exception``, which forwards Werkzeug ``HTTPException`` instances to ``handle_http_exception`` unless trapped via ``trap_http_exception``.
      [E1] src/flask/sansio/app.py:893-926 (App.trap_http_exception)
      [E3] src/flask/app.py:868-898 (Flask.handle_user_exception)
  - If an exception is not an HTTP exception or is trapped, Flask attempts to find a registered error handler using the exception and request blueprints.
      [E3] src/flask/app.py:868-898 (Flask.handle_user_exception)
  - If no error handler is found, the exception is re-raised.
      [E3] src/flask/app.py:868-898 (Flask.handle_user_exception)
  evidence files: src/flask/sansio/app.py, src/flask/helpers.py, src/flask/app.py, tests/test_views.py, tests/test_basic.py

Q: How does Flask send analytics events for each request?
status: insufficient_evidence
  not covered by the evidence: The provided evidence items do not contain any information about how Flask sends analytics events for each request.
  evidence files: src/flask/app.py, tests/test_basic.py, tests/test_blueprints.py, tests/test_helpers.py, src/flask/wrappers.py
```

## What to notice

- **Every claim carries a resolved citation** (evidence id, file, line range, symbol). The
  validator rejected nothing in this run; it only checks that cited ids, paths, lines and symbols
  exist in the retrieved evidence.
- **The false-premise question abstains.** Flask has no analytics feature, and the answer says the
  evidence does not cover it instead of describing nearby code. This is the behaviour M15 tried
  to improve; the baseline already did this on 20 of 24 test questions, so one example proves
  little.
- **The second answer is weak, and the validator cannot catch that.** The question asks what
  *calls* `url_for`; the answer says what `url_for` itself calls. The citation is valid and the
  sentence is supported by the cited code, but it answers a different question. This is the gap
  between citation validity and correctness described in the report.
- **Three of four questions are answered from one or two cited chunks**, while the evidence list
  also contains test files that rank highly. That is the known "tests crowd the top results"
  confound in `evaluation/BACKLOG.md`.
