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

  return (
    <div className="rounded-xl border bg-card p-4">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-sm font-medium">{job.step}</p>
        <p className="text-xs tabular-nums text-muted-foreground">
          {percent}%
          {/* Elapsed appears past 10s, when the wait starts to feel wrong. */}
          {job.elapsed >= 10 && ` · ${formatElapsed(job.elapsed)}`}
        </p>
      </div>
      <Progress value={percent} className="mt-3" />
      <div className="mt-3 flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">{job.label}</p>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 px-2 text-xs"
          onClick={() => void cancelJob(job.id).then(onUpdate)}
        >
          Cancel
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
