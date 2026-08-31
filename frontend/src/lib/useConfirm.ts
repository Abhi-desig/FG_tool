import { useCallback, useRef, useState } from "react"

/**
 * Two clicks before anything is destroyed.
 *
 * DESIGN.md principle 5 is "nothing is lost", and four controls in this app
 * deleted on a single click with no undo — a glossary term, an API key, a saved
 * style, a remembered correction. shadcn has no `alert-dialog` vendored here and
 * a modal for this is heavier than the risk; a button that asks once and forgets
 * after a few seconds is the cheapest honest guard.
 *
 * Returns the id currently awaiting confirmation and a function that returns
 * true only on the second click for the same id.
 */
export function useConfirm(resetAfterMs = 4000) {
  const [pending, setPending] = useState<string | number | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const confirm = useCallback(
    (id: string | number): boolean => {
      if (pending === id) {
        if (timer.current) clearTimeout(timer.current)
        setPending(null)
        return true
      }
      setPending(id)
      if (timer.current) clearTimeout(timer.current)
      timer.current = setTimeout(() => setPending(null), resetAfterMs)
      return false
    },
    [pending, resetAfterMs],
  )

  return { pending, confirm }
}
