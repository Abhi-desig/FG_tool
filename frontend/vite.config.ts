import path from "node:path"

import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname, "./src") },
  },
  server: {
    // `npm run dev` serves the UI; the Python app owns /api.
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  build: {
    // FastAPI serves this directory in production. See ARCHITECTURE.md.
    outDir: "dist",
    emptyOutDir: true,
  },
})
