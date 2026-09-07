import { Badge } from "@/components/ui/badge"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import type { ColumnClass, SheetColumn } from "@/lib/api"

/** What each class means, in the operator's words rather than the code's. */
const CLASSES: { value: ColumnClass; label: string; note: string }[] = [
  {
    value: "PERSON_NAME",
    label: "People's names",
    note: "Written by sound. Never translated — a name has no meaning to find.",
  },
  {
    value: "ADDRESS",
    label: "Houses and places",
    note: "Broken into parts: known places, then Road/PO/Near, then by sound.",
  },
  {
    value: "CODE",
    label: "Numbers and codes",
    note: "Left exactly as they are, and checked again before the file is written.",
  },
  {
    value: "NUMERIC_DATE",
    label: "Figures and dates",
    note: "Left exactly as they are.",
  },
  {
    value: "CATEGORICAL",
    label: "A short repeated list",
    note: "You approve the values once, then they are fixed on every future sheet.",
  },
  {
    value: "FREE_TEXT",
    label: "Ordinary wording",
    note: "The only kind sent to the translation model.",
  },
]

const LABEL = new Map(CLASSES.map((c) => [c.value, c.label]))

/**
 * What each column holds, confirmed before anything is translated.
 *
 * **Why this screen exists.** Measured on a real employee sheet, the old
 * pipeline sent almost every cell to a sentence-level model and returned ~50
 * wrong ones while flagging 2. `Vishnu Prasad` came back as വിഷ്ണുപുരാണം — the
 * Vishnu Purana. That is not a translation error a better model would avoid: a
 * name has no meaning to find, and a model whose job is to find meaning will
 * invent one. The fix is to route by what the column *is*.
 *
 * The classes are suggestions with reasons attached, never decisions already
 * taken. Guessing wrong is symmetrical — spelling a real word by sound is
 * exactly as wrong as translating a name — so the operator has the last word,
 * and they have it *before* the run rather than after (ADR-035).
 */
export function NameColumns({
  columns,
  picked,
  onChange,
}: {
  columns: SheetColumn[]
  picked: Record<string, ColumnClass>
  onChange: (next: Record<string, ColumnClass>) => void
}) {
  if (columns.length === 0) return null

  const toModel = columns.filter(
    (c) => (picked[c.key] ?? c.cls) === "FREE_TEXT",
  ).length

  return (
    <section className="space-y-3 rounded-xl border bg-card p-4">
      <div>
        <h2 className="text-sm font-medium">What is in each column?</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Only <strong>ordinary wording</strong> is sent to the translation model.
          Names, places, codes and repeated lists are handled without it, because a
          model handed a name has no meaning to find and will invent one. Change
          anything that looks wrong before you translate —{" "}
          {toModel === 0
            ? "nothing here would reach the model."
            : `${toModel} of ${columns.length} would reach it.`}
        </p>
      </div>

      <div className="grid gap-2 lg:grid-cols-2">
        {columns.map((column) => {
          const value = picked[column.key] ?? column.cls
          const changed = value !== column.cls
          return (
            <div
              key={column.key}
              className="space-y-1.5 rounded-lg border p-3"
            >
              <div className="flex flex-wrap items-center gap-2">
                <Label
                  htmlFor={`cls-${column.key}`}
                  className="font-medium"
                >
                  {column.header || `Column ${column.letter}`}
                </Label>
                <Badge variant="outline" className="text-[11px] font-normal">
                  {column.letter} · {column.count.toLocaleString()}
                </Badge>
                {changed && (
                  <Badge variant="secondary" className="text-[11px] font-normal">
                    changed from {LABEL.get(column.cls) ?? column.cls}
                  </Badge>
                )}
              </div>

              <Select
                value={value}
                onValueChange={(next) =>
                  onChange({ ...picked, [column.key]: next as ColumnClass })
                }
              >
                <SelectTrigger id={`cls-${column.key}`} className="h-8">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CLASSES.map((c) => (
                    <SelectItem key={c.value} value={c.value}>
                      {c.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              {/* The reason, and the values. A member list's columns are often
                  headed A, B, C or nothing at all, so the sample is what the
                  operator actually recognises the column by. */}
              <p className="text-xs text-muted-foreground">
                {changed
                  ? CLASSES.find((c) => c.value === value)?.note
                  : column.why}
              </p>
              <p className="truncate text-xs text-muted-foreground/70">
                {column.sample.join(" · ") || "no other values"}
              </p>
            </div>
          )
        })}
      </div>
    </section>
  )
}
