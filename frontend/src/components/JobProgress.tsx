import { useEffect, useRef } from "react"

import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import { type Job, cancelJob, jobStatus } from "@/lib/api"

const POLL_MS = 400

/**
 * DESIGN.md: real progress, the current step by name, elapsed time past 10s,
 * and always cancellable. On the shop PC a silent three-minute wait reads as
 * a crash — so nothing here is a bare spinner.
 */
export function JobProgress({
  job,
  onUpdate,
  onDone,
}: {
  job: Job
  onUpdate: (job: Job) => void
  onDone?: (job: Job) => void
}) {
  const notified = useRef<string | null>(null)
  const active = job.status === "queued" || job.status === "running"

  useEffect(() => {
    if (!active) {
      if (job.status === "done" && notified.current !== job.id) {
        notified.current = job.id
        onDone?.(job)
      }
      return
    }
    const timer = setInterval(() => {
      jobStatus(job.id)
        .then(onUpdate)
        .catch(() => {
          /* transient; the next tick retries */
        })
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [job, active, onUpdate, onDone])

  if (job.status === "done") return null

  if (job.status === "failed" || job.status === "cancelled") {
    return (
      <div className="rounded-xl border bg-card p-4">
        <p className="text-sm font-medium">
          {job.status === "cancelled" ? "Cancelled" : "That job failed"}
        </p>
        {job.error && (
          <p className="mt-1 text-xs text-destructive">{job.error}</p>
        )}
      </div>
    )
  }

  const percent = Math.round(job.progress * 100)

  /*
   * Cancellation is cooperative: the model call it is inside cannot be
   * interrupted, so the job keeps running for up to a few seconds on a GPU and
   * minutes on the shop PC's i3. Saying "Cancelling…" is the difference between
   * a slow stop and a dead button (NEXT.md 2.2).
   */
  const cancelling = job.cancelling === true

  return (
    <div className="rounded-xl border bg-card p-4">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-sm font-medium">
          {cancelling ? "Cancelling…" : job.step}
        </p>
        <p className="text-xs tabular-nums text-muted-foreground">
          {/* A percentage that has stopped moving is worse than no percentage. */}
          {job.indeterminate ? "working" : `${percent}%`}
          {/* Elapsed appears past 10s, when the wait starts to feel wrong. */}
          {job.elapsed >= 10 && ` · ${formatElapsed(job.elapsed)}`}
        </p>
      </div>
      <Progress
        value={percent}
        className="mt-3"
        aria-valuenow={job.indeterminate ? undefined : percent}
        aria-valuetext={
          job.indeterminate
            ? `${job.step} — this step does not report progress`
            : `${percent}%`
        }
      />
      {cancelling && (
        <p className="mt-2 text-xs text-muted-foreground">
          Waiting for the current step to finish — it cannot be interrupted part
          way through.
        </p>
      )}
      <div className="mt-3 flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">{job.label}</p>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 px-2 text-xs"
          disabled={cancelling}
          onClick={() => void cancelJob(job.id).then(onUpdate)}
        >
          {cancelling ? "Cancelling…" : "Cancel"}
        </Button>
      </div>
    </div>
  )
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}m ${Math.round(seconds % 60)}s`
}
