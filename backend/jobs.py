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

# How long a finished job's files stay on disk.
#
# SECURITY.md §5 claimed temp files were "cleaned up after each job". They were
# not: the working directory was wiped at startup and clean shutdown only, so a
# session's client photos and spreadsheets sat there until the next launch — and
# a power cut left them indefinitely (NEXT.md 2.4).
#
# Long enough that the operator can come back to a result after working on
# something else, which is exactly what "This session's results" is for; short
# enough that client artwork is not lying around all afternoon.
RESULT_TTL_SECONDS = 30 * 60


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
    # True while the running step genuinely cannot report progress — a single
    # uninterruptible model call. Set by `Reporter.opaque_step`. DESIGN.md asks
    # for real progress; where that is impossible the honest answer is to say so
    # rather than leave a bar sitting at 35% for a minute (NEXT.md 2.3).
    indeterminate: bool = False
    # True once this job's working directory has been removed. The job stays in
    # the history so the operator sees what happened; only the files go.
    files_deleted: bool = False
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
            # Cancellation is cooperative, so a cancelled job keeps running
            # until the current block of work ends — measured at 11 s on a GPU
            # and minutes on the shop PC's i3. Without this the UI had no way to
            # say anything but "Removing background…", which reads as a dead
            # button (NEXT.md 2.2).
            "cancelling": self.cancelling and self.status in {"queued", "running"},
            # The result is gone from disk. Said plainly, because the alternative
            # is a Download button that 404s.
            "files_deleted": self.files_deleted,
            # Whether the *current* step can report progress at all. A frozen
            # bar is only alarming when nothing says it is expected (2.3).
            "indeterminate": self.indeterminate,
            "created_at": round(self.created_at, 3),
            "finished_at": round(self.finished_at, 3) if self.finished_at else None,
        }


class Reporter:
    """Handed to a worker so it can report progress and honour cancellation."""

    def __init__(self, job: Job) -> None:
        self._job = job

    def step(self, text: str, progress: float | None = None) -> None:
        """Name what is happening now. The operator reads this."""
        self._job.step = text
        self._job.indeterminate = False
        if progress is not None:
            self._job.progress = max(0.0, min(1.0, progress))
        self.check_cancelled()

    def opaque_step(self, text: str, progress: float | None = None) -> None:
        """Name a step that cannot report progress, and say that it cannot.

        For a single model call the app cannot see inside. Measured on a GPU,
        background removal sat at 35% for 54 of its 61 seconds with the step text
        unchanged; on the shop PC's i3 that stretch is minutes. The bar is marked
        indeterminate so the UI can show motion and a plain warning instead of a
        number that has stopped meaning anything.
        """
        self._job.step = text
        if progress is not None:
            self._job.progress = max(0.0, min(1.0, progress))
        self._job.indeterminate = True
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
                job.indeterminate = False
            except Cancelled:
                job.status, job.step = "cancelled", "Cancelled"
                job.indeterminate = False
            except Exception as exc:  # noqa: BLE001 - surfaced to the operator
                log.exception("job %s (%s) failed", job.id, kind)
                job.status, job.step = "failed", "Failed"
                job.indeterminate = False
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

    def expired(self, ttl: float = RESULT_TTL_SECONDS) -> list[Job]:
        """Finished jobs whose files are past their time to live."""
        cutoff = time.time() - ttl
        with self._lock:
            return [
                job
                for job in self._jobs.values()
                if job.finished_at is not None
                and job.finished_at < cutoff
                and not job.files_deleted
            ]

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
