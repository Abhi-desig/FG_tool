import { Badge } from "@/components/ui/badge"

/**
 * The exit-gate requirement, made into a component so it cannot be forgotten:
 * *"the UI makes it unmistakable when a job sends client material off-machine."*
 *
 * Every AI action shows this. Features 1–4 never do, and that contrast is the
 * point — the operator should be able to tell at a glance whether a client's
 * photograph is about to leave the building.
 */
export function OffMachineNotice({
  what,
  costRupees,
  batch,
}: {
  what: string
  costRupees: number | null
  batch?: boolean
}) {
  return (
    <div className="rounded-lg border border-[color:var(--warn)]/45 bg-[color:var(--warn)]/10 px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          variant="outline"
          className="border-[color:var(--warn)]/60 text-[color:var(--warn)]"
        >
          Leaves this computer
        </Badge>
        {costRupees != null && (
          <span className="text-sm font-medium">
            ₹{costRupees.toFixed(2)}
            {batch ? " · batch" : ""}
          </span>
        )}
      </div>
      <p className="mt-1.5 text-xs text-muted-foreground">
        {what} is sent to Google to do this. Everything else in Focus Toolkit stays on
        this machine.
      </p>
    </div>
  )
}
