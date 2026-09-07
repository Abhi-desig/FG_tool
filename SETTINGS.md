# Settings — Focus Toolkit

**Status:** Draft for review · **Last updated:** 2026-08-22

A sixth tab in the sidebar, alongside the five features. It exists so the operator can change how
the tool behaves **without touching code** — which is the difference between a tool they own and a
tool they depend on someone else for.

Three sections: **API Keys** · **Prompt Library** · **Preferences**.

---

## 1 · API Keys

The only part of this app with direct monetary value. Handling rules are in
[SECURITY.md](SECURITY.md); this is the behaviour.

### Keys held

| Key | Needed for | Required |
|---|---|---|
| `GEMINI_API_KEY` | Phase 5 — photo editing, poster artwork | Yes, for Phase 5 |
| `FAL_API_KEY` | Optional fallback (Qwen-Image-Edit) | No |
| `REPLICATE_API_TOKEN` | Optional fallback | No |

### Behaviour

- **Entry** — paste into a masked field, save. The plaintext value goes straight to the backend and
  is never held in React state longer than the submit.
- **Storage** — SQLite, **encrypted at rest** with Fernet. The encryption key lives in the OS
  keyring (`keyring` package), with a machine-local key file outside the repo as fallback.
- **Display** — the API returns `{"is_set": true, "hint": "…a3f9"}`. **The real key is never sent to
  the browser**, not even to prefill a field. Re-entering replaces; it does not reveal.
- **Test connection** — a button per key making the cheapest possible real call. Clear result:
  ✅ *Connected* / ❌ *Invalid key* / ⚠️ *No internet*. Guessing whether a key works is a waste of a
  shop's afternoon.
- **Delete** — one click, confirmed. Removes the row entirely.

### Spend tracking

Beneath the keys, a running estimate against the **₹2,000/month** budget:

```
This month:  ₹430  of ₹2,000
             ████░░░░░░░░░░░░░░░░  22%
             38 photo edits · 12 posters (batch)
```

Estimated locally from job history and the per-model rates below — **it is an estimate, and the UI
must say so.** Google's console is the source of truth.

| Model | Purpose | Approx. cost |
|---|---|---|
| `gemini-3.1-flash-image` | Photo edits | ~₹4 |
| `gemini-3-pro-image` | Poster artwork (instant) | ~₹11.5 |
| `gemini-3-pro-image` **batch** | Poster artwork (batch) | **~₹6** |

Rates are configurable here, because Google will change them and the operator should not need a
code change to stay accurate.

---

## 2 · Prompt Library

Named, editable prompt templates. **This is what makes poster and photo output tunable without
touching code** — the operator finds a phrasing that works for their clients and keeps it.

### Structure

Every template has a name, a scope, body text with `{{variables}}`, and history.

| Scope | Used by | Example variables |
|---|---|---|
| `poster-concept` | Phase 4 — turns the poster's words into a visual idea | `{{main}}` `{{h1}}` `{{h2}}` |
| `photo-edit` | Phase 5 — instructs an edit | `{{instruction}}` `{{preserve}}` |
| `translation-hint` | Phase 3 — domain context for the translator | `{{client}}` `{{domain}}` |

### Behaviour

- **Seeded defaults** ship with the app — a working set on day one, not an empty screen.
- **Duplicate** before editing, so a working prompt is never lost to experimentation.
- **Restore default** on any seeded template, always available.
- **Version history** per template. Every save keeps the previous body; the operator can read and
  roll back. Cheap to build on SQLite and it removes all fear of editing.
- **Variable validation** — warn on save if the body references a variable the scope does not
  provide, or omits a required one. Catch it at edit time, not mid-job.
- **Preview** — render the template with sample values to see what the model will actually receive.
- **Active template** per scope; the feature screens use it and show which one is in play.

`poster-concept` is the only poster template left here, and it is a small one: it describes the
picture, and its answer fills `{{concept}}` in whichever design the operator picked. **The designs
themselves are not in this library** — they are files in `data/poster_prompts/`, because they are
the shop's own work and belong somewhere it can edit, copy and back them up with a text editor
(ADR-034).

---

## 2a · Poster designs

**Not a settings screen.** The looks the poster designer offers are files, one per design, in
`data/poster_prompts/`. Adding one is dropping a file in; it appears without restarting the
server. See [ADR-034](DECISIONS.md) and the README in that folder.

| Part | What it is |
|---|---|
| filename | The stable handle. `festival-flex.md` is the design `festival-flex` |
| `name:` | What the operator sees in the dropdown. Falls back to the filename |
| `description:` | One line under the dropdown |
| body | The prompt, after a `---` line. Variables: `{{main}}` `{{h1}}` `{{h2}}` `{{concept}}` |

### Behaviour

- **The copy is the brief.** The operator's own tagged words are substituted into the design, so
  the poster is made from the message rather than from a second description.
- **`{{concept}}`** is the visual idea. Written by `poster-concept` from the copy, or left empty
  when the operator supplied a reference picture for the model to work from instead.
- **An unknown placeholder is emptied, not sent.** A `{{offer}}` a poster cannot fill would
  otherwise reach Google as a bare label and spend money on a confused request. The line it sat
  on goes with it, and the mismatch is written to the log so a typo is findable.
- **A bad file is skipped, never fatal.** One mistyped header must not empty the whole screen.
- **The prompt body never reaches the browser.** It is the shop's own work, and this tool is shown
  to clients.

> **What went with the old styles screen.** `poster_styles`, its six seeded looks, its text
> colours, and `styles.validate()` — which refused any style body that did not forbid lettering.
> That rule existed because a model handed a Malayalam headline will draw it, and will draw it
> wrong. It is now the operator's job to read the words on every generated poster.

---

## 2b · AI models

Which Gemini model does which job — posters, photo editing, the visual idea. Chosen from a list
fetched live from Google, not hardcoded. See [ADR-026](DECISIONS.md).

- **Refresh from Google** lists what the key can actually call.
- **Testing the key** repairs any configured name Google has since retired, and says which.
- Prices shown are this app's estimate **for the job**, not a quote from Google. Choosing a larger
  model costs more than the figure shown.

---

## 3 · Preferences

| Setting | Options | Default | Notes |
|---|---|---|---|
| **Models directory** | Path picker | `./models` | Point at the 932 GB HDD. Shows free space and current usage |
| **Device** | Auto · CPU · CUDA · CoreML | Auto | Auto-detected; override for troubleshooting. Shows what was detected |
| **Default output DPI** | 72 · 150 · 300 | 300 | Per-job override on the image screen |
| **Colour profile** | RGB · CMYK | CMYK | For print output |
| **Default print units** | mm · inches · feet | feet | Feet suits flex banner work |
| **Active client** | Dropdown + manage | — | **Scopes the translation glossary.** See below |
| **Theme** | Light · Dark · System | System | |
| **Batch mode by default** | On / Off | **On** | Half price on poster artwork |
| **Open browser on start** | On / Off | On | |

### Active client

More than a label — it selects which **glossary** the Phase 3 translator uses. Client A's brand
terms must never leak into Client B's catalogue ([ROADMAP.md](ROADMAP.md) Phase 3).

Managing clients lives here: add, rename, archive, and view or edit each client's glossary terms.
The current client is visible in the app header at all times, because translating into the wrong
glossary is a silent, expensive error.

---

## Data model

```sql
api_keys         (name PK, ciphertext, hint, created_at, last_tested_at, last_test_ok)
prompts          (id PK, scope, name, body, is_default, is_active, updated_at)
prompt_versions  (id PK, prompt_id FK, body, saved_at)
preferences      (key PK, value)
clients          (id PK, name, archived)
glossary         (id PK, client_id FK, source_term, target_term, notes)
corrections      (id PK, client_id FK NULLABLE, source, source_norm, target,
                  origin, learned_from, updated_at)          -- ADR-029
jobs             (id PK, feature, model, cost_paise, status, created_at)
```

`corrections` is whole-cell recall, and deliberately **not** the glossary: the glossary masks a
*phrase* inside a sentence, this remembers an entire cell the operator already approved so the next
sheet fills it in offline and free. `client_id IS NULL` means shop-wide; a client-scoped row
overrides it. Two partial unique indexes rather than one constraint, because SQLite treats NULLs as
distinct. See [ADR-029](DECISIONS.md).

`jobs` backs the spend tracker and the history view. It stores **metadata and paths, never copies of
client files** ([SECURITY.md](SECURITY.md)).

## API surface

```
GET    /api/settings/keys           → [{name, is_set, hint, last_test_ok}]   never plaintext
PUT    /api/settings/keys/{name}    ← {value}
DELETE /api/settings/keys/{name}
POST   /api/settings/keys/{name}/test

GET    /api/settings/prompts?scope=
POST   /api/settings/prompts
PUT    /api/settings/prompts/{id}
POST   /api/settings/prompts/{id}/restore-default
GET    /api/settings/prompts/{id}/versions

GET    /api/settings/preferences
PUT    /api/settings/preferences

GET    /api/settings/clients
POST   /api/settings/clients
GET    /api/settings/clients/{id}/glossary
PUT    /api/settings/clients/{id}/glossary

GET    /api/corrections?client_id=&q=&limit=&offset=
PUT    /api/corrections                      ← {client_id, corrections:[{source, target}]}
DELETE /api/corrections/{id}
POST   /api/corrections/import               ← multipart .xlsx, two columns
GET    /api/corrections/export               → .xlsx
POST   /api/corrections/forget-job           ← {job_id}   undoes one export's learning

GET    /api/settings/spend?month=
```

## Build order

Settings is cross-cutting, so it is built incrementally alongside the phase that needs it — not all
at once up front:

| Built during | What appears |
|---|---|
| Phase 1 | The tab, Preferences skeleton, theme |
| Phase 2 | Models directory, device override, DPI and colour profile |
| Phase 3 | Clients and glossary management ✅ · translation engine table ✅ |
| Phase 4 | Poster designs in `data/poster_prompts/`, plus the `poster-concept` template |
| Phase 5 | API keys ✅ · connection testing ✅ · spend tracking ✅ · full prompt library ✅ |
