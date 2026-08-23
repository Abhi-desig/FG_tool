import { useState } from "react"

import { type FeatureId, Sidebar } from "@/components/Sidebar"
import { ExcelTranslator } from "@/features/ExcelTranslator"
import { FontConverter } from "@/features/FontConverter"
import { PosterDesigner } from "@/features/PosterDesigner"
import { ImageTools } from "@/features/ImageTools"
import { Settings } from "@/features/Settings"

export default function App() {
  const [active, setActive] = useState<FeatureId>("fonts")

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar active={active} onSelect={setActive} />
      <main className="flex-1 overflow-y-auto">
        {/* Designed for 1366×768 (the shop PC) upward — see DESIGN.md. */}
        <div className="mx-auto max-w-[1200px] px-6 py-6">
          {active === "fonts" && <FontConverter />}
          {active === "images" && <ImageTools />}
          {active === "excel" && <ExcelTranslator />}
          {active === "posters" && <PosterDesigner />}
          {active === "settings" && <Settings />}
        </div>
      </main>
    </div>
  )
}
