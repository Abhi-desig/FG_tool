import { useCallback, useEffect, useState } from "react"

export type Theme = "light" | "dark" | "system"

const KEY = "focus-toolkit-theme"

function apply(theme: Theme): void {
  const dark =
    theme === "dark" ||
    (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches)
  document.documentElement.classList.toggle("dark", dark)
}

/** Theme preference, persisted locally. Defaults to following the OS. */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem(KEY) as Theme | null) ?? "system",
  )

  useEffect(() => {
    apply(theme)
    localStorage.setItem(KEY, theme)

    if (theme !== "system") return
    const media = window.matchMedia("(prefers-color-scheme: dark)")
    const onChange = () => apply("system")
    media.addEventListener("change", onChange)
    return () => media.removeEventListener("change", onChange)
  }, [theme])

  const cycle = useCallback(() => {
    setTheme((current) =>
      current === "light" ? "dark" : current === "dark" ? "system" : "light",
    )
  }, [])

  return { theme, setTheme, cycle }
}
