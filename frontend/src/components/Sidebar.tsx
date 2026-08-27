import { useEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { features } from "@/lib/api"
import { type Theme, useTheme } from "@/lib/useTheme"

/** The five features plus Settings. Phase 1 ships the first one. */
// Cutout lives inside Image tools rather than as its own screen — the operator
// assesses print size and cuts out in one pass, on one image.
const FEATURES = [
  { id: "fonts", label: "Malayalam converter", phase: 1 },
  { id: "images", label: "Image tools", phase: 2 },
  { id: "excel", label: "Excel translator", phase: 3 },
  { id: "posters", label: "Poster designer", phase: 4 },
] as const

const READY_THROUGH_PHASE = 4

export type FeatureId = (typeof FEATURES)[number]["id"] | "settings"

const THEME_LABEL: Record<Theme, string> = {
  light: "Light",
  dark: "Dark",
  system: "System",
}

interface Props {
  active: FeatureId
  onSelect: (id: FeatureId) => void
}

export function Sidebar({ active, onSelect }: Props) {
  const { theme, cycle } = useTheme()

  /**
   * Which features this install can actually serve.
   *
   * A base install has no Pillow, no openpyxl and no cryptography, so those
   * screens cannot work — and until the routers were mounted lazily the server
   * would not start at all (NEXT.md 2.6). Showing a screen whose every button
   * 404s is worse than saying which command installs it.
   */
  const [missing, setMissing] = useState<Record<string, string>>({})

  useEffect(() => {
    features()
      .then((f) => setMissing(f.unavailable))
      .catch(() => setMissing({}))
  }, [])

  return (
    <nav
      aria-label="Features"
      className="flex w-60 shrink-0 flex-col gap-1 border-r bg-sidebar p-3"
    >
      <div className="px-2 py-3">
        <p className="text-sm font-semibold tracking-tight">Focus Toolkit</p>
        <p className="text-xs text-muted-foreground">Pre-press assistant</p>
      </div>

      {FEATURES.map((feature) => {
        const notInstalled = missing[feature.id]
        const ready = feature.phase <= READY_THROUGH_PHASE && !notInstalled
        return (
          <Button
            key={feature.id}
            variant={active === feature.id ? "secondary" : "ghost"}
            disabled={!ready}
            // Colour and weight are not enough on their own — a screen reader
            // needs to be told which item is the current one (NEXT.md 3.10).
            aria-current={active === feature.id ? "page" : undefined}
            title={notInstalled}
            onClick={() => onSelect(feature.id)}
            className="h-auto justify-start px-2 py-2 text-left font-normal"
          >
            <span className="flex w-full items-center justify-between gap-2">
              <span className={ready ? "" : "text-muted-foreground"}>{feature.label}</span>
              {notInstalled ? (
                <span className="text-[11px] text-muted-foreground">Not installed</span>
              ) : (
                !ready && (
                  <span className="text-[11px] text-muted-foreground">
                    Phase {feature.phase}
                  </span>
                )
              )}
            </span>
          </Button>
        )
      })}

      <Separator className="my-2" />

      <Button
        variant={active === "settings" ? "secondary" : "ghost"}
        aria-current={active === "settings" ? "page" : undefined}
        onClick={() => onSelect("settings")}
        className="h-auto justify-start px-2 py-2 text-left font-normal"
      >
        <span className="flex w-full items-center justify-between gap-2">
          <span>Settings</span>
        </span>
      </Button>

      <div className="mt-auto px-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={cycle}
          className="w-full justify-start px-2 font-normal text-muted-foreground"
        >
          Theme: {THEME_LABEL[theme]}
        </Button>
      </div>
    </nav>
  )
}
