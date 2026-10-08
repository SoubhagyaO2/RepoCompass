"""Smoke test of the real server: start uvicorn on a built live index and call the endpoints.

    python -m app.evidence.smoke --repository flask_evidence --root data/live_work/flask_evidence

Unit tests use FastAPI's in-process test client; this starts an actual server process and talks
HTTP to it. The index must already exist (built by `python -m app.evidence --build`, or left by
`python -m app.evaluation.run_evidence`) and no other process may hold it open.
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from app.evaluation.dataset import load_dataset
from app.evaluation.experiment import DEFAULT_DATASET, RESULTS_DIR
from app.evaluation.run_hybrid import FILES as HYBRID_RESULT_FILES
from app.evidence.schema import EvidenceResponse, IndexInfo

STARTUP_TIMEOUT_S = 180
QUESTIONS_CHECKED = 5


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _get(base: str, path: str, **params: Any) -> tuple[int, bytes]:
    url = f"{base}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def run(repository: str, root: Path) -> dict[str, Any]:
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "REPOCOMPASS_LIVE_REPOSITORY": repository,
        "REPOCOMPASS_LIVE_ROOT": str(root),
    }
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)]
        + ["--log-level", "warning"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
    )  # fmt: skip
    results: dict[str, Any] = {"port": port, "checks": {}}
    checks = results["checks"]
    try:
        started = time.time()
        while True:
            if server.poll() is not None:
                raise RuntimeError(
                    "the server exited during startup:\n"
                    + (server.stdout.read() if server.stdout else "")
                )
            try:
                if _get(base, "/health")[0] == 200:
                    break
            except OSError:
                pass
            if time.time() - started > STARTUP_TIMEOUT_S:
                raise RuntimeError("the server did not start in time")
            time.sleep(1)
        results["startup_seconds"] = round(time.time() - started, 1)

        status, body = _get(base, "/v1/index/status")
        info = IndexInfo.model_validate_json(body)
        checks["index_status"] = {"http": status, "files": info.files, "chunks": info.chunks}

        ds = load_dataset(DEFAULT_DATASET)
        saved = json.loads(
            (RESULTS_DIR / HYBRID_RESULT_FILES[("ast", "hybrid")]).read_text("utf-8")
        )
        saved_files = {q["id"]: q["retrieved_files"] for q in saved["per_question"]}
        order_ok, latencies = [], []
        for q in ds.questions[:QUESTIONS_CHECKED]:
            t = time.perf_counter()
            status, body = _get(base, "/v1/evidence", q=q.question, top_k=10)
            latencies.append((time.perf_counter() - t) * 1000)
            response = EvidenceResponse.model_validate_json(body)
            order_ok.append(
                status == 200
                and [i.file for i in response.evidence] == saved_files[q.id]
                and response.index.manifest_sha256 == info.manifest_sha256
            )
        checks["evidence_order_equals_saved_m8"] = {"questions": QUESTIONS_CHECKED, "ok": order_ok}
        checks["http_latency_ms"] = [round(x, 1) for x in latencies]

        status, body = _get(base, "/v1/evidence", q="session cookie", top_k=3, include_text="true")
        with_text = EvidenceResponse.model_validate_json(body)
        checks["include_text"] = {
            "http": status,
            "items": len(with_text.evidence),
            "all_have_text": all(i.text for i in with_text.evidence),
        }
        checks["bad_requests"] = {
            "empty_q": _get(base, "/v1/evidence", q="")[0],
            "blank_q": _get(base, "/v1/evidence", q="   ")[0],
            "missing_q": _get(base, "/v1/evidence")[0],
            "top_k_0": _get(base, "/v1/evidence", q="x", top_k=0)[0],
            "top_k_51": _get(base, "/v1/evidence", q="x", top_k=51)[0],
            "too_long": _get(base, "/v1/evidence", q="x" * 2001)[0],
        }
        checks["health"] = _get(base, "/health")[0]
    finally:
        server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()

    checks_ok = (
        checks["index_status"]["http"] == 200
        and all(checks["evidence_order_equals_saved_m8"]["ok"])
        and checks["include_text"]["http"] == 200
        and checks["include_text"]["all_have_text"]
        and all(code == 422 for code in checks["bad_requests"].values())
        and checks["health"] == 200
    )
    results["passes"] = bool(checks_ok)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.evidence.smoke", description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "evidence_api_smoke_flask.json")
    args = parser.parse_args(argv)
    results = run(args.repository, args.root)
    args.out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))
    return 0 if results["passes"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
