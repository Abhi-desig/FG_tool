import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import type { SheetColumn } from "@/lib/api"

/**
 * Which columns hold people and places rather than words.
 *
 * A translation model handed a name has to find meaning, so it invents: a real
 * member list returned "Eucalyptus" for a house name, and one row came back
 * carrying a fabricated citation. Ticked columns are written by **sound**
 * instead — offline, by rule, no model involved.
 *
 * The ticks are a suggestion the operator confirms, never a decision taken for
 * them. Spelling a real word by sound is exactly as wrong as translating a
 * name, so the cost of guessing runs both ways and the operator is the one who
 * knows which column is which.
 */
export function NameColumns({
  columns,
  picked,
  onChange,
}: {
  columns: SheetColumn[]
  picked: Set<string>
  onChange: (next: Set<string>) => void
}) {
  if (columns.length === 0) return null

  const toggle = (key: string) => {
    const next = new Set(picked)
    if (next.has(key)) {
      next.delete(key)
    } else {
      next.add(key)
    }
    onChange(next)
  }

  return (
    <section className="space-y-3 rounded-xl border bg-card p-4">
      <div>
        <h2 className="text-sm font-medium">Which columns are names?</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Names, house names and places are written by sound, never translated —
          otherwise the model tries to find a meaning in them and invents one. Headings
          are always translated, even in a ticked column.
        </p>
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        {columns.map((column) => (
          <label
            key={column.key}
            htmlFor={`name-col-${column.key}`}
            className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors ${
              picked.has(column.key)
                ? "border-primary bg-accent"
                : "border-border hover:border-primary/50"
            }`}
          >
            <Checkbox
              id={`name-col-${column.key}`}
              checked={picked.has(column.key)}
              onCheckedChange={() => toggle(column.key)}
              className="mt-0.5"
            />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <Label
                  htmlFor={`name-col-${column.key}`}
                  className="cursor-pointer font-medium"
                >
                  {column.header || `Column ${column.letter}`}
                </Label>
                <Badge variant="outline" className="text-[11px] font-normal">
                  {column.letter} · {column.count.toLocaleString()}
                </Badge>
              </div>
              {/* The values, not just the heading. A member list's columns are
                  often headed "A", "B", "C" or nothing at all. */}
              <p className="mt-1 truncate text-xs text-muted-foreground">
                {column.sample.join(" · ") || "no other values"}
              </p>
            </div>
          </label>
        ))}
      </div>
    </section>
  )
}
