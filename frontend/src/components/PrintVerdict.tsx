import type { Assessment } from "@/lib/api"

/**
 * The loudest element on the image screen (DESIGN.md).
 *
 * Colour is never the only signal — an icon and plain words carry the same
 * message, because a wrong "looks fine" costs a reprint and because roughly one
 * man in twelve cannot rely on red versus green.
 */
const STYLES = {
  good: {
    icon: "✓",
    ring: "border-[color:var(--ok)]/40 bg-[color:var(--ok)]/10",
    text: "text-[color:var(--ok)]",
  },
  caution: {
    icon: "!",
    ring: "border-[color:var(--warn)]/40 bg-[color:var(--warn)]/10",
    text: "text-[color:var(--warn)]",
  },
  too_small: {
    icon: "✕",
    ring: "border-destructive/40 bg-destructive/10",
    text: "text-destructive",
  },
} as const

const DESCRIBING_LABEL: Record<string, string> = {
  enlarged: "After enlarging",
  // NEXT.md 3.6: this card used to sit unlabelled above a finished cutout,
  // reading as a judgement on the result. Cutting out does not change the pixel
  // count, so the verdict is still correct — it is simply about the upload, and
  // it now says which.
  source: "About the image you uploaded",
}

export function PrintVerdict({
  assessment,
  describing = "original",
}: {
  assessment: Assessment
  /**
   * Which pixels this verdict is about: the upload on its own (`original`), the
   * enlarged result (`enlarged`), or the upload while a different result is on
   * screen beside it (`source`).
   */
  describing?: "original" | "enlarged" | "source"
}) {
  const style = STYLES[assessment.verdict]
  const label = DESCRIBING_LABEL[describing]

  return (
    <section
      aria-live="polite"
      className={`rounded-xl border p-5 ${style.ring}`}
    >
      {label && (
        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </p>
      )}
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className={`mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full border text-base font-bold ${style.text}`}
        >
          {style.icon}
        </span>
        <div className="min-w-0">
          <h2 className={`text-[28px] font-bold leading-tight ${style.text}`}>
            {assessment.headline}
          </h2>
          <p className="mt-2 text-sm text-foreground/80">{assessment.detail}</p>
        </div>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
        <Fact label="Prints at" value={`${assessment.effective_dpi} DPI`} />
        <Fact label="Needs" value={`${assessment.required_dpi} DPI`} />
        <Fact
          label="Largest good size"
          value={`${assessment.max_w}×${assessment.max_h} ${assessment.unit}`}
        />
        {assessment.crop_fraction > 0.02 ? (
          <Fact
            label="Cropped to fit"
            value={`${Math.round(assessment.crop_fraction * 100)}%`}
          />
        ) : (
          <Fact label="Cropping" value="None" />
        )}
      </dl>

      <details className="mt-4">
        <summary className="cursor-pointer text-sm text-muted-foreground hover:text-foreground">
          What else could this image be used for?
        </summary>
        <table className="mt-2 w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted-foreground">
              <th className="py-1 font-medium">Job</th>
              <th className="py-1 font-medium">Viewed</th>
              <th className="py-1 text-right font-medium">Largest good size</th>
            </tr>
          </thead>
          <tbody>
            {assessment.best_use.map((row) => (
              <tr
                key={row.print_class}
                className={
                  row.print_class === assessment.print_class
                    ? "font-medium"
                    : "text-foreground/80"
                }
              >
                <td className="py-1">{row.label}</td>
                <td className="py-1 text-muted-foreground">{row.viewing}</td>
                <td className="py-1 text-right tabular-nums">
                  {row.max_w}×{row.max_h} {assessment.unit}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  )
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular-nums">{value}</dd>
    </div>
  )
}
