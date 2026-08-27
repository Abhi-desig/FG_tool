import { useCallback, useEffect, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  type Job,
  jobPreviewUrl,
  jobResultUrl,
  recentJobs,
} from "@/lib/api"

const POLL_MS = 3000

/**
 * Everything this session produced, and a way back to it.
 *
 * **Why this exists.** An 80-second cutout was destroyed by clicking another
 * screen: the dropzone came back empty and a toast still read "Background
 * removed · Ready to download" pointing at nothing. The work was never lost —
 * all nine jobs from that session were still in `/api/jobs`, every output file
 * was still on disk, and the "lost" result still returned HTTP 200 with 32,293
 * bytes. The only thing missing was a UI that asked (NEXT.md 0.4).
 *
 * Deliberately a *history*, not a cache: it reads the server's own list, so it
 * cannot disagree with what is actually downloadable.
 */
export function RecentJobs({ refreshKey }: { refreshKey?: unknown }) {
  const [jobs, setJobs] = useState<Job[]>([])
  const [open, setOpen] = useState(false)

  const load = useCallback(() => {
    recentJobs(25)
      .then(setJobs)
      .catch(() => {
        /* transient; the next tick retries */
      })
  }, [])

  useEffect(() => {
    load()
  }, [load, refreshKey])

  // Keep polling while anything is still running, so a job finished on another
  // screen appears here without a reload.
  useEffect(() => {
    const timer = setInterval(load, POLL_MS)
    return () => clearInterval(timer)
  }, [load])

  const finished = jobs.filter((j) => j.status === "done" && j.result)
  if (jobs.length === 0) return null

  return (
    <section className="rounded-xl border bg-card">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
      >
        <span className="text-sm font-medium">
          This session&rsquo;s results
          {finished.length > 0 && (
            <span className="ml-2 font-normal text-muted-foreground">
              {finished.length} ready to download
            </span>
          )}
        </span>
        <span aria-hidden className="text-xs text-muted-foreground">
          {open ? "Hide" : "Show"}
        </span>
      </button>

      {open && (
        <ul className="border-t">
          {jobs.map((job) => (
            <li
              key={job.id}
              className="flex flex-wrap items-center gap-3 border-b px-4 py-2.5 last:border-b-0"
            >
              {job.status === "done" && job.result && !job.files_deleted ? (
                <img
                  src={jobPreviewUrl(job.id)}
                  alt=""
                  // The transparent-background case is why this sits on a
                  // checkerboard rather than flat grey — see ImageTools.
                  className="checkerboard h-10 w-10 shrink-0 rounded border object-contain"
                />
              ) : (
                <div className="h-10 w-10 shrink-0 rounded border bg-muted" />
              )}

              <div className="min-w-[140px] flex-1">
                <p className="truncate text-sm">{job.label}</p>
                <p className="text-xs text-muted-foreground">
                  {describe(job)}
                </p>
              </div>

              <Badge
                variant={
                  job.status === "done"
                    ? "secondary"
                    : job.status === "failed"
                      ? "destructive"
                      : "outline"
                }
                className="text-[11px]"
              >
                {job.status}
              </Badge>

              {/*
                A swept result must not offer a Download button that 404s —
                temp files are deleted on a timer so client artwork does not sit
                on the shop PC. See SECURITY.md §5.
              */}
              {job.status === "done" && job.result && !job.files_deleted && (
                <Button asChild variant="outline" size="sm" className="h-7 text-xs">
                  <a href={jobResultUrl(job.id)} download>
                    Download
                  </a>
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function describe(job: Job): string {
  const result = job.result
  if (job.files_deleted) return "Expired — the file was deleted from this machine"
  if (job.status === "done" && result) {
    const mb = result.bytes / 1_048_576
    // 0.03 MB, not 0.0 MB — a 32 KB file must not read as an empty one.
    const size = mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(result.bytes / 1024))} KB`
    return `${result.width}×${result.height} · ${size}`
  }
  if (job.status === "failed") return job.error ?? "Failed"
  if (job.status === "cancelled") return "Cancelled"
  return job.step
}
