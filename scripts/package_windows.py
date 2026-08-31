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

    uv run python scripts/package_windows.py

Exit codes: 0 built, 1 the UI is not built or not consistent, 2 the script
could not run from here.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "dist"
NAME = "FocusToolkit-windows"

# Copied wholesale. Anything not named here does not travel.
TREES = ("backend", "data/maps", "frontend/dist")
FILES = (
    "pyproject.toml",
    "uv.lock",
    "start.bat",
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


START_HERE = """FOCUS TOOLKIT — how to start it on this computer
================================================

You only do steps 1 and 2 once, ever.


1. INSTALL PYTHON'S INSTALLER (called "uv")
-------------------------------------------
Double-click:  install-uv.bat

A black window opens and downloads about 30 MB. When it says it is
finished, close it. If Windows asks whether to allow it, say yes.


2. INSTALL THE TOOLKIT
----------------------
Double-click:  first-time-setup.bat

This downloads what the toolkit needs. It takes a while the first time —
around 10 to 20 minutes on a normal connection, and about 4 GB. Leave it
running. It only happens once.


3. START IT
-----------
Double-click:  start.bat

Your web browser opens with the toolkit in it. That is the app.

To make it easier next time: right-click start.bat, choose "Send to",
then "Desktop (create shortcut)". Now it is an icon on your desktop.


TO CLOSE IT
-----------
Close the black window. Closing only the browser leaves it running.


IF THE AI FEATURES DO NOT WORK
------------------------------
Double-click:  check-ai.bat

It tells you whether the problem is this computer or the API key. It
costs nothing to run.


THINGS WORTH KNOWING
--------------------
* Nothing leaves this computer except the two features that say so on
  screen before they run: the Malayalam check in the Excel translator,
  and the AI picture and wording on the poster screen. Everything else
  works with the internet unplugged.

* Your clients, your glossary and your remembered corrections are stored
  in a file called data.db next to this one. It is created the first time
  you run the app. Copy that file if you ever move to another computer.

* API keys are typed into the Settings screen, not into any file here.

* The first time you remove a background or enlarge an image, it downloads
  the model it needs. That is a one-off wait per tool.
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
echo Done. From now on, just double-click start.bat
echo.
pause
"""


def build() -> int:
    index = ROOT / "frontend" / "dist" / "index.html"
    if not index.exists():
        print("FAIL  frontend/dist is not built — the package would serve a blank app.")
        print("      cd frontend && npm run build")
        return 1

    # The UI in the package must be the UI in the source tree. This is the same
    # failure `check_release.py` exists to stop (NEXT.md 0.1), one step later:
    # shipping a zip built from a stale bundle.
    check = subprocess.run(
        (sys.executable, str(ROOT / "scripts" / "check_release.py")),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if check.returncode != 0:
        print("FAIL  the built UI and the committed UI disagree — not packaging that.")
        print(check.stdout.rstrip())
        return 1

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

    (staging / "START-HERE.txt").write_text(START_HERE, encoding="utf-8")
    (staging / "install-uv.bat").write_text(INSTALL_UV, encoding="utf-8")
    (staging / "first-time-setup.bat").write_text(FIRST_RUN, encoding="utf-8")
    copied += 3

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
    print("      Copy it to the shop PC, unzip it, and open START-HERE.txt.")
    return 0


def main() -> int:
    if not (ROOT / "pyproject.toml").exists():
        print("FAIL  run this from the repo.")
        return 2
    return build()


if __name__ == "__main__":
    sys.exit(main())
