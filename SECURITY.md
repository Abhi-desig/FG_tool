# Security — Focus Toolkit

**Status:** Draft for review · **Last updated:** 2026-08-22

This is a local single-user tool, so the classic web threats (multi-tenant auth, RLS, session
hijacking) largely do not apply. What *does* apply is narrower and more real: **an API key that
costs money if leaked, client files that must not escape the building, and a local server that must
not become a hole in the shop's network.**

---

## 1 · Network exposure

**The server binds to `127.0.0.1` only. Never `0.0.0.0`.**

```python
uvicorn.run(app, host="127.0.0.1", port=8000)
```

Binding to `0.0.0.0` would expose every client's artwork and the API key to anyone on the shop's
Wi-Fi, including customers. There is no authentication layer, because there is no remote access —
that is the trade, and it only holds while the bind address holds.

- No CORS wildcard. The frontend is served from the same origin; no cross-origin access is needed.
- Do not add a "share on the network" feature without redesigning auth first.

## 2 · API keys

Keys are the only thing here with direct monetary value.

- **Never in source.** Never in a committed file. Never logged, never in an error message.
- Stored in SQLite **encrypted at rest** (Fernet). The encryption key lives in the OS keyring, with
  a machine-local key file outside the repo as fallback.
- **Never returned to the browser in plaintext.** The settings API returns a masked form
  (`...a3f9`) and a boolean "is set". The React app never holds a real key.
- `.env` holds bootstrap config only and is gitignored. `.env.example` carries placeholders.
- Rotate the key immediately if it ever appears in a screenshot, log, or paste.

Full behaviour in [SETTINGS.md](SETTINGS.md).

## 3 · No user-supplied path ever reaches a model loader

**This is the lesson of CVE-2026-40086**, a real path-traversal flaw in rembg's HTTP server: a
`model_path` parameter accepted from the request let an attacker point the loader at arbitrary files
and read them back through error messages.

The rule that prevents the same class of bug here:

- Models are chosen from a **fixed registry** in `config.py`, by key. The request sends
  `"birefnet-general"`, never a path.
- No endpoint accepts a filesystem path, a filename, or a model URL from the client.
- Uploads are written to a generated name in a temp directory — never the client-supplied filename.
- Any path built from input is resolved and checked to be inside its expected root before use.

We do not run rembg's bundled HTTP server. We import the library. Still pin `rembg>=2.0.81`.

## 4 · Input validation

Every upload is hostile until proven otherwise — even from a known client, because it arrived over
WhatsApp.

- Validate **content type and magic bytes**, not the file extension.
- Cap file size and pixel dimensions. A decompression bomb will take down a 12 GB machine.
  Set `Image.MAX_IMAGE_PIXELS` deliberately rather than leaving Pillow's default.
- Excel: `openpyxl` with formulas not evaluated. Treat cell contents as text, never as anything
  executable.
- Validate every request body with Pydantic. No hand-rolled dict parsing.
- Reject rather than sanitise when the input is malformed. A clear error beats a silent guess.

## 5 · Client data

Client artwork and catalogues are confidential business material.

- Features 1–4 make **no network calls**. This is a feature, and worth telling clients.
- Feature 5 sends images and prompts to Google. The operator should know which client work is
  acceptable to send off-machine, and the UI must make it obvious when a job leaves the building.
- Temp files are cleaned up after each job — client artwork should not accumulate in a temp folder.
- Job history stores metadata and paths, not copies of client files.

## 6 · Secrets in git

Before `git init`, `.gitignore` must already cover:

```
.env
models/
frontend/dist/
*.db
__pycache__/
```

- Scan with `gitleaks` before the first push and before any push that touches config.
- If a key is ever committed: **rotate it first**, then clean history. Rotation is the fix; history
  rewriting is cleanup.

## 7 · Dependencies

- `uv.lock` is committed. Installs are reproducible and pinned.
- Do not add a dependency without asking — see [CLAUDE.md](CLAUDE.md).
- Model weights come only from the sources listed in [LICENSES.md](LICENSES.md).
- Check advisories before bumping anything that loads a model from disk.

## Explicitly out of scope

Not because they are unimportant, but because they do not apply to a single-user localhost tool, and
pretending otherwise would be security theatre:

- User authentication and authorisation
- Rate limiting (one operator, one machine)
- Row-level security
- Audit logging beyond the job history the operator actually uses

**If the app ever becomes multi-user or network-accessible, every one of these becomes required and
this document must be rewritten first.**
