import { useCallback, useMemo, useState } from "react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { ApiError, type CategoricalColumn, approveCategorical } from "@/lib/api"

/**
 * The values a repeated column uses, decided once by a person.
 *
 * **Why this is not left to the model.** A Department column is ten words
 * repeated thirty times. Sent to the model that is thirty guesses at the same
 * ten words, differently each sheet — measured, `IT` came back as the Malayalam
 * for the pronoun "it" and `Operations` as ആക്രമണങ്ങൾ, military attacks.
 * Approved here they become locked glossary terms and are right on every sheet
 * from now on, at the cost of one pass over ten rows (ADR-035).
 *
 * The suggestions come from the word library only, never from the model: a
 * machine guess offered as the default would hand the column straight back to
 * the thing it was taken away from.
 */
export function CategoricalApproval({
  columns,
  clientId,
  onApproved,
}: {
  columns: CategoricalColumn[]
  clientId: number | null
  onApproved: () => void
}) {
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)

  const pending = useMemo(
    () =>
      columns.flatMap((column) =>
        column.values.filter((v) => !v.approved).map((v) => ({ column, ...v })),
      ),
    [columns],
  )

  const value = useCallback(
    (source: string, suggested: string) => edits[source] ?? suggested,
    [edits],
  )

  const approve = useCallback(async () => {
    if (clientId == null) return
    const pairs = pending
      .map((row) => ({
        source_term: row.source,
        target_term: value(row.source, row.suggested).trim(),
      }))
      .filter((p) => p.target_term)

    if (pairs.length !== pending.length) {
      toast.error("Fill in every value first", {
        description: "A blank one would be left to the model, which is the point of this.",
      })
      return
    }

    setBusy(true)
    try {
      await approveCategorical(clientId, pairs)
      toast.success(`${pairs.length} values locked`, {
        description: "These are now fixed on every sheet for this client.",
      })
      onApproved()
    } catch (err: unknown) {
      toast.error(
        err instanceof ApiError ? err.message : "Could not save those values",
      )
    } finally {
      setBusy(false)
    }
  }, [clientId, pending, value, onApproved])

  if (columns.length === 0 || pending.length === 0) return null

  return (
    <section className="space-y-3 rounded-xl border border-[color:var(--warn)]/50 bg-[color:var(--warn)]/10 p-4">
      <div>
        <h2 className="text-sm font-medium">
          Approve these once, and they are fixed for good
        </h2>
        <p className="mt-1 text-xs text-muted-foreground">
          {columns.map((c) => c.header || c.key).join(", ")} repeat a short list of
          values. Decide them here and they become locked terms for this client —
          they are never machine-translated again. The suggestions come from the word
          library, not from the model.
        </p>
      </div>

      {clientId == null ? (
        <p className="text-xs text-muted-foreground">
          Pick a client above first. Approved values are locked to one client&rsquo;s
          glossary, because one shop&rsquo;s <em>Operations</em> is not another&rsquo;s.
        </p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <Table className="min-w-[520px] table-fixed">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[42%]">English</TableHead>
                  <TableHead>Malayalam</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {pending.map((row) => (
                  <TableRow key={`${row.column.key}-${row.source}`}>
                    <TableCell className="align-top">
                      {row.source}
                      {!row.suggested && (
                        <Badge variant="outline" className="ml-2 text-[11px]">
                          not in the library
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="align-top">
                      <Input
                        value={value(row.source, row.suggested)}
                        className="malayalam h-8 py-1"
                        aria-label={`Malayalam for ${row.source}`}
                        placeholder="type it once"
                        onChange={(e) =>
                          setEdits((prev) => ({ ...prev, [row.source]: e.target.value }))
                        }
                      />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          <Button size="sm" disabled={busy} onClick={() => void approve()}>
            {busy ? "Saving…" : `Lock these ${pending.length} values`}
          </Button>
        </>
      )}
    </section>
  )
}
