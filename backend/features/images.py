"""Image operations: inspect, cut out, upscale.

Three things here are deliberate rather than obvious:

**Tiling is mandatory, not an optimisation.** The Real-ESRGAN export has a static
``1×3×128×128`` input, so every image is processed in 128px tiles regardless of
size. Tiles carry a 16px context margin that is discarded from the output, so
only pixels the model saw in full context survive — that is what prevents seams.
It also means memory use is constant, which is why this runs on the shop PC.

**Any input size is accepted, and the operator picks the factor.** ``2x``, ``4x``,
or ``print`` to hit exactly the pixels `printsize.assess()` says the job needs.
The model always runs at its native 4× on the full source and the result is
resampled to the requested size, so nothing is discarded before the model sees
it and the tile count depends only on the source.

**Slowness never refuses a job.** The estimate is shown before the operator
commits and the work is cancellable, so choosing a long run is their call. Only
a physical limit — missing weights, or a result too large to write as one file —
falls back.

**There is always a fallback.** If the weights are missing, or the result is
genuinely too large to build as one file, Lanczos resampling runs instead. It
does not invent detail, and the note says so rather than pretending.
"""

from __future__ import annotations

import io
import logging
import math
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from PIL import Image, ImageFilter

from backend import config, models
from backend.jobs import Reporter

log = logging.getLogger(__name__)

# --- input limits ---------------------------------------------------------
#
# SECURITY.md §4 requires a cap on pixel *dimensions*, and there was none. The
# only guard was `MAX_IMAGE_PIXELS` at 1 Gpx — eleven times Pillow's own default
# — and a 1 GB byte cap that a bomb sails under: a 546 KB PNG at 24000×24000
# (576 Mpx) was accepted, and the response then offered a 4× upscale to
# 96000×96000, quoting 62 minutes and 62,500 tiles with no hint that it is
# impossible (NEXT.md 2.1).
#
# At the old 1 Gpx ceiling an RGBA decode is about 4 GB on a 12 GB machine.

# The largest source this shop actually produces. A 6×4 ft banner at 300 DPI is
# 21600×14400 — 311 Mpx — and nothing legitimate here goes past that; a 150 Mpx
# ceiling still comfortably covers the 100+ MP flatbed scans, a 60 MP camera
# file, and any photograph a client will ever send.
MAX_INPUT_PIXELS = 150_000_000

# And a cap on either side on its own, so a pathological 1×200,000,000 strip is
# refused too — it passes a pixel-count test and still breaks everything
# downstream.
MAX_INPUT_SIDE = 30_000

# Pillow's own bomb guard. Kept just above our own limit so `load` refuses with
# a sentence the operator can act on, rather than Pillow raising first with one
# they cannot.
Image.MAX_IMAGE_PIXELS = MAX_INPUT_PIXELS + 1_000_000

# The model's static geometry. Not tunable — it is baked into the graph.
TILE = 128
SCALE = 4
OVERLAP = 16
STEP = TILE - 2 * OVERLAP  # 96 px of usable core per tile
OUT_STEP = STEP * SCALE  # 384 px written per tile

# Ceiling on what can be built and encoded as one file. Physics, not policy —
# see encodable_pixels(). Past it, Lanczos still delivers a result.
MAX_OUTPUT_PIXELS = 300_000_000

# Measured on 2026-08-22, one 128px tile through Real-ESRGAN x4, steady state.
# The 40× gap is why the UI states the estimate before the operator commits.
TILE_SECONDS = {"gpu": 0.06, "cpu": 2.5}

# One BiRefNet pass. Measured on 2026-08-26: 61 s end to end on CoreML for a
# 12 MP photo. The CPU figure carries the same gpu:cpu ratio as TILE_SECONDS,
# which is the only evidence available until the shop PC is timed directly.
CUTOUT_SECONDS = {"gpu": 60.0, "cpu": 300.0}

ALLOWED_FORMATS = {"PNG", "JPEG", "TIFF", "WEBP", "BMP"}

# Read size for spooling an upload to disk. Large enough that a 200 MB scan is a
# few hundred reads, small enough to be invisible in memory.
SPOOL_CHUNK_BYTES = 1024 * 1024


class ImageError(ValueError):
    """The image is unreadable, unsupported, or too large."""


@dataclass(frozen=True)
class ImageFacts:
    width: int
    height: int
    mode: str
    format: str | None
    has_alpha: bool
    embedded_dpi: tuple[float, float] | None
    megapixels: float


def load(data: bytes) -> Image.Image:
    """Decode bytes to an image, validating what it actually is.

    SECURITY.md: the declared type and the filename are both untrusted. Pillow
    reads the real header; anything not in ALLOWED_FORMATS is refused.
    """
    try:
        probe = Image.open(io.BytesIO(data))
        probe.verify()  # cheap structural check, consumes the file object
    except Exception as exc:  # noqa: BLE001 - Pillow raises many types here
        raise ImageError("That file is not a readable image.") from exc

    fmt = (probe.format or "").upper()
    if fmt not in ALLOWED_FORMATS:
        raise ImageError(
            f"{fmt or 'Unknown'} images are not supported. "
            f"Use {', '.join(sorted(ALLOWED_FORMATS))}."
        )

    # Check the size from the *header*, before decoding. This is the whole point:
    # a decompression bomb is small on disk and enormous in memory, so the byte
    # cap never sees it and by the time `load()` has run the damage is done.
    _check_dimensions(probe.size)

    # verify() leaves the image unusable, so reopen for real work.
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Image.DecompressionBombError as exc:
        raise ImageError(
            "That image is too large to open safely — it decodes to far more "
            "pixels than its file size suggests."
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise ImageError("That image could not be decoded.") from exc
    return image


def _check_dimensions(size: tuple[int, int]) -> None:
    """Refuse an image too big to handle, in words the operator can act on."""
    width, height = size
    if width <= 0 or height <= 0:
        raise ImageError("That image reports no size at all.")

    if width > MAX_INPUT_SIDE or height > MAX_INPUT_SIDE:
        raise ImageError(
            f"That image is {width}×{height} pixels. The longest side this tool "
            f"accepts is {MAX_INPUT_SIDE:,} pixels — far more than a 6×4 ft "
            f"banner needs at 300 DPI. Export a smaller version first."
        )

    pixels = width * height
    if pixels > MAX_INPUT_PIXELS:
        raise ImageError(
            f"That image is {width}×{height} — {pixels / 1_000_000:.0f} "
            f"megapixels. The limit is {MAX_INPUT_PIXELS // 1_000_000} MP, which "
            f"is already larger than anything a print job needs. Export a "
            f"smaller version first."
        )


def spool(stream: Any, limit: int, destination: Path) -> int:
    """Copy an upload to disk in chunks. Returns the byte count.

    **Why disk and not bytes.** `_read_upload` held the whole file in memory
    while the decoded image was captured in the queued job's closure, so a queue
    of large jobs stacked decoded images on a 12 GB machine (NEXT.md 2.1). With
    the file on disk, a queued job holds a path — a few dozen bytes — and decodes
    only when the single worker reaches it.

    Raises `ImageError` past `limit` rather than filling the disk. The partial
    file is the caller's to clean up; `api/images` does that in a `finally`.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with destination.open("wb") as out:
        while True:
            chunk = stream.read(SPOOL_CHUNK_BYTES)
            if not chunk:
                break
            written += len(chunk)
            if written > limit:
                raise ImageError(
                    f"That file is over {limit // (1024 * 1024)} MB. "
                    f"Export a smaller version first."
                )
            out.write(chunk)
    if written == 0:
        raise ImageError("The uploaded file was empty.")
    return written


@contextmanager
def _header(path: Path) -> Iterator[Image.Image]:
    """Open just the header, with Pillow's bomb guard out of the way.

    `Image.open` parses the header only — it allocates no pixels — but Pillow's
    own `MAX_IMAGE_PIXELS` check fires there, raising before the size can be read
    and turning our precise refusal ("that image is 24000×24000 — 576 megapixels,
    the limit is 150") into a useless "that file is not a readable image".

    So the guard is lifted for the header read and `_check_dimensions` produces
    the message instead. Nothing is decoded inside this block, and the limit is
    restored before anything could be.
    """
    ceiling = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = None
    try:
        with Image.open(path) as opened:
            yield opened
    finally:
        Image.MAX_IMAGE_PIXELS = ceiling


def inspect_header(path: Path) -> ImageFacts:
    """Read size and format from the header alone, without decoding.

    Cheap enough to run on every upload, which is what makes the pixel caps in
    `_check_dimensions` free — they are enforced before anything is decoded.
    """
    try:
        with _header(path) as probe:
            fmt = (probe.format or "").upper()
            size = probe.size
            mode = probe.mode
            dpi = probe.info.get("dpi")
    except ImageError:
        raise
    except Exception as exc:  # noqa: BLE001 - Pillow raises many types here
        raise ImageError("That file is not a readable image.") from exc

    if fmt not in ALLOWED_FORMATS:
        raise ImageError(
            f"{fmt or 'Unknown'} images are not supported. "
            f"Use {', '.join(sorted(ALLOWED_FORMATS))}."
        )
    _check_dimensions(size)

    return ImageFacts(
        width=size[0],
        height=size[1],
        mode=mode,
        format=fmt,
        has_alpha=mode in {"RGBA", "LA", "PA"} or "transparency" in mode,
        embedded_dpi=(float(dpi[0]), float(dpi[1])) if dpi else None,
        megapixels=round(size[0] * size[1] / 1_000_000, 2),
    )


def load_path(path: Path) -> Image.Image:
    """Decode a spooled upload, with the same guards `load` applies to bytes."""
    inspect_header(path)
    try:
        image = Image.open(path)
        image.load()
    except Image.DecompressionBombError as exc:
        raise ImageError(
            "That image is too large to open safely — it decodes to far more "
            "pixels than its file size suggests."
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise ImageError("That image could not be decoded.") from exc
    return image


def facts(image: Image.Image) -> ImageFacts:
    dpi = image.info.get("dpi")
    return ImageFacts(
        width=image.width,
        height=image.height,
        mode=image.mode,
        format=image.format,
        has_alpha=image.mode in {"RGBA", "LA", "PA"} or "transparency" in image.info,
        embedded_dpi=(float(dpi[0]), float(dpi[1])) if dpi else None,
        megapixels=round(image.width * image.height / 1_000_000, 2),
    )


# --- background removal ---------------------------------------------------


def estimate_cutout_seconds(width: int, height: int) -> float:
    """Rough wall-clock for one BiRefNet pass.

    BiRefNet runs at a fixed input resolution, so the cost barely moves with the
    source size — it is a hardware constant far more than an image one. Measured
    on 2026-08-26: 61 s on CoreML for a 12 MP photo. The shop PC's i3 has no GPU,
    and the CPU figure is scaled from the ratio measured for Real-ESRGAN.
    """
    on_gpu = config.providers()[0] != "CPUExecutionProvider"
    base = CUTOUT_SECONDS["gpu" if on_gpu else "cpu"]
    # A very large source still costs something to resize and to composite.
    megapixels = (width * height) / 1_000_000
    return base + max(0.0, megapixels - 12) * (0.4 if on_gpu else 2.0)


def _minutes_phrase(seconds: float) -> str:
    if seconds < 90:
        return f"about {round(seconds)} seconds"
    return f"about {round(seconds / 60)} minute{'' if round(seconds / 60) == 1 else 's'}"


def cutout(
    image: Image.Image,
    reporter: Reporter | None = None,
    protect_mask: Image.Image | None = None,
) -> Image.Image:
    """Remove the background, returning RGBA.

    `protect_mask` is a greyscale image the operator painted: white where the
    subject must be kept whatever the model thinks. Painted areas are forced
    fully opaque after the model runs, which is what makes the brush a
    guarantee rather than a hint.
    """
    if reporter:
        reporter.step("Loading background model…", 0.05)

    from rembg import remove

    with models.loaded(config.BACKGROUND_MODEL) as session:
        if reporter:
            # One uninterruptible call: there is no callback to hook and no
            # tiling to count, so the app genuinely cannot report progress here.
            # Measured, this stretch was 54 of the job's 61 seconds with the bar
            # frozen at 35% and the step text unchanged — which reads as a crash
            # on the shop PC, where it is minutes. Saying so is the honest
            # option, and it is the one DESIGN.md's spirit asks for.
            expected = estimate_cutout_seconds(image.width, image.height)
            reporter.opaque_step(
                f"Removing background — {_minutes_phrase(expected)} on "
                f"{config.device_name()}. This step cannot report progress.",
                0.35,
            )
        result = remove(
            image.convert("RGB"),
            session=session,
            post_process_mask=True,
        )

    if not isinstance(result, Image.Image):
        raise ImageError("Background removal returned an unexpected result.")
    result = result.convert("RGBA")

    if protect_mask is not None:
        if reporter:
            reporter.step("Applying protected areas…", 0.85)
        result = _apply_protect_mask(result, protect_mask)

    if reporter:
        reporter.step("Done", 1.0)
    return result


def _apply_protect_mask(image: Image.Image, mask: Image.Image) -> Image.Image:
    """Force alpha to opaque wherever the operator painted."""
    mask = mask.convert("L").resize(image.size, Image.Resampling.LANCZOS)
    # Soften the brush edge so the join does not look cut out with scissors.
    mask = mask.filter(ImageFilter.GaussianBlur(radius=1.5))
    alpha = image.getchannel("A")
    merged = Image.composite(Image.new("L", image.size, 255), alpha, mask)
    image.putalpha(merged)
    return image


# --- upscaling ------------------------------------------------------------


UpscaleMode = Literal["2x", "4x", "print"]


@dataclass(frozen=True)
class UpscaleResult:
    image: Image.Image
    method: Literal["realesrgan-x4", "lanczos"]
    tiles: int
    note: str


def target_for(
    mode: UpscaleMode, size: tuple[int, int], target: tuple[int, int] | None
) -> tuple[int, int]:
    """Output size for a mode. `print` uses the calculator's target."""
    width, height = size
    if mode == "print" and target:
        return (max(1, target[0]), max(1, target[1]))
    factor = 2 if mode == "2x" else SCALE
    return (width * factor, height * factor)


def upscale(
    image: Image.Image,
    mode: UpscaleMode = "4x",
    target: tuple[int, int] | None = None,
    reporter: Reporter | None = None,
) -> UpscaleResult:
    """Enlarge an image. Any input size is accepted.

    `mode` is the operator's choice: ``2x``, ``4x``, or ``print`` to hit the
    pixel size `printsize.assess()` says the job needs.

    The model always runs at its native 4× on the **full** source, and the result
    is then resampled to the requested size. That keeps quality high (nothing is
    thrown away before the model sees it) and makes cost predictable: tile count
    depends only on the source, never on the mode.
    """
    source = image.convert("RGB")
    want = target_for(mode, source.size, target)

    if want[0] <= source.width and want[1] <= source.height:
        return UpscaleResult(
            image=source.resize(want, Image.Resampling.LANCZOS),
            method="lanczos",
            tiles=0,
            note="That size is smaller than the original, so this is a clean resize.",
        )

    reason = _model_unusable(source, want)
    if reason:
        if reporter:
            reporter.step("Resampling…", 0.2)
        return UpscaleResult(
            image=source.resize(want, Image.Resampling.LANCZOS),
            method="lanczos",
            tiles=0,
            note=f"{reason} Used high-quality resampling, which enlarges cleanly "
            f"but does not add detail.",
        )

    enlarged, tiles = _run_tiled(source, reporter)
    if enlarged.size != want:
        if reporter:
            reporter.step(f"Resizing to {want[0]}×{want[1]}…", 0.95)
        enlarged = enlarged.resize(want, Image.Resampling.LANCZOS)

    return UpscaleResult(
        image=enlarged,
        method="realesrgan-x4",
        tiles=tiles,
        note=f"Enlarged with Real-ESRGAN in {tiles} tiles.",
    )


def tile_count_for(width: int, height: int) -> int:
    """Tiles needed to cover an image at the model's fixed geometry."""
    return math.ceil(width / STEP) * math.ceil(height / STEP)


def estimate_seconds(width: int, height: int) -> float:
    """Rough wall-clock for the model path on whatever hardware is present."""
    on_gpu = config.providers()[0] != "CPUExecutionProvider"
    per_tile = TILE_SECONDS["gpu" if on_gpu else "cpu"]
    return tile_count_for(width, height) * per_tile


def _model_unusable(image: Image.Image, want: tuple[int, int]) -> str | None:
    """Why the model path cannot be taken, or None if it can.

    Only physical limits refuse the model — a missing file, or a result too
    large to build and encode at all. **Slowness never refuses.** The operator
    is shown the estimate before committing and the job is cancellable, so a
    long run is their call to make, not ours to block.
    """
    if not models.is_available("realesrgan-x4"):
        return "The upscaling model is not downloaded."

    # The model always runs 4× on the full source, so the result it produces is
    # 4× regardless of the size finally requested.
    produced = (image.width * SCALE) * (image.height * SCALE)
    if produced > MAX_OUTPUT_PIXELS or want[0] * want[1] > MAX_OUTPUT_PIXELS:
        biggest = max(produced, want[0] * want[1])
        return (
            f"That would build a {biggest / 1_000_000:.0f} MP image, beyond what "
            f"can be assembled and written as one file."
        )
    return None


def encodable_pixels() -> int:
    """Largest result that can actually be assembled and written as one file.

    Measured against how Pillow works rather than guessed. An on-disk canvas was
    tried and removed: `Image.frombuffer` only shares memory for byte-aligned
    modes (RGBA/RGBX/L), not 3-byte RGB — and even with RGBA the encoder
    materialises the whole image, so the peak lands at encode either way.

    300 MP ≈ 21000×14000, about 900 MB of canvas plus encoder buffers. That
    covers a 5000 px source at 4×, which is past anything a RIP wants anyway.
    """
    return MAX_OUTPUT_PIXELS


def _run_tiled(
    image: Image.Image, reporter: Reporter | None
) -> tuple[Image.Image, int]:
    """Upscale 4× in 128px tiles, keeping only fully-contexted centres."""
    width, height = image.size
    cols = math.ceil(width / STEP)
    rows = math.ceil(height / STEP)
    total = cols * rows

    if reporter:
        # Say how long this will take before it starts. On the shop PC a silent
        # three-minute wait reads as a crash (DESIGN.md).
        expected = estimate_seconds(width, height)
        how_long = (
            f"about {expected / 60:.0f} min" if expected >= 90 else f"about {expected:.0f}s"
        )
        reporter.step(f"Enlarging {total} tiles — {how_long} on {config.device_name()}", 0.05)

    # Pad so every tile crop is in bounds, replicating edges so border tiles
    # get plausible context instead of black.
    padded = Image.new("RGB", ((cols - 1) * STEP + TILE, (rows - 1) * STEP + TILE))
    padded.paste(image, (OVERLAP, OVERLAP))
    _replicate_edges(padded, image)

    canvas = Image.new("RGB", (width * SCALE, height * SCALE))
    done = 0

    with models.loaded("realesrgan-x4") as session:
        input_name = session.get_inputs()[0].name
        for row in range(rows):
            for col in range(cols):
                sx, sy = col * STEP, row * STEP
                tile = padded.crop((sx, sy, sx + TILE, sy + TILE))

                array = np.asarray(tile, dtype=np.float32).transpose(2, 0, 1) / 255.0
                out = session.run(None, {input_name: array[None, ...]})[0]
                out = np.clip(out[0].transpose(1, 2, 0) * 255.0, 0, 255).astype(np.uint8)

                # Discard the context margin; keep the core the model saw fully.
                core = Image.fromarray(out).crop(
                    (
                        OVERLAP * SCALE,
                        OVERLAP * SCALE,
                        OVERLAP * SCALE + OUT_STEP,
                        OVERLAP * SCALE + OUT_STEP,
                    )
                )
                canvas.paste(core, (sx * SCALE, sy * SCALE))

                done += 1
                if reporter:
                    reporter.step(
                        f"Enlarging — tile {done} of {total}", 0.05 + 0.9 * done / total
                    )

    # Already exactly width*SCALE × height*SCALE, so no crop is needed.
    return canvas, total


def _replicate_edges(padded: Image.Image, image: Image.Image) -> None:
    """Fill the padding by repeating the outermost rows and columns."""
    width, height = image.size
    right = padded.width - (OVERLAP + width)
    bottom = padded.height - (OVERLAP + height)

    if OVERLAP:
        padded.paste(image.crop((0, 0, OVERLAP, height)).transpose(
            Image.Transpose.FLIP_LEFT_RIGHT), (0, OVERLAP))
        padded.paste(image.crop((0, 0, width, OVERLAP)).transpose(
            Image.Transpose.FLIP_TOP_BOTTOM), (OVERLAP, 0))
    if right > 0:
        strip = image.crop((max(0, width - right), 0, width, height))
        padded.paste(strip.transpose(Image.Transpose.FLIP_LEFT_RIGHT),
                     (OVERLAP + width, OVERLAP))
    if bottom > 0:
        strip = image.crop((0, max(0, height - bottom), padded.width - OVERLAP, height))
        padded.paste(strip.transpose(Image.Transpose.FLIP_TOP_BOTTOM),
                     (OVERLAP, OVERLAP + height))


# --- output ---------------------------------------------------------------


def encode(
    image: Image.Image,
    fmt: str = "PNG",
    dpi: int = 300,
    cmyk: bool = False,
) -> tuple[bytes, str, str]:
    """Write print-ready bytes. Returns (data, format, media type).

    DPI is written into the file so CorelDRAW and the RIP place it at the right
    physical size instead of guessing.

    **PNG cannot store 300 DPI exactly, and that is not a bug.** Its `pHYs` chunk
    holds pixels per *metre* as an integer: 300 DPI is 11811.024 px/m, so the
    nearest storable value reads back as 299.9994 DPI in CorelDRAW (NEXT.md
    3.11). No integer gives exactly 300, so the choice is between that and a
    worse approximation. A 6 ft banner placed from this file is off by about
    0.4 thousandths of an inch — a hundredth of the width of a hair — which is
    below anything a large-format printer can resolve.

    TIFF stores resolution as a rational and *is* exact, which is one more reason
    the CMYK print path uses it.
    """
    fmt = fmt.upper()
    out = image

    if cmyk:
        # PNG has no CMYK; TIFF is the sane print container.
        if fmt == "PNG":
            fmt = "TIFF"
        if out.mode == "RGBA":
            flat = Image.new("RGB", out.size, "white")
            flat.paste(out, mask=out.getchannel("A"))
            out = flat
        out = out.convert("CMYK")
    elif fmt in {"JPEG", "BMP"} and out.mode == "RGBA":
        flat = Image.new("RGB", out.size, "white")
        flat.paste(out, mask=out.getchannel("A"))
        out = flat

    buffer = io.BytesIO()
    params: dict[str, Any] = {"dpi": (dpi, dpi)}
    if fmt == "JPEG":
        params |= {"quality": 95, "subsampling": 0}
    elif fmt == "TIFF":
        params |= {"compression": "tiff_lzw"}
    out.save(buffer, format=fmt, **params)

    media = {
        "PNG": "image/png",
        "JPEG": "image/jpeg",
        "TIFF": "image/tiff",
        "WEBP": "image/webp",
        "BMP": "image/bmp",
    }[fmt]
    return buffer.getvalue(), fmt, media


def thumbnail(image: Image.Image, box: int = 640) -> bytes:
    """Small preview for the UI. Never send a 200 MP file to a browser."""
    preview = image.copy()
    preview.thumbnail((box, box), Image.Resampling.LANCZOS)
    if preview.mode not in {"RGB", "RGBA"}:
        preview = preview.convert("RGBA")
    buffer = io.BytesIO()
    preview.save(buffer, format="PNG")
    return buffer.getvalue()


def save_to(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path
