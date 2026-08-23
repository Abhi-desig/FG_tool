"""SQLite storage. Stdlib `sqlite3`, no ORM — see ADR-009.

Phase 2 uses the `preferences` table only. The rest of the schema in
SETTINGS.md (clients, glossary, prompts, api_keys, jobs) arrives with the phase
that needs it; creating empty tables early would just be clutter.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from backend import config, crypto

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


# --- API keys --------------------------------------------------------------

KEY_NAMES = ("GEMINI_API_KEY", "FAL_API_KEY", "REPLICATE_API_TOKEN")


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
