#!/usr/bin/env python3
"""Run every check this project has, and say honestly which ones did not run.

**The failure this exists to stop.** There are five gates — ruff, pytest, tsc,
oxlint and the release check — and no CI to run them. They are run by hand, from
memory, which means in practice they are run one at a time and the other four
drift. Worse, four of the five *silently do nothing* on an incomplete checkout:
`tsc` and `oxlint` need `frontend/node_modules`, `check_release.py` returns 0
when `frontend/dist` is unbuilt, and a large part of the test suite is skipped
without the translation and upscaler models. A green line from any of those is
easy to read as a pass when nothing was checked at all.

So a skip here is a third state, never folded into a pass, and the last line
refuses to say "all good" over one. That is DESIGN.md's second principle applied
to the toolchain: never lie, never hedge.

    uv run python scripts/qc.py            # the gate
    uv run python scripts/qc.py --fix      # let ruff and oxlint fix what they can
    uv run python scripts/qc.py --strict   # a skip is a failure; also runs diagnose
    uv run python scripts/qc.py --only pytest

Exit codes: 0 everything that ran passed and nothing was skipped (or skips were
allowed), 1 a gate failed or --strict saw a skip, 2 the runner itself could not
run.

Deliberately not here: `ruff format --check` (there is no [tool.ruff.format]
config, so it would reformat the whole codebase on first run), a coverage gate
(CLAUDE.md: do not chase coverage on glue code), and `npm run build` (`tsc -b`
catches the type errors, and the full Vite build belongs to the release step
that `check_release.py` already guards).
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
NODE_MODULES = FRONTEND / "node_modules"

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


@dataclass(frozen=True)
class Gate:
    """One check, and what it is worth.

    `needs_node` is separated out rather than probed inside `run` because a
    missing `node_modules` is not a failure — the shop PC has no Node at all by
    design (ARCHITECTURE.md), so on that machine two of these five can never
    run and saying so is the correct output.
    """

    name: str
    argv: tuple[str, ...]
    cwd: Path
    why: str
    needs_node: bool = False
    # `check_release.py` exits 0 while printing its own `SKIP  …` line when
    # there is nothing to check. Only that gate uses the convention, and the
    # flag has to be opt-in: pytest's `-rs` prints `SKIPPED [1] …` for every
    # skipped test, which read as a skipped *gate* and hid a whole passing
    # suite behind one SKIP row the first time this script was run.
    reports_own_skip: bool = False


GATES: tuple[Gate, ...] = (
    Gate(
        name="ruff",
        argv=("uv", "run", "ruff", "check", "."),
        cwd=ROOT,
        why="Python lint and import order.",
    ),
    Gate(
        name="pytest",
        # -rs lists the skip reasons, which is the whole point of this row.
        argv=("uv", "run", "pytest", "-q", "-rs"),
        cwd=ROOT,
        why="The test suite. Read the skip count, not just the colour.",
    ),
    Gate(
        name="tsc",
        argv=("npx", "tsc", "-b"),
        cwd=FRONTEND,
        why="TypeScript. `no any` is only enforced here.",
        needs_node=True,
    ),
    Gate(
        name="oxlint",
        argv=("npm", "run", "lint"),
        cwd=FRONTEND,
        why="React rules-of-hooks and unused code.",
        needs_node=True,
    ),
    Gate(
        name="release",
        argv=("uv", "run", "python", "scripts/check_release.py"),
        cwd=ROOT,
        why="The committed UI is the built UI.",
        reports_own_skip=True,
    ),
)

GATE_NAMES = tuple(gate.name for gate in GATES)


@dataclass
class Result:
    gate: Gate
    state: str
    seconds: float
    detail: str
    output: str = ""


# "312 passed, 24 skipped, 3 warnings in 8.10s"
_COUNT = re.compile(r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed)")
_REPORTED = ("failed", "error", "errors", "passed", "skipped")


def pytest_detail(stdout: str) -> str:
    """Read pytest's own summary line rather than guessing at it.

    A large part of this suite is guarded by `needs_engine`, `needs_upscaler`
    and `importorskip`, so "passed" on its own is not the story — a fresh
    checkout can pass while skipping every test that touches a model. If the
    summary cannot be parsed, the raw last line is printed instead; inventing a
    number here would be the exact dishonesty this script exists to prevent.
    """
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    for line in reversed(lines):
        if "no tests ran" in line:
            return "no tests ran"
        counts = _COUNT.findall(line)
        parts = [f"{n} {word}" for n, word in counts if word in _REPORTED]
        if parts:
            return ", ".join(parts)
    return lines[-1][:80] if lines else "no output"


def skipped_in_pytest(detail: str) -> int:
    """How many tests pytest itself skipped, for the note under the table."""
    found = re.search(r"(\d+)\s+skipped", detail)
    return int(found.group(1)) if found else 0


def skip_reason(gate: Gate, only: str | None) -> str | None:
    """Why this gate will not run, or None if it will."""
    if only is not None and gate.name != only:
        return f"not selected (--only {only})"
    if gate.needs_node and not NODE_MODULES.is_dir():
        return "frontend/node_modules missing — cd frontend && npm install"
    return None


def run(gate: Gate) -> Result:
    started = time.monotonic()
    try:
        done = subprocess.run(
            gate.argv, cwd=gate.cwd, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        return Result(gate, FAIL, time.monotonic() - started, f"could not start: {exc}")

    seconds = time.monotonic() - started
    output = (done.stdout or "") + (done.stderr or "")

    if gate.name == "pytest":
        detail = pytest_detail(done.stdout or output)
    else:
        detail = ""

    if done.returncode != 0:
        return Result(gate, FAIL, seconds, detail or f"exit {done.returncode}", output)

    # A gate that did not run, not a gate that passed. See `reports_own_skip`.
    if gate.reports_own_skip:
        own = [line for line in output.splitlines() if line.startswith("SKIP ")]
        if own:
            return Result(gate, SKIP, seconds, own[0][4:].strip(), output)

    return Result(gate, PASS, seconds, detail, output)


def table(results: list[Result]) -> str:
    width = max(len(r.gate.name) for r in results)
    rows = []
    for result in results:
        timing = f"({result.seconds:.1f}s)" if result.state != SKIP else ""
        detail = f" — {result.detail}" if result.detail else ""
        rows.append(f"{result.state:<5} {result.gate.name:<{width}}{detail} {timing}".rstrip())
    return "\n".join(rows)


def verdict(results: list[Result], strict: bool) -> int:
    """The last line, and the exit code. Never says "all good" over a skip."""
    passed = [r for r in results if r.state == PASS]
    failed = [r for r in results if r.state == FAIL]
    skipped = [r for r in results if r.state == SKIP]

    print()
    print(
        f"{len(results)} gates: {len(passed)} passed, "
        f"{len(skipped)} skipped, {len(failed)} failed."
    )

    for result in results:
        if result.gate.name == "pytest" and result.state == PASS:
            count = skipped_in_pytest(result.detail)
            if count:
                print(
                    f"      {count} test(s) were skipped inside the suite — "
                    "a model-backed test that never ran has proved nothing."
                )
                print("      See them with: uv run pytest -q -rs")

    if failed:
        print(f"NOT a pass — {len(failed)} gate(s) failed. Output above.")
        return 1
    if skipped:
        names = ", ".join(f"{r.gate.name}: {r.detail}" for r in skipped)
        print(f"NOT a full pass — {len(skipped)} gate(s) did not run ({names}).")
        return 1 if strict else 0
    print("Every gate ran and passed.")
    return 0


def fix(quiet: bool) -> None:
    """Let the two linters repair what they can, then show what moved.

    Never on the default path: a gate that edits the working tree while
    reporting on it is not a gate.
    """
    for argv, cwd in (
        (("uv", "run", "ruff", "check", "--fix", "."), ROOT),
        (("npx", "oxlint", "--fix"), FRONTEND),
    ):
        if cwd is FRONTEND and not NODE_MODULES.is_dir():
            continue
        subprocess.run(argv, cwd=cwd, capture_output=quiet, text=True, check=False)
    changed = subprocess.run(
        ("git", "diff", "--stat"), cwd=ROOT, capture_output=True, text=True, check=False
    )
    print("--fix changed:")
    print(changed.stdout.rstrip() or "      nothing")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fix", action="store_true", help="let ruff and oxlint fix first")
    parser.add_argument("--strict", action="store_true", help="a skipped gate is a failure")
    parser.add_argument("--only", choices=GATE_NAMES, help="run one gate")
    parser.add_argument("--quiet", action="store_true", help="table only unless something fails")
    args = parser.parse_args(argv)

    if shutil.which("uv") is None:
        print("FAIL  uv is not on PATH. This project is run through uv — see README.md.")
        return 2
    if not (ROOT / "scripts" / "check_release.py").exists():
        print("FAIL  scripts/ is not where this script expects it. Run from the repo.")
        return 2

    if args.fix:
        fix(args.quiet)

    results: list[Result] = []
    for gate in GATES:
        reason = skip_reason(gate, args.only)
        if reason is not None:
            results.append(Result(gate, SKIP, 0.0, reason))
            continue
        if not args.quiet:
            print(f"...   {gate.name}", flush=True)
        results.append(run(gate))

    if args.strict and args.only is None:
        diagnose = Gate(
            name="diagnose",
            argv=("uv", "run", "python", "-m", "backend.diagnose"),
            cwd=ROOT,
            why="The machine itself: fonts, database, encryption, the API key.",
        )
        if not args.quiet:
            print(f"...   {diagnose.name}", flush=True)
        results.append(run(diagnose))

    print()
    print(table(results))

    for result in results:
        if result.state == FAIL and result.output:
            print()
            print(f"--- {result.gate.name} ---")
            print(result.output.rstrip())

    return verdict(results, args.strict)


if __name__ == "__main__":
    sys.exit(main())
