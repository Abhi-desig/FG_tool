"""Tests for the image layer.

The model-backed paths are marked `slow` and skipped unless the weights are
present, so the suite stays fast and works on a machine that has not downloaded
1 GB of models. The pure logic — validation, guards, tiling arithmetic, output
encoding — is always tested.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image, ImageDraw

from backend import config, models
from backend.features import images, printsize
from backend.jobs import Reporter, run_sync, submit


def make_image(w: int = 200, h: int = 150, fmt: str = "PNG") -> bytes:
    img = Image.new("RGB", (w, h), (40, 90, 160))
    draw = ImageDraw.Draw(img)
    draw.ellipse((w // 4, h // 4, 3 * w // 4, 3 * h // 4), fill=(230, 200, 60))
    buffer = io.BytesIO()
    img.save(buffer, format=fmt)
    return buffer.getvalue()


# --- loading and validation ----------------------------------------------


def test_load_reads_a_real_image() -> None:
    image = images.load(make_image())
    assert image.size == (200, 150)


def test_load_rejects_non_images() -> None:
    with pytest.raises(images.ImageError):
        images.load(b"this is not an image, it is a sentence")


def test_load_rejects_a_renamed_file() -> None:
    """SECURITY.md: the real header decides, not the extension."""
    with pytest.raises(images.ImageError):
        images.load(b"GIF89a" + b"\x00" * 64)  # truncated/bogus


def test_load_rejects_empty_input() -> None:
    with pytest.raises(images.ImageError):
        images.load(b"")


def test_decompression_bomb_limit_is_set_deliberately() -> None:
    """Still a limit, but one that admits real work.

    SECURITY.md §4 wants a deliberate ceiling on pixel *dimensions*, and there
    was none — the only guard was `MAX_IMAGE_PIXELS` at 1 Gpx, eleven times
    Pillow's own default, where an RGBA decode is ~4 GB on a 12 GB machine
    (NEXT.md 2.1).

    Pillow's limit sits just above ours so `load` refuses first, with a sentence
    the operator can act on rather than one they cannot.
    """
    assert Image.MAX_IMAGE_PIXELS is not None, "an unbounded decoder is a bomb risk"
    assert Image.MAX_IMAGE_PIXELS > images.MAX_INPUT_PIXELS
    # A 6x4 ft banner at 300 DPI is 311 Mpx and is not an input; 150 MP still
    # covers every flatbed scan and camera file this shop sees.
    assert images.MAX_INPUT_PIXELS >= 100_000_000, "must admit large scans"


def test_facts_reports_what_the_ui_needs() -> None:
    facts = images.facts(images.load(make_image(320, 240)))
    assert (facts.width, facts.height) == (320, 240)
    assert facts.megapixels == pytest.approx(0.08, abs=0.01)
    assert facts.has_alpha is False


def test_facts_detects_alpha() -> None:
    rgba = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    assert images.facts(rgba).has_alpha is True


# --- tiling arithmetic ---------------------------------------------------


def test_tile_geometry_matches_the_static_graph() -> None:
    """These constants are baked into the ONNX graph, not tunable."""
    spec = models.spec("realesrgan-x4")
    assert spec.input_shape == (1, 3, images.TILE, images.TILE)
    assert spec.scale == images.SCALE
    assert images.STEP == images.TILE - 2 * images.OVERLAP
    assert images.OUT_STEP == images.STEP * images.SCALE


@pytest.mark.parametrize(
    ("w", "h", "tiles"),
    [(96, 96, 1), (192, 96, 2), (200, 150, 6), (960, 960, 100)],
)
def test_tile_count(w: int, h: int, tiles: int) -> None:
    assert images.tile_count_for(w, h) == tiles


def test_estimate_scales_with_tile_count() -> None:
    small = images.estimate_seconds(96, 96)
    big = images.estimate_seconds(960, 960)
    assert big == pytest.approx(small * 100, rel=0.01)


# --- guards --------------------------------------------------------------


def test_memory_guard_refuses_an_unbuildable_result() -> None:
    """Device-independent: this many pixels cannot be assembled on 12 GB."""
    source = images.load(make_image(9000, 9000))
    target = (30000, 30000)  # 900 MP, over MAX_OUTPUT_PIXELS
    assert target[0] * target[1] > images.MAX_OUTPUT_PIXELS
    reason = images._model_unusable(source, target) or ""
    assert "beyond what can be assembled" in reason


def test_slowness_never_blocks_a_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only physical limits refuse the model; a long run is the operator's call.

    The estimate is shown before they commit and the job is cancellable, so a
    slow machine must not silently downgrade their chosen scale.
    """
    source = images.load(make_image(2000, 2000))
    monkeypatch.setattr(config, "providers", lambda: ["CPUExecutionProvider"])
    # ~18 minutes on CPU, and still allowed.
    assert images.estimate_seconds(2000, 2000) > 900
    assert images._model_unusable(source, (8000, 8000)) is None


def test_estimate_is_reported_so_the_choice_is_informed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "providers", lambda: ["CPUExecutionProvider"])
    slow = images.estimate_seconds(2000, 2000)
    monkeypatch.setattr(config, "providers", lambda: ["CoreMLExecutionProvider"])
    fast = images.estimate_seconds(2000, 2000)
    assert slow > fast * 30, "the CPU/GPU gap must be visible in the estimate"


# --- scale options and accepting any size --------------------------------


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("2x", (400, 300)), ("4x", (800, 600))],
)
def test_scale_modes_give_the_stated_size(
    mode: str, expected: tuple[int, int]
) -> None:
    assert images.target_for(mode, (200, 150), None) == expected  # type: ignore[arg-type]


def test_print_mode_uses_the_calculator_target() -> None:
    assert images.target_for("print", (200, 150), (3000, 2250)) == (3000, 2250)


def test_print_mode_without_a_target_falls_back_to_4x() -> None:
    assert images.target_for("print", (200, 150), None) == (800, 600)


@pytest.mark.parametrize("pixels", [(1, 1), (37, 5000), (9000, 12000), (20000, 500)])
def test_any_input_shape_is_accepted(pixels: tuple[int, int]) -> None:
    """No aspect ratio or dimension is rejected outright."""
    for mode in ("2x", "4x"):
        out = images.target_for(mode, pixels, None)  # type: ignore[arg-type]
        assert out[0] > 0 and out[1] > 0


def test_decode_limit_admits_a_large_scan() -> None:
    """A print shop handles 100+ MP scans; only a real bomb should be refused."""
    assert images.MAX_INPUT_PIXELS >= 100_000_000
    # 12000x10000 is a big flatbed scan and must still open.
    images._check_dimensions((12_000, 10_000))


def test_an_oversized_image_is_refused_with_a_usable_message() -> None:
    """The measured case: a 546 KB PNG at 24000x24000 was accepted (NEXT.md 2.1).

    Bombs are small on disk, so the 1 GB byte cap never sees them. The header is
    checked before the decode, which is the only point at which refusing is free.
    """
    with pytest.raises(images.ImageError) as caught:
        images._check_dimensions((24_000, 24_000))
    message = str(caught.value)
    assert "24000" in message
    # It must say what to do, not just that it refused.
    assert "smaller version" in message


def test_a_pathological_strip_is_refused_on_its_long_side() -> None:
    """1 x 200,000,000 passes a pixel-count test and breaks everything after."""
    with pytest.raises(images.ImageError):
        images._check_dimensions((200_000, 1))


def test_the_side_cap_is_checked_before_the_pixel_cap() -> None:
    """So the message names the real problem: one enormous dimension."""
    with pytest.raises(images.ImageError) as caught:
        images._check_dimensions((images.MAX_INPUT_SIDE + 1, 2))
    assert "longest side" in str(caught.value)


def test_an_oversized_upload_is_refused_by_load() -> None:
    """End to end through the real entry point, on real bytes.

    A flat colour compresses to almost nothing, which is exactly why the byte
    cap cannot be the defence.
    """
    buffer = io.BytesIO()
    Image.new("L", (18_000, 18_000), 0).save(buffer, format="PNG")
    data = buffer.getvalue()
    assert len(data) < 5_000_000, "fixture is not a compression bomb"
    with pytest.raises(images.ImageError):
        images.load(data)


def test_upload_cap_is_generous_enough_for_real_files() -> None:
    assert config.MAX_UPLOAD_BYTES >= 512 * 1024 * 1024


def test_encode_ceiling_is_a_real_physical_limit() -> None:
    """Documents why there is a ceiling at all, and that it is generous.

    An on-disk canvas was tried and removed: Pillow's frombuffer only shares
    memory for byte-aligned modes, and the encoder materialises the whole image
    regardless — so the peak lands at encode either way.
    """
    assert images.encodable_pixels() == images.MAX_OUTPUT_PIXELS
    # A 5000px source at 4x must still take the model path.
    assert (5000 * 4) * (3500 * 4) <= images.MAX_OUTPUT_PIXELS


def test_oversized_result_still_returns_an_image() -> None:
    """Beyond the ceiling the job degrades to Lanczos — it never just fails."""
    source = images.load(make_image(300, 300))
    huge = (20000, 20000)  # 400 MP, past the ceiling
    result = images.upscale(source, "print", target=huge)
    assert result.image.size == huge
    assert result.method == "lanczos"
    assert "beyond what can be assembled" in result.note


def test_shrinking_is_a_clean_resize_not_an_upscale() -> None:
    source = images.load(make_image(800, 600))
    result = images.upscale(source, "print", target=(400, 300))
    assert result.method == "lanczos"
    assert result.tiles == 0
    assert result.image.size == (400, 300)


def test_lanczos_note_does_not_claim_added_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force the fallback by making the weights unavailable, not by slowness.

    Deliberately NOT done by forcing a CPU provider — that would run a real
    18-minute inference inside the test suite.
    """
    monkeypatch.setattr(models, "is_available", lambda key: False)
    source = images.load(make_image(400, 400))
    result = images.upscale(source, "2x")
    assert result.method == "lanczos"
    assert result.image.size == (800, 800)
    assert "does not add detail" in result.note.lower()


# --- output encoding -----------------------------------------------------


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "TIFF", "WEBP"])
def test_encode_round_trips_and_stamps_dpi(fmt: str) -> None:
    image = images.load(make_image())
    data, out_fmt, media = images.encode(image, fmt, dpi=300)
    assert out_fmt == fmt
    assert media.startswith("image/")
    reopened = Image.open(io.BytesIO(data))
    assert reopened.size == image.size
    if fmt in {"PNG", "JPEG", "TIFF"}:
        assert reopened.info.get("dpi", (0, 0))[0] == pytest.approx(300, abs=1)


def test_cmyk_switches_png_to_tiff_because_png_cannot_hold_it() -> None:
    image = images.load(make_image())
    data, fmt, _ = images.encode(image, "PNG", cmyk=True)
    assert fmt == "TIFF"
    assert Image.open(io.BytesIO(data)).mode == "CMYK"


def test_alpha_is_flattened_for_formats_without_it() -> None:
    rgba = Image.new("RGBA", (20, 20), (255, 0, 0, 0))
    data, _, _ = images.encode(rgba, "JPEG")
    assert Image.open(io.BytesIO(data)).mode == "RGB"


def test_thumbnail_is_bounded() -> None:
    big = Image.new("RGB", (4000, 3000))
    preview = Image.open(io.BytesIO(images.thumbnail(big, box=640)))
    assert max(preview.size) <= 640


# --- protect mask (pure compositing, no model) ---------------------------


def test_protect_mask_forces_opaque_where_painted() -> None:
    transparent = Image.new("RGBA", (100, 100), (255, 0, 0, 0))
    mask = Image.new("L", (100, 100), 0)
    ImageDraw.Draw(mask).rectangle((20, 20, 80, 80), fill=255)

    result = images._apply_protect_mask(transparent.copy(), mask)
    alpha = np.asarray(result.getchannel("A"))
    assert alpha[50, 50] == 255, "painted centre must be fully kept"
    assert alpha[2, 2] == 0, "unpainted corner must stay transparent"


def test_protect_mask_is_resized_to_the_image() -> None:
    image = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    small_mask = Image.new("L", (20, 10), 255)
    result = images._apply_protect_mask(image, small_mask)
    assert np.asarray(result.getchannel("A")).min() > 200


# --- model registry ------------------------------------------------------


def test_background_model_is_locked() -> None:
    """ADR-007. Changing this is the mistake LICENSES.md exists to prevent."""
    assert config.BACKGROUND_MODEL == "birefnet-general"


@pytest.mark.parametrize("banned", ["bria-rmbg", "briaai/RMBG-2.0", "flux-kontext-dev"])
def test_banned_models_are_refused_by_name(banned: str) -> None:
    with pytest.raises(models.ModelError, match="banned"):
        models.spec(banned)


def test_unknown_model_is_refused() -> None:
    with pytest.raises(models.ModelError, match="unknown model"):
        models.spec("some-model-off-the-internet")


@pytest.mark.parametrize(
    "key", ["../../../etc/passwd", "/etc/hosts", "..", "realesrgan-x4/../../secret"]
)
def test_model_keys_cannot_be_paths(key: str) -> None:
    with pytest.raises(models.ModelError):
        models.spec(key)


def test_weights_stay_inside_the_models_directory() -> None:
    path = models.local_path("realesrgan-x4")
    assert path.is_relative_to(config.MODELS_DIR.resolve())


def test_upscaler_weights_are_checksum_pinned() -> None:
    """A floating third-party file could change under us; a hash cannot."""
    spec = models.spec("realesrgan-x4")
    assert spec.sha256 and len(spec.sha256) == 64
    assert spec.url and "resolve/" in spec.url
    revision = spec.url.split("resolve/")[1].split("/")[0]
    assert len(revision) == 40, "must pin an immutable commit, not a branch"


def test_registry_describes_itself_for_settings() -> None:
    """The registry grows each phase, so assert content rather than equality."""
    rows = {r["key"]: r for r in models.describe()}
    assert {"birefnet-general", "realesrgan-x4"} <= set(rows)
    assert rows["birefnet-general"]["licence"] == "MIT"
    assert rows["realesrgan-x4"]["licence"] == "BSD-3-Clause"


def test_every_registered_model_permits_commercial_use() -> None:
    """LICENSES.md: the shop sells this work. No non-commercial weights, ever."""
    allowed = {"MIT", "Apache-2.0", "BSD-3-Clause"}
    for row in models.describe():
        assert row["licence"] in allowed, row


# --- jobs ----------------------------------------------------------------


def test_job_reports_progress_and_finishes() -> None:
    def work(report: Reporter) -> dict[str, object]:
        report.step("Halfway", 0.5)
        return {"answer": 42}

    job = run_sync("test", "unit test", work)
    assert job.status == "done"
    assert job.result == {"answer": 42}
    assert job.progress == 1.0


def test_job_failure_is_captured_not_raised() -> None:
    def work(_: Reporter) -> dict[str, object]:
        raise ValueError("deliberate")

    job = run_sync("test", "failing", work)
    assert job.status == "failed"
    assert "deliberate" in (job.error or "")


def test_job_can_be_cancelled_cooperatively() -> None:
    def work(report: Reporter) -> dict[str, object]:
        for i in range(1000):
            report.step(f"step {i}", i / 1000)
        return {}

    job = submit("test", "cancellable", work)
    job.cancel()
    while job.status in {"queued", "running"}:
        pass
    assert job.status == "cancelled"


def test_reporter_clamps_progress() -> None:
    def work(report: Reporter) -> dict[str, object]:
        report.progress(5.0)
        return {}

    assert run_sync("test", "clamp", work).progress == 1.0


def test_jobs_run_one_at_a_time() -> None:
    """The memory rule as a queue: two model jobs must never overlap."""
    import threading

    concurrent = 0
    peak = 0
    lock = threading.Lock()

    def work(_: Reporter) -> dict[str, object]:
        nonlocal concurrent, peak
        with lock:
            concurrent += 1
            peak = max(peak, concurrent)
        for _ in range(20_000):
            pass
        with lock:
            concurrent -= 1
        return {}

    jobs = [submit("test", f"j{i}", work) for i in range(6)]
    while any(j.status in {"queued", "running"} for j in jobs):
        pass
    assert peak == 1, f"{peak} jobs ran concurrently; the memory rule was broken"


# --- integration with the calculator -------------------------------------


def test_upscale_target_comes_from_the_calculator() -> None:
    """The two features are designed to compose: assess, then reach that size."""
    assessment = printsize.assess(800, 800, 10, 10, printsize.Unit.INCH, "brochure")
    assert assessment.upscale_to == (3000, 3000)
    result = images.upscale(images.load(make_image(800, 800)), "print", assessment.upscale_to)
    assert result.image.size == (3000, 3000)
    after = printsize.assess(3000, 3000, 10, 10, printsize.Unit.INCH, "brochure")
    assert after.verdict is printsize.Verdict.GOOD


# --- model-backed paths (skipped without weights) ------------------------

needs_upscaler = pytest.mark.skipif(
    not models.is_available("realesrgan-x4"), reason="upscaler weights not downloaded"
)


@needs_upscaler
def test_tiled_upscale_is_seam_free() -> None:
    """Only fully-contexted tile centres are kept, so no seams at OUT_STEP."""
    source = images.load(make_image(200, 150))
    result = images.upscale(source)
    assert result.method == "realesrgan-x4"
    assert result.image.size == (800, 600)

    array = np.asarray(result.image.convert("RGB"), dtype=np.float32)
    deltas = np.abs(np.diff(array, axis=1)).mean(axis=(0, 2))
    seams = [x for x in range(images.OUT_STEP, array.shape[1], images.OUT_STEP)]
    at_seam = float(np.mean([deltas[x - 1] for x in seams]))
    overall = float(deltas.mean())
    assert at_seam < overall * 3 + 0.5, f"seam {at_seam:.2f} vs mean {overall:.2f}"


@needs_upscaler
def test_upscale_hits_the_exact_requested_size() -> None:
    result = images.upscale(images.load(make_image(120, 100)), "print", target=(390, 325))
    assert result.image.size == (390, 325)
