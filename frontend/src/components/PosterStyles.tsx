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
import { contrastRatio, contrastVerdict } from "@/lib/contrast"
import { useConfirm } from "@/lib/useConfirm"
import {
  type BlockAlign,
  type BlockSize,
  type DesignStyle,
  type StyleTextDefault,
  type TextCase,
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

/** Mirrors `posters.SIZE_SCALE`, for the preview only — nothing exports from here. */
const SIZE_FRACTION: Record<string, number> = {
  small: 0.035,
  medium: 0.055,
  large: 0.085,
  huge: 0.135,
}

/** Real words, so the preview shows what a Malayalam poster actually looks like. */
const SAMPLE: Record<string, string> = {
  headline: "ഓണം ഓഫർ",
  offer: "50% OFF",
  occasion: "Onam 2026",
  phone: "9847 000 000",
}

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
  /** Which role's controls are showing. See the note by the chips below. */
  const [openRole, setOpenRole] = useState<(typeof ROLES)[number]["key"]>("headline")
  const { pending: deletePending, confirm: confirmDelete } = useConfirm()
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
        </div>
      </div>

      {/*
        A look is judged by eye, not by reading eight numbers. Built from the
        same helpers the designer's stage uses (`cqh` units inside a
        `containerType: size` box), so the proportions here are the proportions
        on the page.
      */}
      <div className="space-y-1.5">
        <Label>Preview</Label>
        <div
          className="mx-auto w-full max-w-[240px] overflow-hidden rounded-lg border"
          style={{
            aspectRatio: "210 / 297",
            containerType: "size",
            background: draft.text_defaults.background_colour ?? "#1b1b22",
          }}
        >
          <div className="flex h-full flex-col justify-center gap-[2cqh] px-[6cqw]">
            {ROLES.map((role) => {
              const spec = draft.text_defaults[role.key] ?? {}
              const fraction =
                spec.size_fraction ?? SIZE_FRACTION[spec.size ?? "medium"] ?? 0.055
              return (
                <div
                  key={role.key}
                  className="malayalam truncate"
                  style={{
                    color: spec.colour ?? "#ffffff",
                    fontSize: `${fraction * 100}cqh`,
                    fontWeight: spec.weight === "bold" ? 700 : 400,
                    letterSpacing: `${spec.tracking ?? 0}em`,
                    lineHeight: spec.leading ?? 1.25,
                    textAlign:
                      spec.align === "left"
                        ? "left"
                        : spec.align === "right"
                          ? "right"
                          : "center",
                  }}
                >
                  {spec.case === "upper"
                    ? SAMPLE[role.key].toUpperCase()
                    : SAMPLE[role.key]}
                </div>
              )
            })}
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          A4 proportions, sample words. This is how the look reads — it is not a
          fit check, because no canvas is chosen here.
        </p>
      </div>

      {/*
        One role at a time. Eight controls across four roles is thirty-two, and
        flat that buries the colour and size the operator actually came for —
        while the preview above shows all four together, which is what they need
        to judge.

        The chips reuse the `aria-pressed` pattern the style list above already
        uses, so nothing new had to be built for them.
      */}
      <div className="space-y-3">
        <div className="flex flex-wrap gap-2" role="group" aria-label="Which line to style">
          {ROLES.map((role) => (
            <button
              key={role.key}
              type="button"
              aria-pressed={openRole === role.key}
              onClick={() => setOpenRole(role.key)}
              className={`rounded-full border px-3 py-1 text-xs transition-colors ${
                openRole === role.key
                  ? "border-primary bg-accent font-medium"
                  : "border-border hover:border-primary/50"
              }`}
            >
              {role.label}
            </button>
          ))}
        </div>

        {(() => {
          const role = ROLES.find((r) => r.key === openRole) ?? ROLES[0]
          const spec = draft.text_defaults[role.key] ?? {}
          const background = draft.text_defaults.background_colour ?? "#1b1b22"
          const ratio = contrastRatio(spec.colour ?? "#ffffff", background)
          const groupId = `style-role-${role.key}`
          return (
            <div
              role="group"
              aria-labelledby={groupId}
              className="space-y-3 rounded-lg border p-3"
            >
              <p id={groupId} className="text-sm font-medium">
                {role.label}
              </p>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label htmlFor={`style-${role.key}`}>Colour</Label>
                  <div className="flex gap-2">
                    <Input
                      id={`style-${role.key}`}
                      type="color"
                      value={spec.colour ?? "#ffffff"}
                      onChange={(e) => editRole(role.key, { colour: e.target.value })}
                      className="h-9 w-14 p-1"
                    />
                    {/* Paired with a text field so a brand hex can be pasted. */}
                    <Input
                      aria-label={`${role.label} colour as hex`}
                      value={spec.colour ?? "#ffffff"}
                      onChange={(e) => editRole(role.key, { colour: e.target.value })}
                      className="h-9 flex-1 font-mono text-xs"
                    />
                  </div>
                  {ratio !== null && (
                    <p
                      className={`text-xs ${
                        ratio >= 4.5
                          ? "text-muted-foreground"
                          : "text-[color:var(--warn)]"
                      }`}
                    >
                      {contrastVerdict(ratio)} against this style's background.
                    </p>
                  )}
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor={`style-${role.key}-size`}>Size</Label>
                  <Select
                    value={spec.size ?? "medium"}
                    onValueChange={(v) =>
                      // Choosing a preset clears any exact size, so the two
                      // controls never disagree about which is in force.
                      editRole(role.key, { size: v as BlockSize, size_fraction: null })
                    }
                  >
                    <SelectTrigger id={`style-${role.key}-size`}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {SIZES.map((sz) => (
                        <SelectItem key={sz} value={sz}>
                          {sz}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label htmlFor={`style-${role.key}-weight`}>Weight</Label>
                  <Select
                    value={spec.weight ?? "regular"}
                    onValueChange={(v) =>
                      editRole(role.key, { weight: v as "regular" | "bold" })
                    }
                  >
                    <SelectTrigger id={`style-${role.key}-weight`}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="regular">Regular</SelectItem>
                      <SelectItem value="bold">Bold</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={`style-${role.key}-align`}>Align</Label>
                  <Select
                    value={spec.align ?? "centre"}
                    onValueChange={(v) =>
                      editRole(role.key, { align: v as BlockAlign })
                    }
                  >
                    <SelectTrigger id={`style-${role.key}-align`}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="left">Left</SelectItem>
                      <SelectItem value="centre">Centre</SelectItem>
                      <SelectItem value="right">Right</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-3">
                <div className="space-y-1.5">
                  <Label htmlFor={`style-${role.key}-pct`}>Exact size %</Label>
                  <Input
                    id={`style-${role.key}-pct`}
                    type="number"
                    min={1}
                    max={40}
                    step={0.5}
                    placeholder="preset"
                    value={
                      spec.size_fraction != null
                        ? Number((spec.size_fraction * 100).toFixed(1))
                        : ""
                    }
                    onChange={(e) =>
                      editRole(role.key, {
                        size_fraction:
                          e.target.value.trim() === ""
                            ? null
                            : Number(e.target.value) / 100,
                      })
                    }
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={`style-${role.key}-tracking`}>Letter spacing</Label>
                  <Input
                    id={`style-${role.key}-tracking`}
                    type="number"
                    min={-0.05}
                    max={0.5}
                    step={0.01}
                    value={spec.tracking ?? 0}
                    onChange={(e) =>
                      editRole(role.key, { tracking: Number(e.target.value) })
                    }
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={`style-${role.key}-leading`}>Line spacing</Label>
                  <Input
                    id={`style-${role.key}-leading`}
                    type="number"
                    min={0.8}
                    max={3}
                    step={0.05}
                    value={spec.leading ?? 1.25}
                    onChange={(e) =>
                      editRole(role.key, { leading: Number(e.target.value) })
                    }
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor={`style-${role.key}-case`}>Letter case</Label>
                <Select
                  value={spec.case ?? "as-typed"}
                  onValueChange={(v) => editRole(role.key, { case: v as TextCase })}
                >
                  <SelectTrigger id={`style-${role.key}-case`}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="as-typed">As typed</SelectItem>
                    <SelectItem value="upper">UPPERCASE</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  Malayalam has no capitals, so this changes nothing there.
                </p>
              </div>
            </div>
          )
        })()}
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
              // A shop-added look is not restorable — only shipped ones are.
              if (!confirmDelete(selected.id)) return
              await deleteStyle(selected.id)
              await refresh()
              toast.success("Style removed")
            }}
          >
            {deletePending === selected.id ? "Click again to delete" : "Delete"}
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
