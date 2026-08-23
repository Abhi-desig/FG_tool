import { useCallback, useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { ApiError, convert, type Direction } from "@/lib/api"
import { canAutoCopy, copyText } from "@/lib/clipboard"

const SAMPLE = "കേരളം"
const DEBOUNCE_MS = 180

/**
 * Phase 1. Paste Malayalam from WhatsApp, get ML-TTKarthika on the clipboard.
 *
 * DESIGN.md: the paste box is focused on load, conversion is live, and the
 * operator can paste → convert → copy → be back in CorelDRAW without ever
 * touching the mouse.
 */
export function FontConverter() {
  const [input, setInput] = useState("")
  const [output, setOutput] = useState("")
  const [direction, setDirection] = useState<Direction>("to_ascii")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Whether the browser lets us copy without a click. Decides what we promise.
  const [autoCopy, setAutoCopy] = useState(false)

  const inputRef = useRef<HTMLTextAreaElement>(null)
  const outputRef = useRef<HTMLTextAreaElement>(null)
  // Set when the change came from a paste, so that result gets copied.
  const copyWhenReady = useRef(false)

  useEffect(() => {
    inputRef.current?.focus()
    void canAutoCopy().then(setAutoCopy)
  }, [])

  const copy = useCallback(async (automatic: boolean) => {
    const text = outputRef.current?.value ?? ""
    if (!text) return

    const outcome = await copyText(text, outputRef.current)
    if (outcome === "copied") {
      toast.success(automatic ? "Converted and copied" : "Copied", {
        description: "Paste into CorelDRAW with ⌘V / Ctrl+V",
      })
    } else if (outcome === "selected") {
      toast.warning("Press ⌘C / Ctrl+C to copy", {
        description: "Your browser blocks automatic copying. The text is selected.",
      })
    } else {
      toast.error("Could not copy", { description: "Select the output and copy it manually." })
    }
  }, [])

  // Live conversion, debounced. The call is local, so this stays snappy.
  useEffect(() => {
    if (!input) {
      setOutput("")
      setError(null)
      return
    }

    const controller = new AbortController()
    const timer = setTimeout(() => {
      setBusy(true)
      convert(input, direction, controller.signal)
        .then((res) => {
          setOutput(res.result)
          setError(null)
          if (copyWhenReady.current) {
            copyWhenReady.current = false
            // Without the async clipboard we cannot copy outside a gesture, so
            // select the result instead — one keystroke, and never a lie.
            if (autoCopy) void copy(true)
            else outputRef.current?.select()
          }
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted) return
          setError(err instanceof ApiError ? err.message : "Conversion failed.")
          setOutput("")
        })
        .finally(() => {
          if (!controller.signal.aborted) setBusy(false)
        })
    }, DEBOUNCE_MS)

    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [input, direction, autoCopy, copy])

  const swap = useCallback(() => {
    setDirection((d) => (d === "to_ascii" ? "to_unicode" : "to_ascii"))
    setInput(output)
    setOutput(input)
    inputRef.current?.focus()
  }, [input, output])

  const onKeyDown = (event: React.KeyboardEvent) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault()
      void copy(false)
    }
  }

  const toAscii = direction === "to_ascii"

  return (
    <div className="space-y-5" onKeyDown={onKeyDown}>
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Malayalam converter</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Paste from WhatsApp. Get ML-TTKarthika on your clipboard for CorelDRAW.
          </p>
        </div>
        <Badge variant="secondary" className="font-normal">
          Offline · free
        </Badge>
      </header>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Input */}
        <section className="flex flex-col gap-2">
          <div className="flex items-baseline justify-between">
            <Label htmlFor="source">
              {toAscii ? "Malayalam (Unicode)" : "ML-TTKarthika (ASCII)"}
            </Label>
            <span className="text-xs text-muted-foreground">{input.length} chars</span>
          </div>
          <Textarea
            id="source"
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onPaste={() => {
              copyWhenReady.current = true
            }}
            placeholder={toAscii ? `Paste Malayalam here — e.g. ${SAMPLE}` : "Paste ASCII here"}
            spellCheck={false}
            className={`min-h-[240px] resize-y bg-card text-base ${
              toAscii ? "malayalam" : "glyphs"
            }`}
          />
          <p className="text-xs text-muted-foreground">
            {autoCopy
              ? "Pasting converts and copies automatically."
              : "Pasting converts and selects the result — press ⌘C / Ctrl+C."}
          </p>
        </section>

        {/* Output */}
        <section className="flex flex-col gap-2">
          <div className="flex items-baseline justify-between">
            <Label htmlFor="result">
              {toAscii ? "ML-TTKarthika (ASCII)" : "Malayalam (Unicode)"}
            </Label>
            <span className="text-xs text-muted-foreground">
              {busy ? "converting…" : `${output.length} chars`}
            </span>
          </div>
          <Textarea
            id="result"
            ref={outputRef}
            value={output}
            readOnly
            placeholder="Result appears here"
            aria-live="polite"
            className={`min-h-[240px] resize-y bg-muted text-base ${
              toAscii ? "glyphs" : "malayalam"
            }`}
          />
          {toAscii ? (
            <p className="text-xs text-muted-foreground">
              This looks like gibberish here — that is correct. It reads as Malayalam once
              set in ML-TTKarthika.
            </p>
          ) : (
            <p className="text-xs text-muted-foreground">Readable Malayalam text.</p>
          )}
        </section>
      </div>

      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button onClick={() => void copy(false)} disabled={!output}>
              Copy for CorelDRAW
            </Button>
          </TooltipTrigger>
          <TooltipContent>⌘/Ctrl + Enter</TooltipContent>
        </Tooltip>

        <Button variant="outline" onClick={swap} disabled={!input && !output}>
          {toAscii ? "Switch to ASCII → Malayalam" : "Switch to Malayalam → ASCII"}
        </Button>

        <Button
          variant="ghost"
          onClick={() => {
            setInput("")
            setOutput("")
            setError(null)
            inputRef.current?.focus()
          }}
          disabled={!input && !output}
        >
          Clear
        </Button>

        {!input && (
          <Button variant="ghost" className="ml-auto" onClick={() => setInput(SAMPLE)}>
            Try {SAMPLE}
          </Button>
        )}
      </div>
    </div>
  )
}
