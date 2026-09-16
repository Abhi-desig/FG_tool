"""SQLite storage. Stdlib `sqlite3`, no ORM — see ADR-009.

The full schema is here — `preferences`, `clients`, `glossary`, `corrections`,
`api_keys`, `prompts`, `prompt_versions` and `ai_spend`. The tables are
created together in `init()` rather than per phase: an empty table costs nothing,
and the alternative was a migration step on a machine with no one to run it.

API keys are stored encrypted; `crypto` owns that and this module never handles
a plaintext key except to hand it straight to `crypto.encrypt`.

Schema reference and the meaning of each preference key: SETTINGS.md.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from backend import config, crypto, textkey

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS preferences (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clients (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE,
    archived INTEGER NOT NULL DEFAULT 0
);

-- Scoped to a client, never global. Client A's brand terms must not leak into
-- client B's catalogue (ROADMAP.md Phase 3). The UNIQUE constraint is what makes
-- correcting a term once fix every later occurrence.
CREATE TABLE IF NOT EXISTS glossary (
    id          INTEGER PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    source_term TEXT NOT NULL,
    target_term TEXT NOT NULL,
    notes       TEXT NOT NULL DEFAULT '',
    UNIQUE (client_id, source_term)
);

CREATE INDEX IF NOT EXISTS glossary_client ON glossary(client_id);

-- Whole-cell recall, and deliberately not the glossary.
--
-- The glossary masks a *phrase* inside a sentence (features/glossary.py). This
-- remembers an entire cell the operator has already approved, so the second
-- member list never repeats the first one's spelling. A member list is 33,000
-- cells of proper nouns and the offline model fabricates on them — measured,
-- ELAVUNKAL VEEDU came back as the Malayalam for "Eucalyptus" (ADR-028) — so
-- the cheapest correct answer is the one the operator already gave.
--
-- `client_id IS NULL` means shop-wide. A house name written by sound is right
-- for every client, and the shop sees a given co-operative's sheet about once a
-- year, so a purely per-client memory would be empty exactly when it mattered.
-- A scoped row overrides the shop-wide one where a trade term genuinely differs
-- (ADR-029).
CREATE TABLE IF NOT EXISTS corrections (
    id           INTEGER PRIMARY KEY,
    client_id    INTEGER REFERENCES clients(id) ON DELETE CASCADE,
    -- Kept as it was learned, for the Settings list and the Excel round trip.
    source       TEXT NOT NULL,
    -- What lookup matches on: whitespace collapsed and casefolded, because the
    -- same name arrives as "ELAVUNKAL  VEEDU" one year and "Elavunkal Veedu"
    -- the next. See backend/textkey.py, which both this module and the
    -- translator normalise through so they cannot drift apart.
    source_norm  TEXT NOT NULL,
    target       TEXT NOT NULL,
    -- 'operator' when they typed it, 'claude-kept' when they exported a paid
    -- correction unchanged. Decides nothing; it is how the operator tells, a
    -- year later, where a wrong entry came from.
    origin       TEXT NOT NULL DEFAULT 'operator',
    -- The job that taught it, so one bad export can be undone in full.
    learned_from TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL
);

-- Two partial indexes, not one constraint: SQLite treats NULLs as distinct in a
-- UNIQUE constraint, so `UNIQUE (client_id, source_norm)` would happily store
-- the same shop-wide string a thousand times.
CREATE UNIQUE INDEX IF NOT EXISTS corrections_global
    ON corrections(source_norm) WHERE client_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS corrections_scoped
    ON corrections(client_id, source_norm) WHERE client_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS corrections_job ON corrections(learned_from);

-- The operator's corrections to the bundled word library (ADR-032).
--
-- Shop-wide only, with no `client_id`. The library is a dictionary, not a
-- client's terminology: if the shop decides `standee` is സ്റ്റാൻഡി, that is
-- true on every sheet, and a per-client version of it already exists in
-- `glossary` for the cases where a client genuinely wants something else.
--
-- Stored as a diff rather than a copy of the whole dictionary, so a refreshed
-- `data/dictionary/en-ml.tsv.gz` brings 59,000 new answers without discarding
-- the handful the operator has fixed.
CREATE TABLE IF NOT EXISTS dictionary_overrides (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    -- Matched through backend/textkey.py, the same normalisation the library
    -- and the corrections memory use. All three must agree or an override
    -- silently stops applying.
    source_norm TEXT NOT NULL UNIQUE,
    target      TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Ciphertext only. The plaintext key never lands in this file, and never
-- crosses the API boundary to the browser (SECURITY.md).
CREATE TABLE IF NOT EXISTS api_keys (
    name           TEXT PRIMARY KEY,
    ciphertext     BLOB NOT NULL,
    hint           TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    last_tested_at TEXT,
    last_test_ok   INTEGER
);

-- Editable prompt templates, so poster and photo output can be tuned without
-- touching code (SETTINGS.md).
CREATE TABLE IF NOT EXISTS prompts (
    id         INTEGER PRIMARY KEY,
    scope      TEXT NOT NULL,
    name       TEXT NOT NULL,
    body       TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    is_active  INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE (scope, name)
);

-- Every save keeps the previous body, so a working prompt is never lost to an
-- experiment. Cheap on SQLite and it removes all fear of editing.
CREATE TABLE IF NOT EXISTS prompt_versions (
    id        INTEGER PRIMARY KEY,
    prompt_id INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
    body      TEXT NOT NULL,
    saved_at  TEXT NOT NULL
);

-- `poster_styles` used to sit here. Design styles are files now, under
-- `data/poster_prompts/` (ADR-034), so nothing writes this table any more.
-- It is dropped from the schema rather than from the database: an existing
-- data.db keeps its rows, because deleting the shop's saved styles to tidy up a
-- CREATE statement would be a poor trade.

-- Money. Recorded in paise so the arithmetic is exact — floats and currency
-- are a bad pairing and this total is compared against Google's console.
CREATE TABLE IF NOT EXISTS ai_spend (
    id         INTEGER PRIMARY KEY,
    feature    TEXT NOT NULL,
    model      TEXT NOT NULL,
    batch      INTEGER NOT NULL DEFAULT 0,
    cost_paise INTEGER NOT NULL,
    status     TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS spend_month ON ai_spend(created_at);
CREATE INDEX IF NOT EXISTS prompts_scope ON prompts(scope);
"""

# Every preference the UI can set, with its default. A key not listed here is
# rejected, so a typo cannot quietly create a setting nothing reads.
DEFAULTS: dict[str, str] = {
    "default_dpi": "300",
    "colour_profile": "cmyk",
    "print_units": "feet",
    "device": "auto",
    "theme": "system",
    "batch_by_default": "true",
    # Which Gemini model each job uses. Settings rather than constants because
    # Google renames and retires these, and a rename should cost the operator a
    # dropdown, not a code change — the 404 that shipped in 0.1.0 was exactly
    # this mistake. Defaults are resolved against the live model list the first
    # time a key is tested; see backend/features/ai.py.
    "ai_model_layout": "",
    "ai_model_artwork": "",
    "ai_model_photo": "",
    # Poster wording (ADR-030). Missing from this list, `ai.resolve_models`
    # would raise the moment Google retired any model, because it writes every
    # role's preference back after repairing a stale name.
    "ai_model_copy": "",
    # Which Claude model checks the Malayalam, for the same reason as above.
    # `features/verify.py` has read this key since it shipped, but it was never
    # listed here — so `set_preferences` refused it, `get_preferences` filtered
    # it out, and the model was permanently the hardcoded default. That is the
    # exact stale-model failure the module's own comment says it exists to
    # prevent, and `verify.friendly_error` told the operator to fix it "in
    # Settings" where no such control existed. Empty means the shipped default.
    "verify_model": "",
}


def connect() -> sqlite3.Connection:
    """One connection per thread; sqlite3 objects are not thread-safe."""
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    if conn is None:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(config.DB_PATH, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        _local.conn = conn
    return conn


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    cur = connect().cursor()
    try:
        yield cur
    finally:
        cur.close()


def init() -> None:
    connect()


def get_preferences() -> dict[str, Any]:
    """All preferences, defaults filled in for anything never set."""
    values = dict(DEFAULTS)
    with cursor() as cur:
        for row in cur.execute("SELECT key, value FROM preferences"):
            if row["key"] in DEFAULTS:
                values[row["key"]] = row["value"]
    return values


def set_preferences(updates: dict[str, Any]) -> dict[str, Any]:
    """Write known preferences. Unknown keys are refused, not ignored."""
    unknown = set(updates) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown preference(s): {', '.join(sorted(unknown))}")
    with cursor() as cur:
        cur.executemany(
            "INSERT INTO preferences(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            [(k, str(v)) for k, v in updates.items()],
        )
    return get_preferences()


# --- clients ---------------------------------------------------------------


class NotFound(LookupError):
    """No such client."""


def list_clients(include_archived: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT id, name, archived FROM clients"
    if not include_archived:
        sql += " WHERE archived = 0"
    sql += " ORDER BY name COLLATE NOCASE"
    with cursor() as cur:
        return [dict(r) for r in cur.execute(sql)]


def add_client(name: str) -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise ValueError("a client needs a name")
    with cursor() as cur:
        cur.execute(
            "INSERT INTO clients(name) VALUES(?) ON CONFLICT(name) DO NOTHING", (name,)
        )
        row = cur.execute(
            "SELECT id, name, archived FROM clients WHERE name = ?", (name,)
        ).fetchone()
    return dict(row)


def get_client(client_id: int) -> dict[str, Any]:
    with cursor() as cur:
        row = cur.execute(
            "SELECT id, name, archived FROM clients WHERE id = ?", (client_id,)
        ).fetchone()
    if row is None:
        raise NotFound(f"no client with id {client_id}")
    return dict(row)


def archive_client(client_id: int, archived: bool = True) -> dict[str, Any]:
    get_client(client_id)
    with cursor() as cur:
        cur.execute(
            "UPDATE clients SET archived = ? WHERE id = ?", (int(archived), client_id)
        )
    return get_client(client_id)


# --- glossary --------------------------------------------------------------


def get_glossary(client_id: int) -> list[dict[str, Any]]:
    """One client's approved terms. Never returns another client's terms."""
    get_client(client_id)
    with cursor() as cur:
        return [
            dict(r)
            for r in cur.execute(
                "SELECT id, source_term, target_term, notes FROM glossary "
                "WHERE client_id = ? ORDER BY LENGTH(source_term) DESC, source_term",
                (client_id,),
            )
        ]


def upsert_terms(client_id: int, terms: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Add or correct terms.

    Correcting one term fixes every later occurrence — that is the whole point
    of the glossary, and it is why (client_id, source_term) is unique rather
    than allowing duplicates to accumulate.
    """
    get_client(client_id)
    rows = []
    for term in terms:
        source = (term.get("source_term") or "").strip()
        target = (term.get("target_term") or "").strip()
        if not source or not target:
            continue
        rows.append((client_id, source, target, (term.get("notes") or "").strip()))
    if rows:
        with cursor() as cur:
            cur.executemany(
                "INSERT INTO glossary(client_id, source_term, target_term, notes) "
                "VALUES(?, ?, ?, ?) "
                "ON CONFLICT(client_id, source_term) DO UPDATE SET "
                "  target_term = excluded.target_term, notes = excluded.notes",
                rows,
            )
    return get_glossary(client_id)


def delete_term(client_id: int, term_id: int) -> list[dict[str, Any]]:
    with cursor() as cur:
        cur.execute(
            "DELETE FROM glossary WHERE id = ? AND client_id = ?", (term_id, client_id)
        )
    return get_glossary(client_id)


# --- corrections memory ----------------------------------------------------
#
# ADR-029. A cell the operator has already approved is filled from here before
# the model or the paid check ever runs, so the same spelling mistake cannot
# recur and cannot be charged for twice.

# Matches `excel.MAX_CELL_CHARS`. A source longer than a cell can hold could
# never be looked up, so storing it would only ever be noise.
MAX_CORRECTION_CHARS = 5000


def _correction_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "client_id": row["client_id"],
        "source": row["source"],
        "target": row["target"],
        "origin": row["origin"],
        "learned_from": row["learned_from"],
        "updated_at": row["updated_at"],
    }


def _scope_clause(client_id: int | None) -> tuple[str, tuple[Any, ...]]:
    """The WHERE fragment for one scope.

    Branching rather than passing a sentinel: `client_id = NULL` is never true
    in SQL, so a single parameterised clause would silently return nothing for
    the shop-wide scope.
    """
    if client_id is None:
        return "client_id IS NULL", ()
    return "client_id = ?", (client_id,)


def get_corrections(
    client_id: int | None = None,
    query: str = "",
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """One scope's remembered cells, newest first.

    Always paginated. A shop that has exported a few member lists will have tens
    of thousands of these, and sending them all to the browser to filter there
    is the obvious version of this that does not survive its first real sheet.
    """
    where, params = _scope_clause(client_id)
    if query.strip():
        like = f"%{query.strip()}%"
        where += " AND (source LIKE ? OR target LIKE ?)"
        params += (like, like)
    with cursor() as cur:
        return [
            _correction_row(r)
            for r in cur.execute(
                f"SELECT * FROM corrections WHERE {where} "
                "ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
                (*params, max(1, min(limit, 1000)), max(0, offset)),
            )
        ]


def count_corrections(client_id: int | None = None, query: str = "") -> int:
    where, params = _scope_clause(client_id)
    if query.strip():
        like = f"%{query.strip()}%"
        where += " AND (source LIKE ? OR target LIKE ?)"
        params += (like, like)
    with cursor() as cur:
        row = cur.execute(
            f"SELECT COUNT(*) AS n FROM corrections WHERE {where}", params
        ).fetchone()
    return int(row["n"])


def corrections_map(client_id: int | None = None) -> dict[str, str]:
    """Everything that applies to this sheet, keyed by `textkey.normalise`.

    One query, not two: shop-wide rows are ordered first and the client's own
    rows last, so building the dict in order lets a scoped correction overwrite
    the global one for the same cell. This is the hot path — it runs once per
    sheet and is then consulted per cell — so it is deliberately a plain dict
    lookup rather than a query per row.
    """
    with cursor() as cur:
        if client_id is None:
            rows = cur.execute(
                "SELECT source_norm, target FROM corrections WHERE client_id IS NULL"
            )
        else:
            rows = cur.execute(
                "SELECT source_norm, target FROM corrections "
                "WHERE client_id IS NULL OR client_id = ? "
                "ORDER BY (client_id IS NULL) DESC",
                (client_id,),
            )
        return {r["source_norm"]: r["target"] for r in rows}


def upsert_corrections(
    client_id: int | None,
    pairs: list[tuple[str, str]],
    origin: str = "operator",
    learned_from: str = "",
) -> dict[str, int]:
    """Remember these cells, reporting what actually changed.

    Unusable pairs are counted as `skipped` rather than raised: this is called
    from the export path with thousands of rows at once, and failing a whole
    export because one cell was blank would cost the operator the file they
    asked for.
    """
    if client_id is not None:
        get_client(client_id)

    clean: dict[str, tuple[str, str]] = {}
    skipped = 0
    for source, target in pairs:
        source = (source or "").strip()
        target = (target or "").strip()
        norm = textkey.normalise(source)
        # `has_latin` because `excel.extract` only ever produces Latin-bearing
        # sources: a Malayalam string in the English column can never match a
        # cell, and refusing it here is what makes a swapped-column import
        # visible instead of silently poisoning the memory.
        if (
            not source
            or not target
            or not norm
            or len(source) > MAX_CORRECTION_CHARS
            or len(target) > MAX_CORRECTION_CHARS
            or not textkey.has_latin(source)
        ):
            skipped += 1
            continue
        # Last one wins within a single call, so a sheet containing the same
        # cell twice does not depend on insertion order.
        clean[norm] = (source, target)

    if not clean:
        return {"added": 0, "updated": 0, "skipped": skipped}

    where, params = _scope_clause(client_id)
    with cursor() as cur:
        # Counted before writing: `rowcount` reports 1 for both halves of an
        # upsert and cannot tell an insert from an update.
        existing = {
            r["source_norm"]
            for r in cur.execute(
                f"SELECT source_norm FROM corrections WHERE {where}", params
            )
            if r["source_norm"] in clean
        }
        now = _now()
        rows = [
            (client_id, source, norm, target, origin, learned_from, now)
            for norm, (source, target) in clean.items()
        ]
        conflict = (
            "ON CONFLICT(source_norm) WHERE client_id IS NULL"
            if client_id is None
            else "ON CONFLICT(client_id, source_norm) WHERE client_id IS NOT NULL"
        )
        cur.executemany(
            "INSERT INTO corrections"
            "(client_id, source, source_norm, target, origin, learned_from, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?) "
            f"{conflict} DO UPDATE SET "
            "  source = excluded.source, target = excluded.target, "
            "  origin = excluded.origin, learned_from = excluded.learned_from, "
            "  updated_at = excluded.updated_at",
            rows,
        )
    return {
        "added": len(clean) - len(existing),
        "updated": len(existing),
        "skipped": skipped,
    }


def delete_correction(correction_id: int) -> None:
    with cursor() as cur:
        cur.execute("DELETE FROM corrections WHERE id = ?", (correction_id,))


def forget_job(job_id: str) -> int:
    """Undo everything one export taught, and say how much that was.

    Learning happens automatically on export, so a single click on a 33,000-row
    sheet can write tens of thousands of entries. That is the intended payoff,
    and this is why it is not frightening: it is one operation to take back.
    """
    if not job_id:
        return 0
    with cursor() as cur:
        cur.execute("DELETE FROM corrections WHERE learned_from = ?", (job_id,))
        return cur.rowcount


# --- word library overrides ------------------------------------------------


def dictionary_overrides() -> dict[str, str]:
    """The operator's corrections to the bundled library, keyed for lookup.

    Same shape and same hot-path reasoning as `corrections_map`: read once per
    sheet, then consulted per cell.
    """
    with cursor() as cur:
        rows = cur.execute("SELECT source_norm, target FROM dictionary_overrides")
        return {r["source_norm"]: r["target"] for r in rows}


def list_dictionary_overrides() -> list[dict[str, object]]:
    """Every override, newest first, for the Word library panel."""
    with cursor() as cur:
        rows = cur.execute(
            "SELECT id, source, source_norm, target, updated_at "
            "FROM dictionary_overrides ORDER BY updated_at DESC, source"
        )
        return [dict(r) for r in rows]


def upsert_dictionary_overrides(pairs: list[tuple[str, str]]) -> dict[str, int]:
    """Save these corrections, reporting what actually changed.

    Unusable pairs are skipped rather than raised, for the reason
    `upsert_corrections` gives: this runs over a whole imported spreadsheet, and
    one blank row must not cost the operator the import.
    """
    clean: dict[str, tuple[str, str]] = {}
    skipped = 0
    for source, target in pairs:
        source = (source or "").strip()
        target = (target or "").strip()
        norm = textkey.normalise(source)
        if (
            not source
            or not target
            or not norm
            or len(source) > MAX_CORRECTION_CHARS
            or len(target) > MAX_CORRECTION_CHARS
            or not textkey.has_latin(source)
        ):
            skipped += 1
            continue
        clean[norm] = (source, target)

    if not clean:
        return {"added": 0, "updated": 0, "skipped": skipped}

    with cursor() as cur:
        # Counted before writing, because `rowcount` cannot tell an insert from
        # an update in an upsert.
        existing = {
            r["source_norm"]
            for r in cur.execute("SELECT source_norm FROM dictionary_overrides")
            if r["source_norm"] in clean
        }
        now = _now()
        cur.executemany(
            "INSERT INTO dictionary_overrides(source, source_norm, target, updated_at) "
            "VALUES(?, ?, ?, ?) "
            "ON CONFLICT(source_norm) DO UPDATE SET "
            "  source = excluded.source, target = excluded.target, "
            "  updated_at = excluded.updated_at",
            [(source, norm, target, now) for norm, (source, target) in clean.items()],
        )
    return {
        "added": len(clean) - len(existing),
        "updated": len(existing),
        "skipped": skipped,
    }


def delete_dictionary_override(override_id: int) -> None:
    """Drop one override, putting the bundled answer back in force."""
    with cursor() as cur:
        cur.execute("DELETE FROM dictionary_overrides WHERE id = ?", (override_id,))

# --- API keys --------------------------------------------------------------

KEY_NAMES = (
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "FAL_API_KEY",
    "REPLICATE_API_TOKEN",
    # Not a paid key, and it buys nothing on its own — it unlocks a *gated*
    # Hugging Face repo, which is IndicTrans2's only door. Stored here rather
    # than in `.env` for the same reason as the others: the operator is not a
    # programmer, and "edit a hidden file next to the app" is not an
    # instruction, while a field in Settings is. Encrypted at rest by the same
    # code, and never written anywhere that could be emailed.
    "HF_TOKEN",
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def list_api_keys() -> list[dict[str, Any]]:
    """What the Settings screen may see. Never the key itself."""
    with cursor() as cur:
        rows = {
            r["name"]: dict(r)
            for r in cur.execute(
                "SELECT name, hint, created_at, last_tested_at, last_test_ok FROM api_keys"
            )
        }
    return [
        {
            "name": name,
            "is_set": name in rows,
            "hint": rows.get(name, {}).get("hint"),
            "last_tested_at": rows.get(name, {}).get("last_tested_at"),
            "last_test_ok": (
                None
                if rows.get(name, {}).get("last_test_ok") is None
                else bool(rows[name]["last_test_ok"])
            ),
        }
        for name in KEY_NAMES
    ]


def set_api_key(name: str, value: str) -> None:
    if name not in KEY_NAMES:
        raise ValueError(f"unknown key {name!r}; expected one of {', '.join(KEY_NAMES)}")
    value = value.strip()
    if not value:
        raise ValueError("an API key cannot be blank")
    with cursor() as cur:
        cur.execute(
            "INSERT INTO api_keys(name, ciphertext, hint, created_at) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET ciphertext=excluded.ciphertext, "
            "  hint=excluded.hint, created_at=excluded.created_at, "
            "  last_tested_at=NULL, last_test_ok=NULL",
            (name, crypto.encrypt(value), crypto.hint(value), _now()),
        )


def get_api_key(name: str) -> str | None:
    """Decrypt for server-side use only. Must never be returned by a route."""
    with cursor() as cur:
        row = cur.execute(
            "SELECT ciphertext FROM api_keys WHERE name = ?", (name,)
        ).fetchone()
    if row is None:
        return None
    return crypto.decrypt(row["ciphertext"])


def delete_api_key(name: str) -> None:
    with cursor() as cur:
        cur.execute("DELETE FROM api_keys WHERE name = ?", (name,))


def record_key_test(name: str, ok: bool) -> None:
    with cursor() as cur:
        cur.execute(
            "UPDATE api_keys SET last_tested_at = ?, last_test_ok = ? WHERE name = ?",
            (_now(), int(ok), name),
        )


# --- spend -----------------------------------------------------------------


def record_spend(
    feature: str, model: str, cost_paise: int, batch: bool, status: str
) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO ai_spend(feature, model, batch, cost_paise, status, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (feature, model, int(batch), cost_paise, status, _now()),
        )


def spend_summary(month: str | None = None) -> dict[str, Any]:
    """Totals for the current month, in paise.

    Only successful calls count toward the total — a refused request costs
    nothing, and showing it as spend would make the figure disagree with
    Google's console for no reason.
    """
    month = month or datetime.now(UTC).strftime("%Y-%m")
    with cursor() as cur:
        rows = list(
            cur.execute(
                "SELECT feature, model, batch, COUNT(*) AS runs, SUM(cost_paise) AS paise "
                "FROM ai_spend WHERE substr(created_at, 1, 7) = ? AND status = 'ok' "
                "GROUP BY feature, model, batch",
                (month,),
            )
        )
        failures = cur.execute(
            "SELECT COUNT(*) AS n FROM ai_spend "
            "WHERE substr(created_at, 1, 7) = ? AND status != 'ok'",
            (month,),
        ).fetchone()["n"]

    breakdown = [dict(r) for r in rows]
    return {
        "month": month,
        "total_paise": sum(int(r["paise"] or 0) for r in breakdown),
        "runs": sum(int(r["runs"]) for r in breakdown),
        "failed_runs": int(failures),
        "breakdown": breakdown,
    }
