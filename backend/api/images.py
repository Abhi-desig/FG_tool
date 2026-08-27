"""Phase 2 routes: inspect, assess, cut out, upscale.

Long operations return a job id immediately and are polled — the shop PC takes
minutes on a CPU upscale, and DESIGN.md requires that be visible rather than a
blocked request.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend import config, jobs, models
from backend.features import images, printsize

router = APIRouter(prefix="/api", tags=["images"])

# Spooled uploads live here until their job finishes. Named so `main.py`'s sweep
# can find stragglers left by a power cut.
UPLOAD_SCRATCH = "uploads"


def _read_upload(upload: UploadFile) -> bytes:
    """Read an upload with a hard size cap, then validate it is really an image."""
    data = upload.file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(
            413,
            f"That file is over {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB. "
            f"Export a smaller version first.",
        )
    if not data:
        raise HTTPException(400, "The uploaded file was empty.")
    return data


def _open(data: bytes) -> images.Image.Image:
    try:
        return images.load(data)
    except images.ImageError as exc:
        raise HTTPException(400, str(exc)) from exc


def _spool_upload(upload: UploadFile, stem: str) -> Path:
    """Write an upload to a scratch file, validated from its header.

    Deliberately not decoded here. `images.inspect_header` reads the size and
    format from the header, which is enough to refuse a bomb or an unsupported
    type; the pixels are only materialised when the worker reaches the job.

    The scratch directory is removed by the worker when the job ends, and by the
    sweep in `main.py` if the process dies first (SECURITY.md §5).
    """
    scratch = config.WORK_DIR / UPLOAD_SCRATCH / uuid.uuid4().hex[:12]
    suffix = Path(upload.filename or "").suffix[:8]
    destination = scratch / f"{stem}{suffix or '.bin'}"
    try:
        images.spool(upload.file, config.MAX_UPLOAD_BYTES, destination)
        images.inspect_header(destination)
    except images.ImageError as exc:
        shutil.rmtree(scratch, ignore_errors=True)
        # Over the byte cap is 413; anything else is a bad image.
        status = 413 if "MB" in str(exc) else 400
        raise HTTPException(status, str(exc)) from exc
    return destination


def _discard_scratch(*paths: Path | None) -> None:
    """Remove spooled uploads once the decode is done."""
    for path in paths:
        if path is not None:
            shutil.rmtree(path.parent, ignore_errors=True)


def _job_dir(job_id: str):
    path = config.WORK_DIR / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


# --- print size -----------------------------------------------------------


class AssessRequest(BaseModel):
    pixels_w: int = Field(gt=0, le=100_000)
    pixels_h: int = Field(gt=0, le=100_000)
    target_w: float = Field(gt=0, le=10_000)
    target_h: float = Field(gt=0, le=10_000)
    unit: Literal["mm", "cm", "inch", "feet"] = "feet"
    print_class: str = printsize.DEFAULT_CLASS


@router.get("/printsize/classes")
def print_classes() -> list[dict[str, object]]:
    return [
        {
            "key": c.key,
            "label": c.label,
            "viewing": c.viewing,
            "good_dpi": c.good_dpi,
            "min_dpi": c.min_dpi,
        }
        for c in printsize.PRINT_CLASSES
    ]


@router.post("/printsize/assess")
def assess(req: AssessRequest) -> dict[str, object]:
    """Will this image hold up at this size? The shop's most-used answer."""
    try:
        result = printsize.assess(
            req.pixels_w,
            req.pixels_h,
            req.target_w,
            req.target_h,
            req.unit,
            req.print_class,
        )
    except printsize.PrintSizeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return result.__dict__ | {
        "best_use": printsize.best_use_for(req.pixels_w, req.pixels_h, req.unit)
    }


# --- inspect --------------------------------------------------------------


@router.post("/images/inspect")
def inspect(
    file: Annotated[UploadFile, File()],
    unit: Annotated[str, Form()] = "feet",
) -> dict[str, object]:
    """Dimensions, DPI metadata, and what this image can be printed at."""
    image = _open(_read_upload(file))
    facts = images.facts(image)
    return {
        "facts": facts.__dict__,
        "best_use": printsize.best_use_for(facts.width, facts.height, unit),
        # The model runs 4x on the full source either way, so 2x and 4x cost
        # the same time — only the written file differs. Say so plainly.
        "estimated_upscale_seconds": round(
            images.estimate_seconds(facts.width, facts.height), 1
        ),
        "tiles": images.tile_count_for(facts.width, facts.height),
        # Every option says whether it is actually possible. It used to offer a
        # 4× enlargement of a 576 Mpx source — 96000×96000, 9.2 Gpx — quoting 62
        # minutes and 62,500 tiles with no indication that it cannot be built or
        # written at all (NEXT.md 2.1).
        "scale_options": [
            _scale_option(option, facts)
            for option in ("2x", "4x")
        ],
        "device": config.device_name(),
    }


def _scale_option(option: str, facts: images.ImageFacts) -> dict[str, object]:
    width, height = images.target_for(option, (facts.width, facts.height), None)
    pixels = width * height
    possible = pixels <= images.MAX_OUTPUT_PIXELS
    return {
        "scale": option,
        "width": width,
        "height": height,
        "megapixels": round(pixels / 1_000_000, 1),
        "possible": possible,
        "why_not": (
            None
            if possible
            else (
                f"{width}×{height} is {pixels / 1_000_000:.0f} MP — past the "
                f"{images.MAX_OUTPUT_PIXELS // 1_000_000} MP that can be "
                f"assembled and written as one file."
            )
        ),
    }


@router.post("/images/preview")
def preview(file: Annotated[UploadFile, File()]) -> Response:
    """Bounded thumbnail. Never hand a 200 MP file to a browser."""
    image = _open(_read_upload(file))
    return Response(images.thumbnail(image), media_type="image/png")


# --- jobs ----------------------------------------------------------------


@router.post("/images/cutout")
def start_cutout(
    file: Annotated[UploadFile, File()],
    protect: Annotated[UploadFile | None, File()] = None,
    dpi: Annotated[int, Form(ge=30, le=1200)] = 300,
) -> dict[str, object]:
    """Remove the background. Returns a job to poll.

    The upload is spooled to disk and validated from its header; the decode
    happens inside the worker. A *queued* job therefore holds a path rather than
    a decoded image, which is what stops a queue of large jobs stacking hundreds
    of megabytes of pixels on a 12 GB machine (NEXT.md 2.1).
    """
    name = file.filename or "image"
    source = _spool_upload(file, "source")
    mask_path = _spool_upload(protect, "protect") if protect is not None else None

    def work(report: jobs.Reporter) -> dict[str, object]:
        # Raising `images.ImageError` here rather than HTTPException: this runs on
        # the job thread, where the registry turns an exception into `job.error`
        # and the operator sees the message. An HTTP error would be swallowed.
        report.step("Reading the image…", 0.02)
        try:
            image = images.load_path(source)
            mask = images.load_path(mask_path) if mask_path is not None else None
            result = images.cutout(image, report, protect_mask=mask)
        finally:
            # The client's photograph does not stay on disk any longer than the
            # decode needs it — cancelled and failed jobs included.
            _discard_scratch(source, mask_path)
        report.step("Writing PNG…", 0.95)
        data, fmt, media = images.encode(result, "PNG", dpi=dpi)
        job_id = report._job.id  # noqa: SLF001 - the reporter owns this job
        out = _job_dir(job_id) / f"cutout.{fmt.lower()}"
        images.save_to(out, data)
        return {
            "file": out.name,
            "media_type": media,
            "width": result.width,
            "height": result.height,
            "dpi": dpi,
            "bytes": len(data),
        }

    job = jobs.submit("cutout", f"Cut out — {name}", work)
    return job.as_dict()


@router.post("/images/upscale")
def start_upscale(
    file: Annotated[UploadFile, File()],
    scale: Annotated[Literal["2x", "4x", "print"], Form()] = "4x",
    target_w: Annotated[int | None, Form(ge=1, le=100_000)] = None,
    target_h: Annotated[int | None, Form(ge=1, le=100_000)] = None,
    dpi: Annotated[int, Form(ge=30, le=1200)] = 300,
    fmt: Annotated[str, Form()] = "PNG",
    cmyk: Annotated[bool, Form()] = False,
) -> dict[str, object]:
    """Enlarge an image. Any input size is accepted.

    `scale` is the operator's choice: `2x`, `4x`, or `print` to hit exactly the
    pixel size `/printsize/assess` says the job needs.
    """
    name = file.filename or "image"
    target = (target_w, target_h) if target_w and target_h else None
    if scale == "print" and target is None:
        raise HTTPException(400, "Choosing 'print' needs a target size to aim for.")

    # Spooled, not held in memory — see `start_cutout`.
    source = _spool_upload(file, "source")

    def work(report: jobs.Reporter) -> dict[str, object]:
        report.step("Reading the image…", 0.02)
        try:
            image = images.load_path(source)
            result = images.upscale(image, scale, target, report)
        finally:
            _discard_scratch(source)
        report.step("Writing file…", 0.96)
        data, out_fmt, media = images.encode(result.image, fmt, dpi=dpi, cmyk=cmyk)
        job_id = report._job.id  # noqa: SLF001
        out = _job_dir(job_id) / f"upscaled.{out_fmt.lower()}"
        images.save_to(out, data)
        return {
            "file": out.name,
            "media_type": media,
            "method": result.method,
            "scale": scale,
            "tiles": result.tiles,
            "note": result.note,
            "width": result.image.width,
            "height": result.image.height,
            "dpi": dpi,
            "bytes": len(data),
        }

    job = jobs.submit("upscale", f"Enlarge — {name}", work)
    return job.as_dict()


@router.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, object]:
    job = jobs.registry.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job.")
    return job.as_dict()


@router.get("/jobs")
def job_list(limit: int = 25) -> list[dict[str, object]]:
    return [j.as_dict() for j in jobs.registry.recent(limit)]


@router.post("/jobs/{job_id}/cancel")
def job_cancel(job_id: str) -> dict[str, object]:
    job = jobs.registry.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job.")
    job.cancel()
    return job.as_dict()


@router.get("/jobs/{job_id}/result")
def job_result(job_id: str) -> FileResponse:
    """Download a finished job's output."""
    job = jobs.registry.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job.")
    if job.status != "done" or not job.result:
        raise HTTPException(409, f"That job is {job.status}, not finished.")
    if job.files_deleted:
        raise HTTPException(
            410,
            f"That result was deleted {jobs.RESULT_TTL_SECONDS // 60} minutes after "
            f"it finished, so client artwork does not sit on this machine. "
            f"Run it again.",
        )

    # The filename comes from our own job result, never from the client.
    path = (config.WORK_DIR / job_id / str(job.result["file"])).resolve()
    if not path.is_relative_to(config.WORK_DIR.resolve()) or not path.exists():
        raise HTTPException(404, "The result file is no longer available.")
    return FileResponse(
        path,
        media_type=str(job.result.get("media_type", "application/octet-stream")),
        filename=path.name,
    )


@router.get("/jobs/{job_id}/preview")
def job_preview(job_id: str) -> Response:
    job = jobs.registry.get(job_id)
    if job is None or job.status != "done" or not job.result:
        raise HTTPException(404, "No finished result to preview.")
    path = (config.WORK_DIR / job_id / str(job.result["file"])).resolve()
    if not path.is_relative_to(config.WORK_DIR.resolve()) or not path.exists():
        raise HTTPException(404, "The result file is no longer available.")
    with images.Image.open(path) as opened:
        opened.load()
        return Response(images.thumbnail(opened), media_type="image/png")


# --- settings ------------------------------------------------------------


@router.get("/settings/models")
def settings_models() -> dict[str, object]:
    parent = config.MODELS_DIR.parent
    free = shutil.disk_usage(parent).free if parent.exists() else 0
    return {
        "models_dir": str(config.MODELS_DIR),
        "free_gb": round(free / 1_073_741_824, 1),
        "device": config.device_name(),
        "providers": config.providers(),
        "models": models.describe(),
    }
