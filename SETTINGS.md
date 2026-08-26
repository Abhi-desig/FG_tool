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
| `poster-layout` | Phase 4 — asks the AI for a layout plan as JSON | `{{occasion}}` `{{headline}}` `{{offer}}` `{{tone}}` |
| `poster-artwork` | Phase 5 — generates the background image | `{{subject}}` `{{style}}` `{{palette}}` `{{aspect}}` |
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

The poster-layout prompt is the important one. It must reliably return **JSON layout data, never
image text** — that constraint is the foundation of the Malayalam advantage described in
[ROADMAP.md](ROADMAP.md) Phase 4, and it belongs in the seeded default.

---

## 2a · Poster design styles

The looks the poster designer offers in step 2, and the fixed prompt behind each one. See
[ADR-027](DECISIONS.md).

A style is one row in `poster_styles` and carries both halves of a look:

| Field | What it is |
|---|---|
| `key` | Stable handle a poster remembers. Renaming the style is safe; changing this is not. |
| `body` | The fixed prompt structure. Variables: `{{headline}}` `{{offer}}` `{{occasion}}` `{{phone}}` `{{idea}}` `{{subject}}` `{{palette}}` `{{aspect}}` |
| `palette` | Colours to ask the AI for, used when the operator has not overridden them |
| `swatches` | Three hex colours, so the picker shows an appearance rather than a name |
| `text_defaults` | Background colour, and the colour/size/weight each line takes on |

Six ship seeded: **Festival**, **Big offer**, **Wedding**, **Modern minimal**, **Product shot**,
**Event banner**.

### Behaviour

- **The copy is the brief.** The operator's own words are substituted into the style's prompt, so
  the picture is generated from the message on the poster rather than from a second description.
- **`{{idea}}`** is the operator's optional own suggestion for the picture. Left empty, its line
  disappears rather than reaching the model as a bare label.
- **Every style must forbid lettering.** `styles.validate()` refuses a body that does not, and
  warns on an unknown or missing variable — a model handed a Malayalam headline will draw it, and
  will draw it wrong.
- **See exactly what will be sent.** The designer renders the assembled prompt for free before any
  paid call, so nothing about the spend is taken on trust.
- **Restore default** on any seeded style. Only a style the shop added itself can be deleted.

---

## 2b · AI models

Which Gemini model does which job — artwork, photo editing, layout planning. Chosen from a list
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
jobs             (id PK, feature, model, cost_paise, status, created_at)
```

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
| Phase 4 | Prompt library (`poster-layout`) — delivered in Phase 5 alongside the rest |
| Phase 5 | API keys ✅ · connection testing ✅ · spend tracking ✅ · full prompt library ✅ |
