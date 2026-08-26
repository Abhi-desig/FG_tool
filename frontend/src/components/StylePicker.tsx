import { Badge } from "@/components/ui/badge"
import type { DesignStyle } from "@/lib/api"

/**
 * The looks the shop works from.
 *
 * Shown as swatches rather than a dropdown of names, because the operator is
 * choosing an appearance and "Festival" is not one until you have seen it. The
 * prompt behind each look lives in Settings; here it is deliberately invisible.
 */
export function StylePicker({
  styles,
  chosen,
  onChoose,
}: {
  styles: DesignStyle[]
  chosen: string | null
  onChoose: (style: DesignStyle) => void
}) {
  if (styles.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No design styles yet. Add one in Settings.
      </p>
    )
  }

  return (
    <div className="grid grid-cols-2 gap-2">
      {styles.map((style) => {
        const selected = style.key === chosen
        return (
          <button
            key={style.key}
            type="button"
            onClick={() => onChoose(style)}
            aria-pressed={selected}
            className={`group rounded-lg border p-2 text-left transition-colors outline-offset-2 hover:bg-accent ${
              selected ? "border-primary bg-accent ring-2 ring-primary" : ""
            }`}
          >
            <span
              className="flex h-10 overflow-hidden rounded-md border"
              aria-hidden="true"
            >
              {(style.swatches.length ? style.swatches : ["#888888"]).map((colour, i) => (
                <span
                  key={`${colour}-${i}`}
                  className="flex-1"
                  style={{ backgroundColor: colour }}
                />
              ))}
            </span>
            <span className="mt-1.5 flex items-center gap-1.5">
              <span className="text-sm font-medium">{style.name}</span>
              {/* Colour is never the only signal — DESIGN.md. */}
              {selected && (
                <Badge variant="secondary" className="px-1.5 py-0 text-[10px]">
                  chosen
                </Badge>
              )}
            </span>
            <span className="mt-0.5 block text-xs leading-snug text-muted-foreground">
              {style.description}
            </span>
          </button>
        )
      })}
    </div>
  )
}
