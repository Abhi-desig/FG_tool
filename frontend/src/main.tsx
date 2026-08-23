import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import App from "@/App"
import { TooltipProvider } from "@/components/ui/tooltip"
import { Toaster } from "@/components/ui/sonner"
import "@/index.css"

const root = document.getElementById("root")
if (!root) throw new Error("#root is missing from index.html")

createRoot(root).render(
  <StrictMode>
    <TooltipProvider delayDuration={400}>
      <App />
      <Toaster position="bottom-right" />
    </TooltipProvider>
  </StrictMode>,
)
