"""One Qdrant client is shared by the API and the indexing worker thread, so every call must be
serialised (the local client is not documented as thread safe)."""

import threading
import time
from typing import Any

from app.retrieval.vector_store import _Locked


class Slow:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.guard = threading.Lock()

    def work(self, n: int) -> int:
        with self.guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.01)
        with self.guard:
            self.active -= 1
        return n

    value = 7  # a plain attribute passes through


def test_calls_through_the_wrapper_never_overlap() -> None:
    inner = Slow()
    locked: Any = _Locked(inner)
    results: list[int] = []
    threads = [
        threading.Thread(target=lambda i=i: results.append(locked.work(i))) for i in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == list(range(8)) and inner.max_active == 1


def test_attributes_and_results_pass_through() -> None:
    locked: Any = _Locked(Slow())
    assert locked.value == 7 and locked.work(3) == 3
