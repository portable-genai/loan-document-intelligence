"""Concurrency regression: the local SQLite audit adapter is safe across worker threads.

Under ``local serve`` the FastAPI container is process-wide (``deps.get_container`` is
``lru_cache``d, so one adapter instance with one ``sqlite3`` connection) while the sync
endpoints run in Starlette's anyio worker threadpool. A request handled on a worker
thread therefore calls ``record()`` / ``read_all()`` on a connection opened by a
*different* thread. Without ``check_same_thread=False`` plus a lock, sqlite3 raises
"SQLite objects created in a thread can only be used in that same thread", which would
silently drop the WORM audit event and 500 the request under concurrency.

This test constructs the adapter **once** with ``:memory:`` Settings, then drives many
interleaved writes and reads from a ``ThreadPoolExecutor`` (8 threads), asserting no
exception is raised and the final row count is exact. It passes with the current
``check_same_thread=False`` + lock fix in ``LocalAppendOnlyAuditAdapter``; it would fail
(cross-thread ``sqlite3.ProgrammingError``) if either were removed.
"""

from __future__ import annotations

import concurrent.futures

from loan_doc_intel.adapters.local.audit import LocalAppendOnlyAuditAdapter
from loan_doc_intel.config import LocalSettings, Settings
from loan_doc_intel.domain.models import AuditEvent, Decision

_THREADS = 8
_PER_THREAD = 25


def _settings() -> Settings:
    # A single shared in-memory connection (in-memory DBs are per-connection, so the one
    # connection opened in __init__ is the one all worker threads must reach).
    return Settings(profile="local", local=LocalSettings(audit_path=":memory:"))


def _drive(fn) -> list:
    """Run ``fn(i)`` for i in range(_THREADS * _PER_THREAD) across a thread pool, raising
    the first exception any worker hit (so a cross-thread sqlite3 error fails the test)."""
    n = _THREADS * _PER_THREAD
    with concurrent.futures.ThreadPoolExecutor(max_workers=_THREADS) as pool:
        futures = [pool.submit(fn, i) for i in range(n)]
        return [f.result() for f in concurrent.futures.as_completed(futures)]


def test_audit_adapter_is_thread_safe() -> None:
    adapter = LocalAppendOnlyAuditAdapter(_settings())

    def work(i: int) -> int:
        adapter.record(
            AuditEvent(
                action="process",
                actor=f"underwriter-{i}",
                decision=Decision.ALLOWED,
                redacted_prompt="extract income from payslip",
                redacted_response="ok",
            )
        )
        # Interleave a read on the same connection from the worker thread too.
        return len(adapter.read_all())

    results = _drive(work)

    # Every interleaved read ran without a cross-thread error.
    assert all(r >= 0 for r in results)
    # The append-only store holds exactly one row per recorded event, no drops.
    assert len(adapter.read_all()) == _THREADS * _PER_THREAD
