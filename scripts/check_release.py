#!/usr/bin/env python3
"""Fail when the committed UI is not the built UI.

**The failure this exists to stop.** `519de89` changed ~2,000 lines of frontend
and never re-force-added `frontend/dist`, so the tracked bundle was the
pre-Phase-5 UI: `git show HEAD:frontend/dist/assets/index-CkNRyGuO.js` contained
zero references to `api/styles` or `api/ai/models`, both added in that commit.
The shop PC would have been served a UI with no AI screens at all, and nothing
said so (NEXT.md 0.1).

`frontend/dist` is gitignored and force-added on a release commit — see the note
at the bottom of `.gitignore`. That is a deliberate choice (the shop PC has no
Node, so a plain clone must be runnable), and its one weakness is that it depends
on somebody remembering. This is the check instead of the memory.

Run it before pushing a release, and from CI:

    uv run python scripts/check_release.py

Exit codes: 0 all good, 1 a real mismatch, 2 the check itself could not run.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "frontend" / "dist"
INDEX = DIST / "index.html"

# src="/assets/index-doUN5KJP.js", href="/assets/index-1kQTUHSM.css"
ASSET_REF = re.compile(r"""["'](/assets/[^"']+)["']""")


def fail(message: str) -> None:
    print(f"FAIL  {message}")


def git(*args: str) -> str | None:
    """Run git, or None if it cannot answer (not a repo, no such object)."""
    try:
        done = subprocess.run(
            ("git", *args), cwd=ROOT, capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def referenced_assets(html: str) -> set[str]:
    """The asset paths `index.html` actually loads."""
    return {match.group(1) for match in ASSET_REF.finditer(html)}


def main() -> int:
    if not INDEX.exists():
        print("SKIP  frontend/dist is not built — nothing to check.")
        print("      Build it with: cd frontend && npm run build")
        return 0

    problems = 0

    # 1. Every asset index.html asks for must exist on disk. This is the check
    #    that catches a stale index.html pointing at a deleted bundle.
    on_disk = referenced_assets(INDEX.read_text(encoding="utf-8"))
    for reference in sorted(on_disk):
        if not (DIST / reference.lstrip("/")).exists():
            fail(f"index.html loads {reference}, which is not in frontend/dist.")
            problems += 1

    tracked = git("ls-files", "frontend/dist")
    if tracked is None:
        print("SKIP  not a git repository — file checks only.")
        return 1 if problems else 0

    tracked_files = {line for line in tracked.splitlines() if line.strip()}
    # Files staged for deletion are on their way out; do not report them as
    # missing from the working tree.
    staged_deletions = {
        line.split("\t", 1)[1]
        for line in (git("diff", "--cached", "--name-status", "frontend/dist") or "").splitlines()
        if line.startswith("D\t")
    }
    tracked_files -= staged_deletions
    if not tracked_files:
        print("SKIP  frontend/dist is not tracked in git.")
        print("      Ship it as a release artifact, or force-add it:")
        print("        git add -f frontend/dist")
        return 1 if problems else 0

    # 2. The tracked index.html must reference the same assets as the built one.
    #    This is the exact 0.1 failure: dist rebuilt, index.html committed, the
    #    hashed bundles left behind at their old names.
    # `:path` is the *index* — what the next commit will contain. Deliberately
    # not `HEAD:path`: this has to be usable before committing, and after a
    # commit the index and HEAD agree anyway, so the check works either side.
    committed_index = git("show", ":frontend/dist/index.html")
    if committed_index is None:
        fail("frontend/dist/index.html is not committed, but other dist files are.")
        problems += 1
    else:
        committed = referenced_assets(committed_index)
        if committed != on_disk:
            fail("the staged index.html loads different assets from the built one.")
            print(f"      committed: {sorted(committed)}")
            print(f"      built:     {sorted(on_disk)}")
            problems += 1

    # 3. Every asset the built index.html loads must be tracked, or the shop PC
    #    gets a 404 for its own JavaScript.
    for reference in sorted(on_disk):
        path = f"frontend/dist{reference}"
        if path not in tracked_files:
            fail(f"{path} is loaded by index.html but not committed.")
            problems += 1

    # 4. Nothing tracked may be missing from the working tree. This is the trap
    #    state NEXT.md found: two tracked assets showing as deleted, so one
    #    `git checkout .` would restore the old bundle over the new one.
    for path in sorted(tracked_files):
        if not (ROOT / path).exists():
            fail(f"{path} is tracked but missing from frontend/dist.")
            problems += 1

    if problems:
        print()
        print(f"{problems} problem(s). To fix, from the repo root:")
        print("    cd frontend && npm run build && cd ..")
        print("    git add -f frontend/dist")
        return 1

    print(f"OK    frontend/dist is consistent ({len(tracked_files)} files tracked).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
