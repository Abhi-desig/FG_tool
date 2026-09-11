#!/usr/bin/env python3
"""Build the folder the shop PC actually runs, as one downloadable .zip.

**Why this exists.** ARCHITECTURE.md's deployment story is "the shop PC installs
Python and nothing else" — no Node, no build step, no git. But there was nothing
that produced *the thing to copy over*. Handing somebody a repo URL and a list of
commands is not a deliverable for an operator who is not a programmer, and a
hand-assembled folder is how `models/` or a `.env` ends up on a USB stick.

**What it refuses to include**, per CLAUDE.md rule 5 and SECURITY.md:

* `.env` and anything key-shaped — the shop PC gets its own keys typed into
  Settings, encrypted at rest there.
* `*.db` — the operator's own clients, glossary and corrections. Shipping a
  database would overwrite theirs, and shipping *ours* would leak test data.
* `models/` — 1.4 GB of weights that download on first use anyway.
* `tests/`, `frontend/src/`, `node_modules/` — the shop PC cannot run any of it.

`frontend/dist` **is** included and is the whole reason this is not just a git
clone: it is the built UI, and without it the app serves a 503 stub.

**It refuses to package anything the gate rejects.** `scripts/qc.py` runs first
and a failure stops the build. A zip is the one artefact nobody re-runs anything
against — it goes to a machine with no tests, no Node and no git, and whatever
is wrong in it stays wrong until a client sees it.

    uv run python scripts/package_windows.py

Exit codes: 0 built, 1 the gate failed or the UI is not built, 2 the script
could not run from here.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "dist"
NAME = "focus-toolkit-shop"

# Copied wholesale. Anything not named here does not travel.
#
# `data` entire, not `data/maps`. It used to be the one subfolder, and every
# asset added since was silently left behind: the 1.7 MB Olam word library, the
# name lexicon, the place gazetteer, and all nine poster styles. A shop PC
# unzipping that package got a poster screen reading "No poster styles yet" and
# a translator with no offline dictionary — most of three phases of work,
# missing, with nothing to say so. The whole folder is 1.7 MB, and naming the
# folder rather than its children means the next asset travels by default
# instead of by memory.
TREES = ("backend", "data", "frontend/dist")
FILES = (
    "pyproject.toml",
    "uv.lock",
    # `start.bat` is deliberately not here. The package ships
    # START-FOCUS-TOOLKIT.bat instead, and two launchers in one folder is one
    # launcher too many for somebody being told over the phone what to click.
    "check-ai.bat",
    "README.md",
    "QC.md",
    "SETTINGS.md",
    "LICENSES.md",
)

# Never packaged, at any depth. Belt and braces over the allow-list above.
BANNED_NAMES = {".env", "data.db", "data.db-shm", "data.db-wal"}
BANNED_DIRS = {"__pycache__", "node_modules", ".git", "models", "tests", ".venv"}
BANNED_SUFFIXES = {".db", ".pyc", ".log"}


def refused(path: Path) -> bool:
    if path.name in BANNED_NAMES or path.suffix in BANNED_SUFFIXES:
        return True
    return any(part in BANNED_DIRS for part in path.parts)


START_HERE = """FOCUS TOOLKIT — read this first
===============================

You only do steps 1, 2 and 3 once, ever. After that it is one
double-click to start.


1. PUT THIS FOLDER SOMEWHERE SENSIBLE
-------------------------------------
Unzip this to a normal folder on the C: drive. This works well:

    C:\\FocusToolkit

Do NOT leave it inside the Downloads folder, and do NOT run it from
inside the zip file. Windows makes a read-only copy in both cases and
the toolkit cannot save anything.

Do not put it on the Desktop or in OneDrive either — OneDrive tries to
sync it and the toolkit slows to a crawl.


2. INSTALL PYTHON'S INSTALLER (called "uv")
-------------------------------------------
Double-click:  install-uv.bat

A black window opens and downloads about 30 MB. When it says it is
finished, close it. If Windows asks whether to allow it, say yes.

If Windows says "Windows protected your PC", click "More info", then
"Run anyway". That message appears for anything not bought from the
Microsoft Store.

YOU DO NOT NEED TO INSTALL PYTHON YOURSELF. uv brings its own. If
something tells you Python is missing, or you see "python is not
recognised as an internal or external command", it means step 2 did
not finish — run install-uv.bat again and watch for a red error.


3. INSTALL THE TOOLKIT
----------------------
Double-click:  first-time-setup.bat

This downloads what the toolkit needs. It takes a while the first time —
around 10 to 20 minutes on a normal connection, and about 4 GB. Leave it
running. It only happens once.


4. START IT
-----------
Double-click:  START-FOCUS-TOOLKIT.bat

A black window opens, then your web browser opens with the toolkit in
it. That is the app.

Make it easier next time: right-click START-FOCUS-TOOLKIT.bat, choose
"Send to", then "Desktop (create shortcut)". Now it is an icon on your
desktop.

If the browser does not open on its own, open it yourself and type this
into the address bar:

    localhost:8000


TO CLOSE IT
-----------
Close the black window. Closing only the browser leaves it running in
the background.


TWO THINGS THAT LOOK WRONG BUT ARE NOT
--------------------------------------
* REMOVING A BACKGROUND OR ENLARGING A PICTURE TAKES 2 TO 3 MINUTES
  PER IMAGE. That is normal. It is doing real work on this computer
  rather than sending your client's photo to a website. The screen
  shows which step it is on and how long it has been going. Do not
  close it — and the very first time you use each of those two tools
  it also downloads what it needs, so that one is slower still.

* THE RUPEE FIGURE ON SCREEN IS AN ESTIMATE, NOT A BILL. The AI
  features show roughly what they have cost this month. It is close,
  but the real number is the one on Google's own billing page. Treat
  the figure here as a warning light, not an invoice.


IF THE AI FEATURES DO NOT WORK
------------------------------
Double-click:  check-ai.bat

It tells you whether the problem is this computer or the API key. It
costs nothing to run.


THINGS WORTH KNOWING
--------------------
* Nothing leaves this computer except the features that say so on
  screen before they run: the Malayalam check in the Excel translator,
  and the poster screen. Everything else works with the internet
  unplugged.

* Your clients, your glossary and your remembered corrections are stored
  in a file called data.db next to this one. It is created the first time
  you run the app. Copy that file if you ever move to another computer.

* API keys are typed into the Settings screen, not into any file here.

* Malayalam written into a poster picture by the AI is often misspelled.
  Read every word on a poster against what you typed before printing it.

* VERSION.txt says exactly which build this is. If you report a problem,
  send that file with it.
"""

INSTALL_UV = """@echo off
REM Installs uv, which is what runs Python for this toolkit.
REM You only need to do this once on this computer.
echo Installing uv. This downloads about 30 MB.
echo.
powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
echo.
echo If that finished without a red error, you are done with this step.
echo Next: double-click first-time-setup.bat
echo.
pause
"""

FIRST_RUN = """@echo off
REM Downloads everything the toolkit needs. Once only, and it takes a while.
cd /d "%~dp0"
echo Setting up the Focus Toolkit.
echo This takes 10-20 minutes the first time and downloads about 4 GB.
echo Leave this window open until it says it is done.
echo.
uv sync --extra images --extra translate --extra ai
if errorlevel 1 (
  echo.
  echo Setup did not finish. Check the internet connection and run this again.
  pause
  exit /b 1
)
echo.
echo Done. From now on, just double-click START-FOCUS-TOOLKIT.bat
echo.
pause
"""


START_APP = """@echo off
REM Focus Toolkit - double-click this to start.
REM Make it a desktop icon: right-click this file, Send to, Desktop (create shortcut).
cd /d "%~dp0"
title Focus Toolkit - keep this window open
echo Starting the Focus Toolkit.
echo Your browser will open in a few seconds.
echo.
echo KEEP THIS BLACK WINDOW OPEN while you use the toolkit.
echo Closing it stops the toolkit.
echo.
REM The server opens the browser itself once it is actually listening, which is
REM the only moment that is not too early. See OPEN_BROWSER in backend/config.py.
uv run python -m backend.main
if errorlevel 1 (
  echo.
  echo The toolkit could not start.
  echo If this is the first time, run first-time-setup.bat and try again.
  echo If it still fails, run check-ai.bat and read README-FIRST.txt.
  pause
)
"""


def version_text() -> str:
    """What is in this zip, so a bug report can name a build.

    The operator will never read this. It is for whoever is asked "which
    version is on the shop PC?" three months from now, when the answer decides
    whether a bug is already fixed.
    """
    commit = git("rev-parse", "HEAD") or "unknown"
    described = git("describe", "--always", "--dirty") or ""
    branch = git("rev-parse", "--abbrev-ref", "HEAD") or "unknown"
    built = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    dirty = "-dirty" in described
    lines = [
        "Focus Toolkit — shop package",
        "",
        f"Commit: {commit.strip()}",
        f"Branch: {branch.strip()}",
        f"Built:  {built}",
    ]
    if dirty:
        lines += [
            "",
            "WARNING: built from a working tree with uncommitted changes, so",
            "the commit above does not fully describe what is in this zip.",
        ]
    return "\n".join(lines) + "\n"


def git(*args: str) -> str | None:
    """Run git, or None if it cannot answer. A zip must still build without it."""
    try:
        done = subprocess.run(
            ("git", *args), cwd=ROOT, capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def build() -> int:
    index = ROOT / "frontend" / "dist" / "index.html"
    if not index.exists():
        print("FAIL  frontend/dist is not built — the package would serve a blank app.")
        print("      cd frontend && npm run build")
        return 1

    # The whole gate, not just the release check. A zip is the one artefact
    # nobody re-runs anything against: it is copied to a machine with no tests,
    # no Node and no git, and whatever is wrong in it stays wrong until a client
    # sees it. `qc.py` includes `check_release.py`, so the stale-bundle check
    # this used to run on its own is still here, with the other four gates
    # around it.
    #
    # Skips do not block. On this machine the gate is green; on a machine
    # without the models a skip means a test could not run, not that something
    # is broken, and refusing to package for that would make the packager
    # unusable exactly where it is most needed. `qc.py` exits 0 for skips alone
    # and 1 for any real failure, which is the line this wants.
    print("...   running the full gate (this takes a couple of minutes)")
    gate = subprocess.run(
        ("uv", "run", "python", str(ROOT / "scripts" / "qc.py")),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if gate.returncode != 0:
        print("FAIL  the gate did not pass — not packaging that.")
        print(gate.stdout.rstrip()[-3000:])
        return 1
    for line in gate.stdout.splitlines():
        if line.startswith(("PASS ", "SKIP ", "FAIL ")) or line.startswith("5 gates"):
            print(f"      {line}")

    staging = OUT_DIR / NAME
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    copied = 0
    for tree in TREES:
        source = ROOT / tree
        if not source.exists():
            print(f"FAIL  {tree} is missing.")
            return 1
        for path in source.rglob("*"):
            if not path.is_file() or refused(path.relative_to(ROOT)):
                continue
            target = staging / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied += 1

    for name in FILES:
        source = ROOT / name
        if source.exists():
            shutil.copy2(source, staging / name)
            copied += 1

    (staging / "README-FIRST.txt").write_text(START_HERE, encoding="utf-8")
    (staging / "install-uv.bat").write_text(INSTALL_UV, encoding="utf-8")
    (staging / "first-time-setup.bat").write_text(FIRST_RUN, encoding="utf-8")
    (staging / "START-FOCUS-TOOLKIT.bat").write_text(START_APP, encoding="utf-8")
    (staging / "VERSION.txt").write_text(version_text(), encoding="utf-8")
    copied += 5

    # Last line of defence: assert nothing forbidden reached the staging folder
    # before it is sealed into a zip somebody will email.
    for path in staging.rglob("*"):
        if path.is_file() and refused(path.relative_to(staging)):
            print(f"FAIL  {path.relative_to(staging)} must never be packaged.")
            return 1

    archive = OUT_DIR / f"{NAME}.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(OUT_DIR))

    size_mb = archive.stat().st_size / 1_000_000
    print(f"OK    {archive.relative_to(ROOT)} — {copied} files, {size_mb:.1f} MB")
    print("      Copy it to the shop PC, unzip it, and open README-FIRST.txt.")
    return 0


def main() -> int:
    if not (ROOT / "pyproject.toml").exists():
        print("FAIL  run this from the repo.")
        return 2
    return build()


if __name__ == "__main__":
    sys.exit(main())
