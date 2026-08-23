"""Background jobs with real progress.

DESIGN.md is specific about this because of the shop PC: a 90-second upscale on
an i3 must show real progress and a named step, not a spinner, and must be
cancellable. A silent 90-second wait reads as a crash.

**One worker, deliberately.** `max_workers=1` serialises model work, which is
the memory rule from ARCHITECTURE.md expressed as a queue rather than as a
convention someone has to remember.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal

log = logging.getLogger(__name__)

Status = Literal["queued", "running", "done", "failed", "cancelled"]

# Enough history for the operator to look back over a batch, not a memory leak.
MAX_JOBS = 200


class Cancelled(Exception):
    """Raised inside a worker when the operator cancels."""


@dataclass
class Job:
    id: str
    kind: str
    label: str
    status: Status = "queued"
    step: str = "Queued"
    progress: float = 0.0
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        return (self.finished_at or time.time()) - self.started_at

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelling(self) -> bool:
        return self._cancel.is_set()

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "status": self.status,
            "step": self.step,
            "progress": round(self.progress, 4),
            "elapsed": round(self.elapsed, 1),
            "result": self.result,
            "error": self.error,
        }


class Reporter:
    """Handed to a worker so it can report progress and honour cancellation."""

    def __init__(self, job: Job) -> None:
        self._job = job

    def step(self, text: str, progress: float | None = None) -> None:
        """Name what is happening now. The operator reads this."""
        self._job.step = text
        if progress is not None:
            self._job.progress = max(0.0, min(1.0, progress))
        self.check_cancelled()

    def progress(self, fraction: float) -> None:
        self._job.progress = max(0.0, min(1.0, fraction))
        self.check_cancelled()

    def check_cancelled(self) -> None:
        """Call between units of work. Cancellation is cooperative."""
        if self._job.cancelling:
            raise Cancelled


class JobRegistry:
    def __init__(self) -> None:
        # One worker: model jobs never overlap. See the module docstring.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="job")
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()

    def submit(
        self,
        kind: str,
        label: str,
        work: Callable[[Reporter], dict[str, Any]],
    ) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, label=label)
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > MAX_JOBS:
                self._jobs.popitem(last=False)

        def run() -> None:
            if job.cancelling:
                job.status, job.step = "cancelled", "Cancelled before starting"
                job.finished_at = time.time()
                return
            job.status, job.step = "running", "Starting"
            job.started_at = time.time()
            try:
                job.result = work(Reporter(job))
                job.status, job.step, job.progress = "done", "Finished", 1.0
            except Cancelled:
                job.status, job.step = "cancelled", "Cancelled"
            except Exception as exc:  # noqa: BLE001 - surfaced to the operator
                log.exception("job %s (%s) failed", job.id, kind)
                job.status, job.step = "failed", "Failed"
                job.error = f"{type(exc).__name__}: {exc}"
            finally:
                job.finished_at = time.time()

        self._pool.submit(run)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 25) -> list[Job]:
        with self._lock:
            return list(reversed(list(self._jobs.values())))[:limit]

    def active_count(self) -> int:
        with self._lock:
            return sum(
                1 for j in self._jobs.values() if j.status in {"queued", "running"}
            )

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


registry = JobRegistry()


def submit(
    kind: str, label: str, work: Callable[[Reporter], dict[str, Any]]
) -> Job:
    return registry.submit(kind, label, work)


def run_sync(kind: str, label: str, work: Callable[[Reporter], dict[str, Any]]) -> Job:
    """Submit and wait. For tests, and for jobs known to be fast."""
    job = registry.submit(kind, label, work)
    while job.status in {"queued", "running"}:
        time.sleep(0.01)
    return job


__all__ = ["Cancelled", "Future", "Job", "Reporter", "registry", "run_sync", "submit"]
