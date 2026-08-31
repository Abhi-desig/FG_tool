"""Tests for the QC gate.

`scripts/qc.py` exists to stop a skipped check reading as a passed one. A gate
that lies about its own honesty rule is worse than no gate, so the rule itself
is what these test — not that the subprocesses work, which is their own job.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "qc.py"


def _load():
    spec = importlib.util.spec_from_file_location("qc", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before executing: `@dataclass` resolves annotations through
    # `sys.modules[cls.__module__]`, which is not there yet for a hand-loaded
    # script and fails with a bare AttributeError.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


qc = _load()


def _result(name: str, state: str, detail: str = "") -> object:
    gate = next((g for g in qc.GATES if g.name == name), qc.GATES[0])
    return qc.Result(gate=gate, state=state, seconds=0.1, detail=detail)


def test_the_script_exists_and_is_runnable() -> None:
    assert SCRIPT.exists()
    assert qc.main is not None
    assert len(qc.GATES) == 5


def test_a_skipped_gate_is_never_reported_as_a_pass(capsys: pytest.CaptureFixture) -> None:
    """The whole reason this script exists.

    Four of the five gates do nothing on an incomplete checkout, and each of
    them exits 0 while doing it.
    """
    results = [
        _result("ruff", qc.PASS),
        _result("tsc", qc.SKIP, "frontend/node_modules missing"),
    ]
    code = qc.verdict(results, strict=False)
    out = capsys.readouterr().out

    assert code == 0, "a skip alone is not a failure without --strict"
    assert "NOT a full pass" in out
    assert "Every gate ran and passed" not in out
    assert "node_modules" in out, "the fix must be named, not just the fact"


def test_everything_passing_says_so_plainly(capsys: pytest.CaptureFixture) -> None:
    code = qc.verdict([_result("ruff", qc.PASS), _result("pytest", qc.PASS)], strict=False)
    assert code == 0
    assert "Every gate ran and passed" in capsys.readouterr().out


def test_strict_mode_fails_on_a_skip(capsys: pytest.CaptureFixture) -> None:
    """Before a release, "it did not run" is not good enough."""
    results = [_result("ruff", qc.PASS), _result("release", qc.SKIP, "dist not built")]
    assert qc.verdict(results, strict=True) == 1
    assert "NOT a full pass" in capsys.readouterr().out


def test_a_failing_gate_sets_the_exit_code(capsys: pytest.CaptureFixture) -> None:
    results = [_result("ruff", qc.PASS), _result("pytest", qc.FAIL, "1 failed, 2 passed")]
    assert qc.verdict(results, strict=False) == 1
    assert "NOT a pass" in capsys.readouterr().out


def test_a_failure_outranks_a_skip(capsys: pytest.CaptureFixture) -> None:
    """Both present: the operator must be told about the failure first."""
    results = [_result("pytest", qc.FAIL, "1 failed"), _result("tsc", qc.SKIP, "no node")]
    assert qc.verdict(results, strict=False) == 1
    assert "NOT a pass —" in capsys.readouterr().out


def test_skips_inside_the_suite_are_surfaced(capsys: pytest.CaptureFixture) -> None:
    """A green pytest row over 24 skipped model tests has proved less than it looks."""
    qc.verdict([_result("pytest", qc.PASS, "312 passed, 24 skipped")], strict=False)
    out = capsys.readouterr().out
    assert "24 test(s) were skipped inside the suite" in out
    assert "uv run pytest -q -rs" in out


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ("312 passed, 24 skipped in 8.10s", "312 passed, 24 skipped"),
        ("312 passed, 24 skipped, 3 warnings in 8.10s", "312 passed, 24 skipped"),
        ("1 failed, 2 passed in 0.50s", "1 failed, 2 passed"),
        ("no tests ran in 0.01s", "no tests ran"),
        ("41 passed in 1.20s", "41 passed"),
        ("2 errors in 0.30s", "2 errors"),
        # The summary is the last line; earlier chatter must not win.
        ("collecting ...\n9 passed in 0.10s", "9 passed"),
    ],
)
def test_the_pytest_summary_is_read_rather_than_guessed(stdout: str, expected: str) -> None:
    assert qc.pytest_detail(stdout) == expected


def test_an_unreadable_pytest_summary_prints_the_raw_line_rather_than_a_number() -> None:
    """Inventing a count here would be the dishonesty the script exists to stop."""
    assert qc.pytest_detail("INTERNALERROR> something broke") == (
        "INTERNALERROR> something broke"
    )
    assert qc.pytest_detail("") == "no output"


def test_the_default_run_never_writes_a_file() -> None:
    """`--fix` is opt-in. A gate that edits the tree while reporting on it is not a gate."""
    for gate in qc.GATES:
        assert "--fix" not in gate.argv, f"{gate.name} would modify the working tree"


def test_a_gate_needing_node_is_skipped_rather_than_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shop PC has no Node at all by design — that is not a failing build."""
    monkeypatch.setattr(qc, "NODE_MODULES", ROOT / "does" / "not" / "exist")
    tsc = next(g for g in qc.GATES if g.name == "tsc")
    ruff = next(g for g in qc.GATES if g.name == "ruff")
    assert "npm install" in (qc.skip_reason(tsc, None) or "")
    assert qc.skip_reason(ruff, None) is None


def test_only_skips_every_other_gate() -> None:
    for gate in qc.GATES:
        reason = qc.skip_reason(gate, "pytest")
        if gate.name == "pytest":
            assert reason is None
        else:
            assert reason is not None and "not selected" in reason


def test_every_gate_says_why_it_matters() -> None:
    """An operator reading a FAIL row needs to know what it was protecting."""
    for gate in qc.GATES:
        assert gate.why.strip(), f"{gate.name} has no stated purpose"


def test_pytests_own_skip_lines_do_not_mark_the_gate_skipped() -> None:
    """The first run of this script got this wrong, in the direction it forbids.

    `pytest -rs` prints `SKIPPED [1] tests/test_fonts.py:171: payyans not
    installed` for every skipped test. Read as the release check's `SKIP  …`
    convention, that turned a suite of 400 passing tests into one SKIP row.
    """
    pytest_gate = next(g for g in qc.GATES if g.name == "pytest")
    release_gate = next(g for g in qc.GATES if g.name == "release")
    assert not pytest_gate.reports_own_skip
    assert release_gate.reports_own_skip
