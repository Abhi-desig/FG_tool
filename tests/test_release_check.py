"""Tests for the release check.

NEXT.md 0.1 and 4.2. The check exists because force-adding `frontend/dist`
depends on somebody remembering, and once it was forgotten the shop PC would
have been served a UI with no AI screens in it. A check nobody has tested is the
same class of promise, so these exercise its logic directly.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_release.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_release", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load()


def test_the_script_exists_and_is_runnable() -> None:
    assert SCRIPT.exists()
    assert check.main is not None


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        (
            '<script src="/assets/index-abc.js"></script>',
            {"/assets/index-abc.js"},
        ),
        (
            '<link href="/assets/index-abc.css"><script src="/assets/index-def.js">',
            {"/assets/index-abc.css", "/assets/index-def.js"},
        ),
        # Vite emits crossorigin and type attributes around them.
        (
            '<script type="module" crossorigin src="/assets/i.js"></script>',
            {"/assets/i.js"},
        ),
        ("<p>no assets here</p>", set()),
    ],
)
def test_asset_references_are_found(html: str, expected: set[str]) -> None:
    assert check.referenced_assets(html) == expected


def test_a_relative_or_external_reference_is_ignored() -> None:
    """Only the /assets bundles matter; a CDN or a data URI is not our concern."""
    html = '<img src="https://example.com/x.png"><script src="./local.js">'
    assert check.referenced_assets(html) == set()


def test_the_real_dist_passes_the_check() -> None:
    """The whole point: this must be green on a release commit.

    If this fails, `frontend/dist` and the tracked bundle have diverged — rebuild
    and `git add -f frontend/dist`. See README.md → Releasing the UI.
    """
    assert check.main() == 0, "frontend/dist is not consistent with what is committed"


def test_the_shipped_bundle_contains_the_phase_5_screens() -> None:
    """The exact 0.1 regression, pinned.

    `git show HEAD:…/index-CkNRyGuO.js` contained zero references to `api/styles`
    or `api/ai/models`, both added in the commit that shipped them. A hash check
    alone would not have caught that the *contents* were a version behind.
    """
    dist = ROOT / "frontend" / "dist"
    index = dist / "index.html"
    if not index.exists():
        pytest.skip("frontend/dist is not built")

    bundles = [
        dist / reference.lstrip("/")
        for reference in check.referenced_assets(index.read_text(encoding="utf-8"))
        if reference.endswith(".js")
    ]
    assert bundles, "index.html loads no JavaScript at all"

    source = "\n".join(b.read_text(encoding="utf-8", errors="replace") for b in bundles)
    for route in ("api/styles", "api/ai/models", "api/features"):
        assert route in source, f"the shipped bundle never calls {route}"
