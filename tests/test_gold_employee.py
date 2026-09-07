"""The acceptance gate for ADR-035, measured on a frozen gold set.

**Flag recall is the metric that has to move.** The failure this change exists
to fix was not that the pipeline produced wrong cells — a 57M model on a member
list always will — but that it produced ~50 of them while flagging 2. A wrong
cell the operator is told about costs a minute. A wrong cell they are not told
about is printed and sent to a client.

**What the gold set can and cannot say.** Every verdict in it is decided by
*evidence* — Latin leaked into the output, a phrase whose meaning was measured
and back-translated during the session, output identical to the input. Anything
needing a judgement about Malayalam is `unknown` and excluded from both metrics,
transliterations included. An earlier draft scored those `ok` by construction
and reported a perfect zero error rate, which measured nothing but its own
assumption. The excluded rows are exactly the ones a Malayalam reader has still
to mark; until they do, these numbers cover the provable subset and no more.
"""

from __future__ import annotations

import pathlib

import pytest

GOLD = pathlib.Path(__file__).parent / "golden" / "employee_gold.tsv"

# The gate from the brief. Flag recall is the share of provably-wrong cells that
# the grid marked — as a problem, a check, or UNRESOLVED.
RECALL_GATE = 0.85


def _rows() -> list[dict[str, str | bool]]:
    out: list[dict[str, str | bool]] = []
    for line in GOLD.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        assert len(parts) == 9, f"malformed gold row: {line[:60]}"
        out.append(
            {
                "cell": parts[0],
                "source": parts[1],
                "before": parts[2],
                "before_verdict": parts[3],
                "before_flagged": parts[4] == "True",
                "after": parts[5],
                "after_verdict": parts[6],
                "after_flagged": parts[7] == "True",
                "basis": parts[8],
            }
        )
    return out


ROWS = _rows()


def _measure(rows: list[dict[str, str | bool]], which: str) -> tuple[int, int, float, float]:
    """(scored, wrong, error rate, flag recall) over the provable subset."""
    scored = [r for r in rows if r[f"{which}_verdict"] in ("ok", "wrong")]
    wrong = [r for r in scored if r[f"{which}_verdict"] == "wrong"]
    caught = [r for r in wrong if r[f"{which}_flagged"]]
    return (
        len(scored),
        len(wrong),
        len(wrong) / len(scored) if scored else 0.0,
        len(caught) / len(wrong) if wrong else 1.0,
    )


def test_the_gold_set_is_big_enough_to_mean_something() -> None:
    assert len(ROWS) >= 120, f"only {len(ROWS)} cells"
    # And it must contain negative cases, or the metric is trivially satisfied.
    assert any(r["before_verdict"] == "wrong" for r in ROWS)


def test_flag_recall_clears_the_gate() -> None:
    """The one number that decides whether this ships."""
    _, wrong, _, recall = _measure(ROWS, "after")
    assert wrong == 0 or recall > RECALL_GATE, (
        f"flag recall {recall:.2f} does not clear {RECALL_GATE}; "
        f"{wrong} provably-wrong cells, "
        f"{sum(1 for r in ROWS if r['after_verdict'] == 'wrong' and not r['after_flagged'])} "
        f"of them unflagged"
    )


def test_flag_recall_improved() -> None:
    """The change has to move the metric, not merely satisfy it.

    Stated as a comparison because a pipeline that translated nothing would
    score a perfect recall while being useless.
    """
    _, before_wrong, _, before_recall = _measure(ROWS, "before")
    _, after_wrong, _, after_recall = _measure(ROWS, "after")
    assert before_wrong > 0, "the before run has no provable errors to improve on"
    assert after_recall >= before_recall, (
        f"recall went backwards: {before_recall:.2f} -> {after_recall:.2f}"
    )


def test_no_name_or_address_cell_reached_the_model() -> None:
    """A hard invariant. `Vishnu Prasad` → വിഷ്ണുപുരാണം is what happens when one
    does, and no confidence check can catch it after the fact."""
    from backend.features import columns, translit

    for source in ("Anjali Menon", "Thoppil Veedu", "Elavunkal Veedu"):
        cls, _, _ = columns.classify(
            "Name" if " " in source and "Veedu" not in source else "House Name",
            [source],
            1,
            translit.known_names(),
            translit.known_places(),
            frozenset(),
        )
        assert cls in ("PERSON_NAME", "ADDRESS"), f"{source} classified {cls}"


def test_transliteration_is_stable_across_runs() -> None:
    """A member list repeats a family's house name down the column. Two
    spellings of it in one file is a defect the operator cannot fix by hand."""
    from backend.features import translit

    for source in ("Elavunkal Veedu", "Thoppil Veedu, Uthimoodu PO", "K.M. Nair"):
        first, unknown_a = translit.person(source)
        translit.reset_cache()
        second, unknown_b = translit.person(source)
        assert first == second, f"{source} spelled two ways"
        assert unknown_a == unknown_b


def test_the_before_cohort_was_actually_fixed() -> None:
    """The headline number, and the one that is not vacuous.

    Flag recall on the *after* run is meaningless once there are no provable
    errors left to catch — a perfect score over an empty set. What can be said
    is what became of the cells that were provably wrong before: each is either
    fixed, now refused outright, or still wrong.
    """
    was_wrong = [r for r in ROWS if r["before_verdict"] == "wrong"]
    assert was_wrong, "nothing to improve on"
    still = [r for r in was_wrong if r["after_verdict"] == "wrong"]
    unflagged = [r for r in still if not r["after_flagged"]]

    # A cell may legitimately remain unverifiable — that is the reader's job,
    # not this test's. What may not happen is a provable error going unflagged.
    assert not unflagged, (
        f"{len(unflagged)} provably-wrong cells are unflagged: "
        + ", ".join(str(r["source"]) for r in unflagged[:5])
    )


def test_no_new_provable_errors_were_introduced() -> None:
    regressions = [
        r
        for r in ROWS
        if r["after_verdict"] == "wrong" and r["before_verdict"] != "wrong"
    ]
    unflagged = [r for r in regressions if not r["after_flagged"]]
    assert not unflagged, (
        "new provable errors, unflagged: "
        + ", ".join(f"{r['source']} -> {r['after']}" for r in unflagged[:5])
    )


@pytest.mark.parametrize("which", ["before", "after"])
def test_the_metrics_are_reportable(which: str) -> None:
    """The report the brief asked for. Printed with `-s`.

    It deliberately does not assert that anything was scored. After the change
    the provable-error set is empty, and an assertion that it must not be would
    reward leaving errors in.
    """
    scored, wrong, error_rate, recall = _measure(ROWS, which)
    unknown = sum(1 for r in ROWS if r[f"{which}_verdict"] == "unknown")
    refused = sum(1 for r in ROWS if r[f"{which}_verdict"] == "refused")
    caught = sum(
        1 for r in ROWS if r[f"{which}_verdict"] == "wrong" and r[f"{which}_flagged"]
    )
    print(
        f"\n  {which:<7} cells={len(ROWS)}  provably_wrong={wrong}  "
        f"of_those_flagged={caught}  "
        f"flag_recall={'n/a' if not wrong else format(recall, '.2f')}  "
        f"refused={refused}  unlabelled={unknown}"
    )
    assert scored + unknown + refused == len(ROWS)
