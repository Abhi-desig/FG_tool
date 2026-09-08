"""The poster engine's reject list, checked offline.

Every test here is about money or about a printed mistake. The prompt engine
(v1.1, ADR-036) states these rules to the model that writes the prompt; this
suite is what makes the checkable ones true rather than requested.

The distinction the suite keeps hold of: exactly one rule refuses a call, and it
is the one that is exact. Everything else warns. A validator that refuses on
judgement is one the operator learns to override, and the override would take
the digit check with it.
"""

from __future__ import annotations

from backend.posterspec import MAX_POSTER_WORDS, check, poster_text

# A prompt in the engine's shape, with every global lock in place. Tests below
# damage one thing at a time so a failure names one cause.
GOOD = (
    "A vertical 4:5 print poster. The upper 45% of the frame is held empty as a "
    "reserved zone, one continuous field of deep navy, smooth and free of all "
    "detail — nothing enters this area, no clouds, no wires, no roofline. Below "
    "it, weathered red clay tile in late golden-hour light raking from the left, "
    "with one specular highlight along the upper tile course. All type on one "
    'left axis: very large extra-bold warm gold text reading "Happy Onam"; '
    'medium-bold white text reading "Subsidy up to 78,000"; small grey text on '
    'its own line reading "9656 00 3244". All lettering sharp and correctly '
    "spelled. Four colours only — deep navy, white, warm gold, grey. Nothing "
    "within 8% of any edge. No watermarks, no extra text, no additional words "
    "anywhere in the image."
)

COPY = {
    "main": "Happy Onam",
    "h1": "Subsidy up to 78,000",
    "h2": "9656 00 3244",
}


def test_a_prompt_in_the_engines_shape_passes_clean() -> None:
    """The baseline. If this ever warns, a rule got stricter than the engine."""
    report = check(GOOD, COPY)
    assert report.ok
    assert report.warnings == []


# --- the one refusal -------------------------------------------------------


def test_a_lost_phone_number_refuses_the_call() -> None:
    """The failure this module exists for.

    A poster with the shop's number one digit wrong is printed, delivered and
    paid for before anybody reads it. ADR-030 used to prevent this by setting
    the digits in the app; ADR-034 gave that up. This is what is left, and it
    has to stop the spend rather than warn beside it.
    """
    without = GOOD.replace(' small grey text on its own line reading "9656 00 3244".', "")
    report = check(without, COPY)

    assert not report.ok
    assert "9656" in report.refusals[0]
    assert "3244" in report.refusals[0]
    # Says the money was not spent, because that is the operator's first question.
    assert "nothing was sent" in report.refusals[0].casefold()


def test_a_changed_digit_is_caught_not_just_a_missing_line() -> None:
    report = check(GOOD.replace("3244", "3245"), COPY)
    assert not report.ok
    assert "3244" in report.refusals[0]


def test_copy_the_design_never_asked_for_is_not_reported_as_lost() -> None:
    """The false positive that would make the check unusable.

    `posters.copy_used_by` narrows the copy to the tags a design actually draws.
    Passing the whole copy for a headline-only design would refuse every good
    poster, and this is the test that keeps the narrowing honest.
    """
    headline_only = {"main": "Happy Onam"}
    assert check(GOOD, headline_only).ok


def test_a_prompt_with_no_digits_in_its_copy_is_never_refused() -> None:
    assert check(GOOD, {"main": "Happy Onam"}).ok
    assert check(GOOD, {}).ok


def test_an_empty_prompt_refuses_rather_than_spending() -> None:
    assert not check("", COPY).ok
    assert not check("   \n  ", COPY).ok


# --- the specificity budget ------------------------------------------------


def test_generic_adjectives_are_named_individually() -> None:
    report = check(GOOD.replace("weathered red clay tile", "a stunning modern-looking roof"), COPY)
    assert report.ok  # a warning, not a refusal
    assert any("stunning" in w and "modern-looking" in w for w in report.warnings)


def test_a_banned_word_inside_the_operators_own_copy_is_left_alone() -> None:
    """The copy is sacred, including its adjectives.

    A shop selling a "Perfect Onam Deal" is not writing a defective prompt, and
    flagging its own wording back at the operator is how a warning list gets
    ignored wholesale.
    """
    copy = dict(COPY, main="Perfect Onam Deal")
    prompt = GOOD.replace("Happy Onam", "Perfect Onam Deal")
    report = check(prompt, copy)
    assert report.ok
    assert not any("perfect" in w.casefold() for w in report.warnings)


def test_a_banned_word_is_not_matched_inside_a_longer_word() -> None:
    """ "perfectly even" is a legitimate way to describe a gradient."""
    report = check(GOOD.replace("smooth and free of all detail", "perfectly even"), COPY)
    assert not any("perfect" in w.casefold() for w in report.warnings)


# --- the typography cap ----------------------------------------------------


def test_only_quoted_runs_count_as_poster_text() -> None:
    assert poster_text(GOOD) == ["Happy Onam", "Subsidy up to 78,000", "9656 00 3244"]


def test_too_many_words_of_lettering_warns_with_the_count() -> None:
    long_line = " ".join(f"word{n}" for n in range(MAX_POSTER_WORDS + 5))
    prompt = GOOD.replace("Happy Onam", long_line)
    report = check(prompt, dict(COPY, main=long_line))
    assert any(str(MAX_POSTER_WORDS) in w and "cap" in w for w in report.warnings)


def test_a_design_that_does_not_quote_its_copy_says_so_once() -> None:
    """Not a wall of failures — one sentence saying the check could not run.

    The shipped example quotes its copy, but the operator's own designs are
    theirs and may not. Silence would imply the wording had been checked.
    """
    unquoted = GOOD.replace('"', "")
    report = check(unquoted, {"main": "Happy Onam"})
    assert any("cannot check the wording" in w for w in report.warnings)


def test_wording_that_is_not_in_the_copy_is_reported() -> None:
    report = check(GOOD.replace("Happy Onam", "Happy Onam Festival Sale"), COPY)
    assert any("not in your copy" in w for w in report.warnings)


def test_a_capitalised_headline_is_not_treated_as_a_rewrite() -> None:
    """The engine sets headlines in capitals; the shop writes them in title case."""
    report = check(GOOD.replace("Happy Onam", "HAPPY ONAM"), COPY)
    assert not any("not in your copy" in w for w in report.warnings)


# --- the global locks ------------------------------------------------------


def test_a_reserved_zone_without_a_percentage_warns() -> None:
    report = check(
        GOOD.replace("upper 45% of the frame is held empty", "top is left as negative space"), COPY
    )
    assert any("percentage" in w for w in report.warnings)


def test_an_aspect_ratio_stated_late_warns() -> None:
    report = check(GOOD.replace("A vertical 4:5 print poster.", "A print poster."), COPY)
    assert any("aspect ratio" in w for w in report.warnings)


def test_an_undeclared_palette_warns_and_an_oversized_one_is_counted() -> None:
    missing = check(
        GOOD.replace("Four colours only — deep navy, white, warm gold, grey.", ""), COPY
    )
    assert any("palette is not declared" in w for w in missing.warnings)

    six = check(
        GOOD.replace(
            "Four colours only — deep navy, white, warm gold, grey.",
            "Colours only — deep navy, white, warm gold, grey, dusty green and terracotta.",
        ),
        COPY,
    )
    assert any("over the 4-colour lock" in w for w in six.warnings)


def test_a_missing_closing_lock_warns() -> None:
    report = check(
        GOOD.replace(
            "No watermarks, no extra text, no additional words anywhere in the image.", ""
        ),
        COPY,
    )
    assert any("no-extra-text lock" in w for w in report.warnings)
