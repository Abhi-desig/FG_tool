import { useState } from "react"
import { toast } from "sonner"

import { type FeatureId, Sidebar } from "@/components/Sidebar"
import { ExcelTranslator } from "@/features/ExcelTranslator"
import { FontConverter } from "@/features/FontConverter"
import { ImageTools } from "@/features/ImageTools"
import { PosterDesigner } from "@/features/PosterDesigner"
import { Settings } from "@/features/Settings"

const FEATURES: { id: FeatureId; render: () => React.ReactNode }[] = [
  { id: "fonts", render: () => <FontConverter /> },
  { id: "images", render: () => <ImageTools /> },
  { id: "excel", render: () => <ExcelTranslator /> },
  { id: "posters", render: () => <PosterDesigner /> },
  { id: "settings", render: () => <Settings /> },
]

export default function App() {
  const [active, setActive] = useState<FeatureId>("fonts")

  /**
   * Screens stay mounted once they have been opened.
   *
   * They used to be conditionally rendered, so navigating away unmounted the
   * screen and threw its state away. An 80-second cutout was destroyed by
   * clicking *Excel translator* and back: the dropzone came back empty while
   * the result was still sitting on the server, downloadable (NEXT.md 0.4).
   *
   * Mounted lazily rather than all at once, so a fresh start still makes only
   * the requests the first screen needs; kept mounted afterwards, so no screen
   * can lose work the operator has waited for. Hidden with `display: none` —
   * `hidden` also takes them out of the accessibility tree, which is what we
   * want for a screen that is not on show.
   */
  const [visited, setVisited] = useState<Set<FeatureId>>(new Set(["fonts"]))

  const select = (next: FeatureId) => {
    setActive(next)
    setVisited((prev) => (prev.has(next) ? prev : new Set(prev).add(next)))
    // NEXT.md 3.5: toasts survived five navigations and covered live UI —
    // they obscured a step heading and the models table. A notice about the
    // screen you just left has no business on the one you just opened.
    toast.dismiss()
  }

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar active={active} onSelect={select} />
      <main className="flex-1 overflow-y-auto">
        {/* Designed for 1366×768 (the shop PC) upward — see DESIGN.md. */}
        <div className="mx-auto max-w-[1200px] px-6 py-6">
          {FEATURES.filter((f) => visited.has(f.id)).map((feature) => (
            <div key={feature.id} hidden={feature.id !== active}>
              {feature.render()}
            </div>
          ))}
        </div>
      </main>
    </div>
  )
}
