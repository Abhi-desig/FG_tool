# CLAUDE.md — Standing orders

Read on every request. Companion docs: [PRD](PRD.md) · [ARCHITECTURE](ARCHITECTURE.md) ·
[SECURITY](SECURITY.md) · [DESIGN](DESIGN.md) · [ROADMAP](ROADMAP.md) · [LICENSES](LICENSES.md) ·
[SETTINGS](SETTINGS.md) · [DECISIONS](DECISIONS.md) · [QC](QC.md) ·
[POSTER_LAYOUTS](POSTER_LAYOUTS.md)

---

## What this is

A local pre-press tool for **one** Kerala print shop. Single operator, localhost only, no cloud.
The operator is not a programmer. Output is sold to clients, so errors are expensive and visible.

## Hard rules

1. **Do not add a dependency without asking.** Every package is disk, install risk, and licence
   risk on a 112 GB SSD. Justify it against what is already installed.
2. **Do not write a UI component that shadcn/ui already provides.** Check first, then compose.
   Writing a custom modal, toast, or dropdown is a bug, not a feature.
3. **`MODEL = "birefnet-general"` never changes.** `bria-rmbg` requires a paid commercial licence.
   See [LICENSES.md](LICENSES.md).
4. **Never copy code from Upscayl** (AGPL-3.0). Model weights are fine; source is not.
5. **Never commit `.env`, `models/`, or `*.db`.**
6. **Respect phase order.** Do not build Phase N+1 while Phase N has not passed its exit gate in
   [ROADMAP.md](ROADMAP.md). A half-finished Phase 2 is worse than no Phase 2.
7. **Load one model at a time, and free it after use.** The shop PC has 12 GB. See the memory rule
   in [ARCHITECTURE.md](ARCHITECTURE.md).
8. **Server binds to `127.0.0.1` only.** Never `0.0.0.0`.

## Conventions

**Python**
- Type-hint every function signature. `from __future__ import annotations` where useful.
- No bare `except:`. Catch the specific exception; if you must catch broadly, log it.
- Pydantic models for every request and response body.
- Feature modules never import each other. Shared logic goes in `config.py` or a new helper.
- Paths via `pathlib.Path`, never string concatenation.

**TypeScript / React**
- `strict: true`. **No `any`** — use `unknown` and narrow.
- Function components with hooks. No class components.
- Server state through one fetch layer; do not scatter `fetch` calls through components.
- Tailwind utilities for styling. No CSS-in-JS, no separate stylesheets except tokens.

**General**
- Match surrounding code. Comment density, naming, and idiom should be indistinguishable.
- Comment *why*, not *what*.
- Small commits, present tense: `add ML-TTKarthika map loader`.

## Deny list

Never run without explicit per-instance approval:

```
rm -rf                git push --force        git reset --hard
git commit --no-verify / -n                   git clean -fd
uv pip install --system                       any command touching models/ destructively
```

## Testing

- **Phase 1 requires golden tests.** Malayalam conversion is verified against
  `tests/golden/malayalam_pairs.tsv`, not by eye.
- Other phases: test the logic that would silently produce wrong output — the DPI calculator, the
  glossary substitution, the layout renderer. Do not chase coverage on glue code.
- A passing test is not the exit gate. See [ROADMAP.md](ROADMAP.md) — most gates require checking
  real output in CorelDRAW or on a real print.
- Before handing work over, run the whole gate in one command:

  ```
  uv run python scripts/qc.py
  ```

  It runs ruff, pytest, tsc, oxlint and the release check, and reports a **skip** as a third
  state rather than folding it into a pass. Then walk [QC.md](QC.md) in the running app —
  the things a machine cannot check.

## When unsure

State the assumption and continue; do not stall. But **ask before**: adding a dependency, changing
the stack, changing a locked value, spending API credit, or touching anything in the deny list.

## Anti-drift

The stack is settled in [ARCHITECTURE.md](ARCHITECTURE.md) and the reasoning is in
[DECISIONS.md](DECISIONS.md). Do not propose Next.js, an ORM, Docker, a task queue, or a rewrite.
If a change is genuinely warranted, add an ADR — do not just do it.
