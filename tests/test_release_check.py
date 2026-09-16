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
    # Sentinels: routes recent enough that a stale bundle would not have them,
    # and spread across the app so one dead screen is caught. `api/styles` was
    # the original sentinel and is gone with the poster rebuild (ADR-034) —
    # a sentinel for a deleted route asserts nothing.
    for route in ("api/posters/generate", "api/ai/models", "api/features"):
        assert route in source, f"the shipped bundle never calls {route}"


# --- the shop PC package ---------------------------------------------------

PACKAGE = ROOT / "scripts" / "package_windows.py"


def _load_packager():
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("package_windows", PACKAGE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_packager_refuses_the_things_that_must_never_ship() -> None:
    """CLAUDE.md rule 5 and SECURITY.md. A packaged `.env` or `data.db` would be
    emailed around, and the operator's own database would be overwritten by ours.
    """
    pkg = _load_packager()
    from pathlib import Path as _Path

    for name in (
        ".env",
        "data.db",
        "data.db-wal",
        "backend/__pycache__/main.pyc",
        "models/birefnet-general.onnx",
        "tests/test_ai.py",
        "frontend/node_modules/react/index.js",
        "something.log",
    ):
        assert pkg.refused(_Path(name)), f"{name} would have been packaged"


def test_the_packager_includes_what_the_shop_pc_cannot_rebuild() -> None:
    """No Node on that machine, so the built UI and the font map have to travel."""
    pkg = _load_packager()
    from pathlib import Path as _Path

    for name in (
        "backend/main.py",
        "frontend/dist/index.html",
        "frontend/dist/fonts/NotoSansMalayalam.woff2",
        "data/maps/ML-TTKarthika.map",
    ):
        assert not pkg.refused(_Path(name)), f"{name} was wrongly excluded"
    assert "frontend/dist" in pkg.TREES
    assert "uv.lock" in pkg.FILES


# --- the bundle must match the source it was built from --------------------


def _fake_frontend(tmp_path: Path) -> Path:
    """A minimal frontend tree: the files `source_hash` is told to read."""
    frontend = tmp_path / "frontend"
    (frontend / "src" / "lib").mkdir(parents=True)
    (frontend / "public").mkdir()
    (frontend / "dist").mkdir()
    (frontend / "index.html").write_text("<div id=root></div>", encoding="utf-8")
    (frontend / "package.json").write_text('{"name":"x"}', encoding="utf-8")
    (frontend / "src" / "main.tsx").write_text("export const a = 1", encoding="utf-8")
    (frontend / "src" / "lib" / "api.ts").write_text("export const b = 2", encoding="utf-8")
    return frontend


def _point_at(monkeypatch: pytest.MonkeyPatch, frontend: Path) -> None:
    monkeypatch.setattr(check, "FRONTEND", frontend)
    monkeypatch.setattr(check, "DIST", frontend / "dist")
    monkeypatch.setattr(check, "SOURCE_STAMP", frontend / "dist" / "source-hash.txt")


def test_a_frontend_edit_without_a_rebuild_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure this was added for.

    Every other check in this script compares `dist` against itself or against
    git, and all of them pass for a bundle that is simply out of date. During
    the ADR-037 work a frontend fix was invisible in the browser and the gate
    stayed green, because nothing compared the bundle to its source.
    """
    frontend = _fake_frontend(tmp_path)
    _point_at(monkeypatch, frontend)

    check.stamp()  # stands in for `npm run build`
    stamped = (frontend / "dist" / "source-hash.txt").read_text(encoding="utf-8").strip()
    assert stamped == check.source_hash(), "a fresh stamp must match its own source"

    (frontend / "src" / "lib" / "api.ts").write_text("export const b = 3", encoding="utf-8")

    assert check.source_hash() != stamped, "an unrebuilt edit was not detected"


def test_renaming_a_component_changes_the_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rename changes the bundle without changing any file's contents.

    Hashing contents alone would miss it, so the path is in the digest too.
    """
    frontend = _fake_frontend(tmp_path)
    _point_at(monkeypatch, frontend)
    before = check.source_hash()

    (frontend / "src" / "main.tsx").rename(frontend / "src" / "entry.tsx")

    assert check.source_hash() != before


def test_the_built_output_and_node_modules_are_not_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise the hash could never settle.

    `dist` is the thing being checked — hashing it would make every build
    change its own answer. `node_modules` is derived from the lockfile, which
    *is* hashed, and walking it would cost seconds on every run.
    """
    frontend = _fake_frontend(tmp_path)
    _point_at(monkeypatch, frontend)
    before = check.source_hash()

    (frontend / "dist" / "assets").mkdir()
    (frontend / "dist" / "assets" / "index-abc.js").write_text("bundled", encoding="utf-8")
    (frontend / "node_modules" / "react").mkdir(parents=True)
    (frontend / "node_modules" / "react" / "index.js").write_text("react", encoding="utf-8")

    assert check.source_hash() == before


def test_a_dependency_bump_changes_the_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It changes the bundle without touching a line of `src`."""
    frontend = _fake_frontend(tmp_path)
    _point_at(monkeypatch, frontend)
    before = check.source_hash()

    (frontend / "package-lock.json").write_text('{"lockfileVersion":3}', encoding="utf-8")

    assert check.source_hash() != before


def test_the_hash_does_not_depend_on_where_the_checkout_lives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two clones of the same commit must agree, or CI fights every developer."""
    hashes = []
    for name in ("one", "two"):
        frontend = _fake_frontend(tmp_path / name)
        _point_at(monkeypatch, frontend)
        hashes.append(check.source_hash())
    assert hashes[0] == hashes[1]


def test_an_unstamped_bundle_is_a_failure_not_a_pass() -> None:
    """An unknown age is the state this whole check exists to stop passing."""
    frontend = check.FRONTEND
    if not (frontend / "dist" / "index.html").exists():
        pytest.skip("frontend/dist is not built")
    assert check.SOURCE_STAMP.exists(), (
        "the shipped bundle carries no source stamp — rebuild it so its age is provable"
    )


def test_the_package_ships_the_assets_the_features_need() -> None:
    """The bug this pins: `data/maps` was packaged and the rest of `data` was not.

    Everything added after the font map — the Olam word library, the name
    lexicon, the place gazetteer, all nine poster styles — was silently left out
    of the shop PC's zip. The result was not a crash: the poster screen read
    "No poster styles yet" and the translator ran with no offline dictionary,
    which is most of three phases of work missing with nothing to say so.
    """
    pkg = _load_packager()
    from pathlib import Path as _Path

    for name in (
        "data/dictionary/en-ml.tsv.gz",
        "data/names/exceptions.tsv",
        "data/places/gazetteer.tsv",
        "data/poster_prompts/04-offer-block.md",
    ):
        assert not pkg.refused(_Path(name)), f"{name} would not reach the shop PC"
    assert "data" in pkg.TREES, "name the folder, so the next asset travels by default"


def test_the_package_names_its_own_build() -> None:
    """VERSION.txt answers "which version is on the shop PC?" months later."""
    pkg = _load_packager()
    text = pkg.version_text()
    assert "Commit:" in text
    assert "Built:" in text
    # Never a bare "unknown" commit line with no explanation beside it.
    assert len(text.splitlines()) >= 5


def test_the_named_entry_point_is_the_same_build() -> None:
    """One implementation. Two names is how the two drift."""
    import importlib.util
    import sys as _sys

    path = ROOT / "scripts" / "build_shop_zip.py"
    assert path.exists()
    spec = importlib.util.spec_from_file_location("build_shop_zip", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    _sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    # Not an identity check on `main` — loading a module by path twice gives two
    # distinct function objects, so that would only ever be testing the loader.
    # What matters is that this file delegates instead of reimplementing.
    assert module.main.__module__ == "package_windows"
    assert not hasattr(module, "TREES"), "the entry point has grown its own file list"
    assert not hasattr(module, "build"), "the entry point has grown its own packer"


def test_bundling_the_weights_never_lifts_the_bans_that_matter() -> None:
    """`--with-models` unbans exactly one directory.

    The weights are excluded by default because they are 2 GB and fetch
    themselves, not because they are secret. A key or the operator's database
    is a different question and the answer does not change.
    """
    pkg = _load_packager()
    from pathlib import Path as _Path

    assert pkg.refused(_Path("models/birefnet-general/model.onnx"))
    assert not pkg.refused(_Path("models/birefnet-general/model.onnx"), True)

    for name in (".env", "data.db", "data.db-wal", "tests/test_ai.py", "x.log"):
        assert pkg.refused(_Path(name), True), f"{name} escaped with --with-models"


def test_the_huggingface_cache_is_flattened_not_doubled(tmp_path: Path) -> None:
    """A naive copy would follow the symlinks and package 2 GB instead of 1.

    Worse than the size: Windows cannot use a symlink out of a zip — its own
    extractor writes the link text into a file, which breaks the model. So the
    snapshots are materialised and `blobs/` is dropped.
    """
    pkg = _load_packager()
    import shutil as _shutil

    # A cache in the shape huggingface_hub builds: real bytes in blobs/, and
    # snapshots/ as symlinks pointing at them.
    models = tmp_path / "models" / "hf" / "hub" / "models--x--y"
    (models / "blobs").mkdir(parents=True)
    (models / "snapshots" / "abc").mkdir(parents=True)
    (models / "refs").mkdir()
    (models / "blobs" / "deadbeef").write_bytes(b"weights" * 1000)
    (models / "refs" / "main").write_text("abc", encoding="utf-8")
    (models / "snapshots" / "abc" / "model.bin").symlink_to(models / "blobs" / "deadbeef")

    staging = tmp_path / "staging"
    staging.mkdir()
    original_root = pkg.ROOT
    try:
        pkg.ROOT = tmp_path
        pkg.copy_models(staging)
    finally:
        pkg.ROOT = original_root

    packed = staging / "models" / "hf" / "hub" / "models--x--y"
    snapshot = packed / "snapshots" / "abc" / "model.bin"

    assert snapshot.is_file() and not snapshot.is_symlink(), "Windows cannot follow this"
    assert snapshot.read_bytes() == b"weights" * 1000, "the bytes did not travel"
    assert (packed / "refs" / "main").exists(), "from_pretrained resolves refs/main first"
    assert not (packed / "blobs").exists(), "blobs behind materialised snapshots are dead weight"

    del _shutil


def test_files_windows_reads_get_windows_line_endings(tmp_path: Path) -> None:
    """LF-only breaks two things on the shop PC, quietly.

    `cmd.exe` reads ahead to parse a multi-line `if errorlevel 1 ( ... )` block,
    and LF endings are a known way to make it mis-handle one — both generated
    launchers contain exactly that construct, on their error paths, so it would
    fail only once something else had already gone wrong. And Notepad before
    Windows 10 1809 renders an LF-only file as a single unbroken line, which
    README-FIRST.txt is the worst possible candidate for.
    """
    pkg = _load_packager()
    target = tmp_path / "x.bat"

    pkg.write_for_windows(target, "@echo off\nif errorlevel 1 (\n  pause\n)\n")
    raw = target.read_bytes()

    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b""), "a bare LF survived"

    # Idempotent: text that already has CRLF must not come out as CR CR LF.
    pkg.write_for_windows(target, "already\r\nCRLF\r\n")
    assert b"\r\r\n" not in target.read_bytes()


def test_the_app_reads_a_style_file_saved_by_notepad(tmp_path, monkeypatch) -> None:
    """The operator is told they can drop .md style files into the folder.

    On Windows they will do that in Notepad, which writes CRLF. Python's text
    mode translates it on the way in, so this works — pinned because the front
    matter is matched with a regex that ends in a literal `\\n`, and it would be
    easy to "tighten" that into something CRLF breaks silently.
    """
    from backend.features import posters

    monkeypatch.setattr(posters, "DESIGNS_DIR", tmp_path)
    (tmp_path / "notepad.md").write_bytes(
        b"name: Saved in Notepad\r\naspect: 4:5\r\n---\r\nA 4:5 poster. No watermarks.\r\n"
    )

    found = posters.designs()
    assert [d.name for d in found] == ["Saved in Notepad"]
    assert found[0].aspect == "4:5"
