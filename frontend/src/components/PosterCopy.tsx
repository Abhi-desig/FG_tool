import { useState } from "react"
import { toast } from "sonner"

import { AiCopyWriter } from "@/components/AiCopyWriter"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import { type BlockRole, type CopyLine, splitCopy } from "@/lib/api"

/** The four lines nearly every poster this shop prints actually has. */
export type Copy = Record<Exclude<BlockRole, "free">, string>

export const EMPTY_COPY: Copy = { headline: "", offer: "", occasion: "", phone: "" }

const FIELDS: {
  role: Exclude<BlockRole, "free">
  label: string
  placeholder: string
}[] = [
  { role: "headline", label: "Headline", placeholder: "ഗ്രാൻഡ് സെയിൽ" },
  { role: "offer", label: "Offer", placeholder: "50% OFF" },
  { role: "occasion", label: "Occasion", placeholder: "Onam" },
  { role: "phone", label: "Phone", placeholder: "9847 000 000" },
]

/**
 * Step one: the words.
 *
 * **The paste box is the real input.** Work arrives as one WhatsApp message,
 * and retyping it into four fields is the slowest part of the job and the one
 * where a phone number loses a digit. So the whole thing goes in at once and
 * the app sorts it out.
 *
 * The four fields below are the *result*, not a second form to fill in — they
 * are there so every guess is visible and correctable. Anything the splitter
 * could not place becomes an extra line on the poster rather than vanishing;
 * losing a client's wording without saying so is the one outcome worth
 * designing against.
 *
 * Malayalam is handled throughout: the browser shapes it, so what is on screen
 * is what prints.
 */
export function PosterCopy({
  copy,
  onChange,
  onSplit,
}: {
  copy: Copy
  onChange: (next: Copy) => void
  onSplit: (lines: CopyLine[]) => void
}) {
  const [pasted, setPasted] = useState("")
  const [busy, setBusy] = useState(false)
  const [extras, setExtras] = useState<string[]>([])

  const split = async (text: string) => {
    if (!text.trim()) return
    setBusy(true)
    try {
      const result = await splitCopy(text)
      onSplit(result.lines)
      const spare = result.lines.filter((l) => l.role === "free").map((l) => l.text)
      setExtras(spare)
      toast.success(`${result.lines.length} lines sorted`, {
        description: spare.length
          ? `${spare.length} extra line${spare.length === 1 ? "" : "s"} added to the poster — check where they landed.`
          : "Check the guesses below and fix anything wrong.",
      })
    } catch {
      toast.error("Could not read that. Type the lines in below instead.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <Label htmlFor="copy-paste">Paste the whole message</Label>
        <Textarea
          id="copy-paste"
          value={pasted}
          rows={6}
          spellCheck={false}
          className="malayalam text-sm"
          placeholder={"ഓണം\nഗ്രാൻഡ് സെയിൽ\n50% OFF\n9847 000 000"}
          onChange={(e) => setPasted(e.target.value)}
          // Sort it the moment it lands. Waiting for a button press is one more
          // thing to explain to someone who just wants the poster.
          onPaste={(e) => {
            const text = e.clipboardData.getData("text")
            if (text.trim()) {
              e.preventDefault()
              setPasted(text)
              void split(text)
            }
          }}
        />
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void split(pasted)}
            disabled={busy || !pasted.trim()}
          >
            {busy ? "Sorting…" : "Sort it out"}
          </Button>
          {pasted && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setPasted("")
                setExtras([])
              }}
            >
              Clear
            </Button>
          )}
          <span className="text-xs text-muted-foreground">
            Straight from WhatsApp — one line each.
          </span>
        </div>
      </div>

      {extras.length > 0 && (
        <p className="rounded-lg border bg-muted/50 p-2 text-xs text-muted-foreground">
          <strong className="text-foreground">{extras.length} line</strong>
          {extras.length === 1 ? " was" : "s were"} not one of the four below, so{" "}
          {extras.length === 1 ? "it is" : "they are"} on the poster as{" "}
          {extras.length === 1 ? "an extra line" : "extra lines"}:{" "}
          <span className="malayalam text-foreground">{extras.join(" · ")}</span>
        </p>
      )}

      <Separator />

      <p className="text-xs text-muted-foreground">
        What it found. Change anything that went to the wrong place.
      </p>

      {FIELDS.map((field) => (
        <div key={field.role} className="space-y-1.5">
          <Label htmlFor={`copy-${field.role}`}>{field.label}</Label>
          <Input
            id={`copy-${field.role}`}
            value={copy[field.role]}
            placeholder={field.placeholder}
            className="malayalam"
            onChange={(e) => onChange({ ...copy, [field.role]: e.target.value })}
          />
        </div>
      ))}

      <p className="text-xs text-muted-foreground">
        Every line stays real, editable text — the picture never contains words.
      </p>

      <Separator />

      {/*
        Offline and free is above; paid is below. An install with no Gemini key
        loses nothing by this being here.
      */}
      <AiCopyWriter copy={copy} onUse={(patch) => onChange({ ...copy, ...patch })} />
    </div>
  )
}
