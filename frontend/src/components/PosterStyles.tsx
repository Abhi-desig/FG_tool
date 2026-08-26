import { useCallback, useEffect, useMemo, useState } from "react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import {
  type BlockSize,
  type DesignStyle,
  type StyleTextDefault,
  deleteStyle,
  listStyles,
  restoreStyleDefault,
  saveStyle,
  validateStyle,
} from "@/lib/api"

const ROLES: { key: "headline" | "offer" | "occasion" | "phone"; label: string }[] = [
  { key: "headline", label: "Headline" },
  { key: "offer", label: "Offer" },
  { key: "occasion", label: "Occasion" },
  { key: "phone", label: "Phone" },
]

const SIZES: BlockSize[] = ["small", "medium", "large", "huge"]

const BLANK: Omit<DesignStyle, "id" | "is_default" | "sort_order" | "updated_at"> = {
  key: "",
  name: "",
  description: "",
  palette: "",
  swatches: ["#1b1b22", "#8a8a94", "#f5f5f0"],
  text_defaults: {
    background_colour: "#1b1b22",
    headline: { colour: "#ffffff", size: "large", weight: "bold" },
    offer: { colour: "#ffffff", size: "huge", weight: "bold" },
    occasion: { colour: "#ffffff", size: "medium", weight: "regular" },
    phone: { colour: "#ffffff", size: "small", weight: "regular" },
  },
  body:
    "A background picture for a printed poster made by a shop in Kerala.\n" +
    "\n" +
    "The poster will carry these words:\n" +
    "  Headline: {{headline}}\n" +
    "  Offer: {{offer}}\n" +
    "\n" +
    "Make the picture about that message.\n" +
    "Extra instruction: {{idea}}\n" +
    "\n" +
    "Look: describe the look here.\n" +
    "Colours: {{palette}}\n" +
    "Aspect: {{aspect}}\n" +
    "\n" +
    "The words above are context only. Do NOT draw them. No lettering, no words,\n" +
    "no numbers and no logos anywhere in the image.",
}

/**
 * The shop's design styles, and the prompt structure behind each one.
 *
 * This is the box the poster designer's step 2 draws from. The operator picks a
 * look there and never sees a prompt; the wording is owned here, once, and
 * reused on every job — which is the difference between a house style and
 * whatever anyone happened to type that morning.
 *
 * Editing is safe the same way the prompt library is: a shipped style can always
 * be restored exactly, and only a style the shop added itself can be deleted.
 */
export function PosterStyles() {
  const [styles, setStyles] = useState<DesignStyle[]>([])
  const [variables, setVariables] = useState<string[]>([])
  const [required, setRequired] = useState<string[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [draft, setDraft] = useState(BLANK)
  const [problems, setProblems] = useState<string[]>([])
  const [dirty, setDirty] = useState(false)
  const [creating, setCreating] = useState(false)

  const refresh = useCallback(async (keepId?: number) => {
    const data = await listStyles().catch(() => null)
    if (!data) return
    setStyles(data.styles)
    setVariables(data.variables)
    setRequired(data.required)
    const pick = data.styles.find((s) => s.id === keepId) ?? data.styles[0]
    if (pick) {
      setSelectedId(pick.id)
      setDraft(pick)
      setDirty(false)
      setCreating(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const selected = useMemo(
    () => styles.find((s) => s.id === selectedId) ?? null,
    [styles, selectedId],
  )

  // Check the prompt as the operator types — a style that never forbids
  // lettering costs real money before anyone notices.
  useEffect(() => {
    if (!dirty) return
    const timer = setTimeout(() => {
      validateStyle(draft.body)
        .then((r) => setProblems(r.problems))
        .catch(() => setProblems([]))
    }, 300)
    return () => clearTimeout(timer)
  }, [draft.body, dirty])

  const edit = (patch: Partial<typeof draft>) => {
    setDraft((prev) => ({ ...prev, ...patch }))
    setDirty(true)
  }

  const editRole = (role: (typeof ROLES)[number]["key"], patch: StyleTextDefault) => {
    edit({
      text_defaults: {
        ...draft.text_defaults,
        [role]: { ...draft.text_defaults[role], ...patch },
      },
    })
  }

  const save = async () => {
    try {
      const result = await saveStyle(draft, creating ? undefined : (selectedId ?? undefined))
      setProblems(result.problems)
      await refresh(result.style.id)
      toast.success(creating ? "Style added" : "Style saved")
    } catch (error) {
      toast.error("Could not save that style", {
        description: error instanceof Error ? error.message : undefined,
      })
    }
  }

  return (
    <section className="space-y-4 rounded-xl border bg-card p-4">
      <div>
        <h2 className="text-lg font-semibold">Poster design styles</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Each look the designer can pick, and the fixed prompt behind it. The
          poster's own words are dropped into <code>{"{{headline}}"}</code> and the
          rest when a job runs, so the picture ends up about the message.
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        {styles.map((style) => (
          <button
            key={style.id}
            type="button"
            onClick={() => {
              setSelectedId(style.id)
              setDraft(style)
              setDirty(false)
              setProblems([])
              setCreating(false)
            }}
            aria-pressed={!creating && selectedId === style.id}
            className={`flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-sm outline-offset-2 hover:bg-accent ${
              !creating && selectedId === style.id ? "border-primary bg-accent" : ""
            }`}
          >
            <span className="flex h-4 w-8 overflow-hidden rounded border" aria-hidden="true">
              {(style.swatches.length ? style.swatches : ["#888"]).map((c, i) => (
                <span key={`${c}-${i}`} className="flex-1" style={{ backgroundColor: c }} />
              ))}
            </span>
            {style.name}
          </button>
        ))}
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            setCreating(true)
            setDraft(BLANK)
            setDirty(true)
            setProblems([])
          }}
        >
          Add a style
        </Button>
      </div>

      <Separator />

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="style-name">Name</Label>
          <Input
            id="style-name"
            value={draft.name}
            onChange={(e) => edit({ name: e.target.value })}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="style-key">Key</Label>
          <Input
            id="style-key"
            value={draft.key}
            disabled={!creating && selected?.is_default}
            spellCheck={false}
            className="font-mono text-xs"
            onChange={(e) => edit({ key: e.target.value })}
          />
          <p className="text-xs text-muted-foreground">
            Lowercase, no spaces. Posters remember this, so renaming the style is
            safe but changing the key is not.
          </p>
        </div>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="style-description">One-line description</Label>
        <Input
          id="style-description"
          value={draft.description}
          placeholder="Onam, Vishu, Diwali. Warm, garlanded, lamp-lit."
          onChange={(e) => edit({ description: e.target.value })}
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="style-palette">Colours to ask the AI for</Label>
        <Input
          id="style-palette"
          value={draft.palette}
          placeholder="marigold gold, deep maroon, kasavu cream"
          onChange={(e) => edit({ palette: e.target.value })}
        />
      </div>

      <div className="space-y-1.5">
        <Label>Swatches shown on the card</Label>
        <div className="flex gap-2">
          {[0, 1, 2].map((i) => (
            <Input
              key={i}
              type="color"
              aria-label={`Swatch ${i + 1}`}
              value={draft.swatches[i] ?? "#888888"}
              onChange={(e) => {
                const next = [...draft.swatches]
                next[i] = e.target.value
                edit({ swatches: next })
              }}
              className="h-9 flex-1 p-1"
            />
          ))}
        </div>
      </div>

      <Separator />

      <div className="space-y-2">
        <Label>How this look colours the words</Label>
        <div className="grid gap-2 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="style-bg" className="text-xs font-normal text-muted-foreground">
              Poster background
            </Label>
            <Input
              id="style-bg"
              type="color"
              value={draft.text_defaults.background_colour ?? "#1b1b22"}
              onChange={(e) =>
                edit({
                  text_defaults: {
                    ...draft.text_defaults,
                    background_colour: e.target.value,
                  },
                })
              }
              className="h-9 w-full p-1"
            />
          </div>
          {ROLES.map((role) => (
            <div key={role.key} className="space-y-1.5">
              <Label
                htmlFor={`style-${role.key}`}
                className="text-xs font-normal text-muted-foreground"
              >
                {role.label}
              </Label>
              <div className="flex gap-2">
                <Input
                  id={`style-${role.key}`}
                  type="color"
                  value={draft.text_defaults[role.key]?.colour ?? "#ffffff"}
                  onChange={(e) => editRole(role.key, { colour: e.target.value })}
                  className="h-9 w-14 p-1"
                />
                <Select
                  value={draft.text_defaults[role.key]?.size ?? "medium"}
                  onValueChange={(v) => editRole(role.key, { size: v as BlockSize })}
                >
                  <SelectTrigger aria-label={`${role.label} size`} className="flex-1">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {SIZES.map((s) => (
                      <SelectItem key={s} value={s}>
                        {s}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          ))}
        </div>
      </div>

      <Separator />

      <div className="space-y-2">
        <Label htmlFor="style-body">Prompt structure</Label>
        <div className="flex flex-wrap gap-1">
          {variables.map((v) => (
            <Badge
              key={v}
              variant={required.includes(v) ? "secondary" : "outline"}
              className="cursor-pointer font-mono text-[11px]"
              onClick={() => edit({ body: `${draft.body}{{${v}}}` })}
            >
              {`{{${v}}}`}
              {required.includes(v) ? " *" : ""}
            </Badge>
          ))}
        </div>
        <Textarea
          id="style-body"
          value={draft.body}
          onChange={(e) => edit({ body: e.target.value })}
          spellCheck={false}
          className="min-h-[260px] font-mono text-xs"
        />
      </div>

      {problems.map((p) => (
        <p key={p} className="text-xs text-[color:var(--warn)]">
          {p}
        </p>
      ))}

      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={() => void save()} disabled={!dirty}>
          {creating ? "Add style" : "Save"}
        </Button>
        {!creating && selected?.is_default && (
          <Button
            variant="ghost"
            onClick={async () => {
              const restored = await restoreStyleDefault(selected.id)
              setDraft(restored.style)
              setDirty(false)
              setProblems([])
              await refresh(selected.id)
              toast.success("Default restored")
            }}
          >
            Restore default
          </Button>
        )}
        {!creating && selected && !selected.is_default && (
          <Button
            variant="ghost"
            onClick={async () => {
              await deleteStyle(selected.id)
              await refresh()
              toast.success("Style removed")
            }}
          >
            Delete
          </Button>
        )}
        {creating && (
          <Button variant="ghost" onClick={() => void refresh(selectedId ?? undefined)}>
            Cancel
          </Button>
        )}
        {dirty && <span className="text-xs text-muted-foreground">Unsaved changes</span>}
      </div>
    </section>
  )
}
