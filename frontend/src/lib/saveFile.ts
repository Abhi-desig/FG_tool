/**
 * Hand a downloaded file to the browser, in the one way that actually works.
 *
 * **The failure this exists to stop.** Three screens each did this inline:
 *
 *     const url = URL.createObjectURL(blob)
 *     const link = document.createElement("a")
 *     link.href = url
 *     link.download = name
 *     link.click()
 *     URL.revokeObjectURL(url)      // <- here
 *
 * The revoke runs on the same tick as the click, before the browser has
 * finished reading the blob, so the saved file can be truncated or empty. The
 * server was sending a valid workbook — `file` reported "Microsoft Excel 2007+",
 * the content type and filename were right, the formulas were intact — and what
 * landed on disk would not open in Excel. A corrupt file with the right name is
 * worse than a failed download, because it looks like the export is broken.
 *
 * The anchor was also never attached to the document, which some browsers
 * require before `click()` does anything at all.
 */

/** How long to keep the blob URL alive after the click. */
const REVOKE_DELAY_MS = 60_000

export function saveFile(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement("a")
  link.href = url
  link.download = filename
  // Attached, hidden, clicked, removed. The attach is not decoration: a
  // detached anchor's click() is ignored by some browsers.
  link.style.display = "none"
  document.body.append(link)
  link.click()
  link.remove()

  // Long after the read has certainly finished. Leaving it revoked-later costs
  // one URL entry for a minute; revoking it too early costs the file.
  window.setTimeout(() => URL.revokeObjectURL(url), REVOKE_DELAY_MS)
}
