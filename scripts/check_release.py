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

**The second failure, and why the checks below were not enough.** Everything
here compared `frontend/dist` against *itself* and against git. None of it
compared the bundle against the **source it was built from**, so a frontend edit
that was never rebuilt left a perfectly self-consistent, perfectly committed,
perfectly stale bundle — and this check said OK. That happened during the
ADR-037 work: a fix was invisible in the browser and the gate stayed green.
`source_hash` closes it, stamped into the bundle at build time by the `postbuild`
script in `frontend/package.json`.

Run it before pushing a release, and from CI:

    uv run python scripts/check_release.py

`--stamp` writes the current source hash into the bundle. The build does this
itself; there is no reason to run it by hand, and running it *without* a build
would certify a stale bundle as fresh.

Exit codes: 0 all good, 1 a real mismatch, 2 the check itself could not run.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
DIST = FRONTEND / "dist"
INDEX = DIST / "index.html"

# Written by `npm run build`, read by this check. Lives inside the bundle so it
# travels with it — into git, into the shop PC zip, anywhere the bundle goes.
SOURCE_STAMP = DIST / "source-hash.txt"

# What the build actually reads. Anything whose change should produce a
# different bundle belongs here; anything else must not, or the hash churns and
# people learn to ignore it.
#
# `package-lock.json` is in deliberately: a dependency bump changes the output
# without touching a line of `src`. `node_modules` and `dist` are out — one is
# derived from the lockfile, the other is the thing being checked.
SOURCE_TREES = ("src", "public")
SOURCE_FILES = (
    "index.html",
    "vite.config.ts",
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "tsconfig.app.json",
    "tsconfig.node.json",
)

# src="/assets/index-doUN5KJP.js", href="/assets/index-1kQTUHSM.css"
ASSET_REF = re.compile(r"""["'](/assets/[^"']+)["']""")


def fail(message: str) -> None:
    print(f"FAIL  {message}")


def shown(path: Path) -> str:
    """A path as short as it can be said, without assuming where it is.

    Repo-relative reads better in output, but these module globals are
    redirected by the tests, and a hard `relative_to` turns a message into a
    `ValueError` the moment the path sits outside the repo.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


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


def source_hash() -> str:
    """One digest over every file the frontend build reads.

    Deterministic across machines and checkouts: paths are sorted, recorded
    relative to `frontend/` in posix form, and hashed with their contents and
    lengths. The path is in the digest as well as the bytes, so renaming a
    component — which changes the bundle — changes the hash even though no
    file's contents moved.

    Read as bytes, not text: a `.woff2` in `public/` is not decodable, and
    normalising line endings would let a CRLF checkout disagree with an LF one
    about a bundle that is in fact identical.
    """
    digest = hashlib.sha256()
    for path in sorted(_source_files(), key=lambda p: p.relative_to(FRONTEND).as_posix()):
        body = path.read_bytes()
        digest.update(path.relative_to(FRONTEND).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(body)).encode("ascii"))
        digest.update(b"\0")
        digest.update(body)
        digest.update(b"\0")
    return digest.hexdigest()


def _source_files() -> list[Path]:
    """Every build input that exists. A missing optional file is not an error."""
    found = [FRONTEND / name for name in SOURCE_FILES]
    for tree in SOURCE_TREES:
        found.extend(p for p in (FRONTEND / tree).rglob("*") if p.is_file())
    return [p for p in found if p.is_file()]


def main() -> int:
    if not INDEX.exists():
        print("SKIP  frontend/dist is not built — nothing to check.")
        print("      Build it with: cd frontend && npm run build")
        return 0

    problems = 0

    # 0. The bundle must have been built from the source that is here now.
    #    Every other check below compares dist against itself or against git,
    #    and all of them pass for a bundle that is simply out of date.
    if not SOURCE_STAMP.exists():
        fail(f"{shown(SOURCE_STAMP)} is missing, so the bundle's age is unknown.")
        print("      Rebuild to stamp it: cd frontend && npm run build")
        problems += 1
    else:
        stamped = SOURCE_STAMP.read_text(encoding="utf-8").strip()
        current = source_hash()
        if stamped != current:
            fail("frontend/dist was built from different source than is here now.")
            print(f"      built from: {stamped[:16] or '(empty)'}")
            print(f"      source now: {current[:16]}")
            print("      A frontend edit has not been rebuilt, so the browser is")
            print("      being served the previous UI.")
            problems += 1

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


def stamp() -> int:
    """Record the current source hash inside the bundle.

    Called by `npm run build`'s `postbuild`, immediately after Vite has written
    `dist` — which it empties first, so the stamp cannot survive from a previous
    build and be mistaken for this one's.
    """
    if not DIST.is_dir():
        print("SKIP  frontend/dist does not exist — nothing to stamp.")
        return 0
    digest = source_hash()
    SOURCE_STAMP.write_text(digest + "\n", encoding="utf-8")
    print(f"OK    stamped {shown(SOURCE_STAMP)} ({digest[:16]}…)")
    return 0


if __name__ == "__main__":
    sys.exit(stamp() if "--stamp" in sys.argv[1:] else main())
