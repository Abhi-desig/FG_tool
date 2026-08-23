/**
 * Copying is the whole point of the converter, so it cannot depend on one API.
 *
 * `navigator.clipboard.writeText` is the good path, but it can be denied by
 * browser policy even on localhost in a secure context. We fall back to the
 * legacy `execCommand("copy")`, which needs no permission but does need to run
 * inside a user gesture — so it works from a button click and not from an
 * async auto-copy. When neither works we leave the text selected, which always
 * lets the operator press ⌘C / Ctrl+C.
 */

export type CopyOutcome = "copied" | "selected" | "failed"

/** True if the async clipboard is usable, so we can promise auto-copy. */
export async function canAutoCopy(): Promise<boolean> {
  if (!navigator.clipboard?.writeText) return false
  try {
    const status = await navigator.permissions.query({
      name: "clipboard-write" as PermissionName,
    })
    return status.state !== "denied"
  } catch {
    // Firefox cannot be queried. Assume it works; the fallback covers us.
    return true
  }
}

export async function copyText(
  text: string,
  source: HTMLTextAreaElement | null,
): Promise<CopyOutcome> {
  if (!text) return "failed"

  try {
    await navigator.clipboard.writeText(text)
    return "copied"
  } catch {
    // Fall through to the legacy path.
  }

  if (!source) return "failed"
  source.focus()
  source.select()
  try {
    if (document.execCommand("copy")) return "copied"
  } catch {
    // Fall through; the text is at least selected now.
  }
  return "selected"
}
