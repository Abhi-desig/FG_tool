"""Focus Toolkit server.

Serves the built React app and the feature API from one local process. Start it
with `uv run python -m backend.main`, or from the desktop shortcut.
"""

from __future__ import annotations

import shutil
import threading
import webbrowser
from contextlib import asynccontextmanager
from importlib import import_module
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from backend import config, guard, jobs
from backend.features import fonts

MAX_INPUT_CHARS = 20_000

# Bounds on a preferences write. Generous — these are model names and colour
# choices — but present, which they were not.
MAX_PREFERENCE_KEYS = 100
MAX_PREFERENCE_KEY_CHARS = 64
MAX_PREFERENCE_VALUE_CHARS = 500

# How often the temp-file sweep runs. Well under the TTL so nothing is much
# overdue, and cheap enough to be invisible on an idle machine.
SWEEP_INTERVAL_SECONDS = 60


def sweep_expired_jobs() -> int:
    """Delete the files of jobs past their TTL. Returns how many were removed.

    SECURITY.md §5 promised temp files were cleaned up after each job. They were
    not — `WORK_DIR` was wiped at startup and clean shutdown only, so a session's
    client photos and spreadsheets accumulated, and a power cut left them there
    until the next launch (NEXT.md 2.4).

    The job stays in the history with `files_deleted` set, so the operator is
    told the result has expired rather than handed a Download button that 404s.
    """
    removed = 0
    for job in jobs.registry.expired():
        directory = config.WORK_DIR / job.id
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)
        job.files_deleted = True
        removed += 1
    return removed


def _start_sweeper(stop: threading.Event) -> threading.Thread:
    """Run the sweep on a timer for as long as the server is up."""

    def loop() -> None:
        # Checked far more often than the TTL so a file is never much overdue,
        # and cheaply enough that it costs nothing on an idle shop PC.
        while not stop.wait(SWEEP_INTERVAL_SECONDS):
            try:
                sweep_expired_jobs()
            except OSError as exc:
                # A locked or vanished file must not kill the sweeper.
                print(f"  sweep warning: {exc}")

    thread = threading.Thread(target=loop, name="sweep", daemon=True)
    thread.start()
    return thread


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Fail loudly at startup if something is broken, not on first use."""
    report = fonts.map_report()
    print(f"  font map: {report['unicode_entries']} letters ({report['font']})")
    for warning in report["warnings"]:  # type: ignore[union-attr]
        print(f"  map warning: {warning}")

    # Everything below needs an extra. A base install serves the font converter
    # and says plainly what else is missing — see `_mount`.
    try:
        from backend import db, models
        from backend.features import prompts, styles

        db.init()
        prompts.seed_defaults()
        styles.seed_defaults()
        print(f"  device:   {config.device_name()}")
        for model in models.describe():
            state = "ready" if model["available"] else "not downloaded"
            print(f"  model:    {model['key']} — {state}")
    except ImportError as exc:
        print(f"  base install: {exc.name} missing — font converter only")

    for name, why in _missing_features.items():
        print(f"  feature:  {name} — {why}")

    # SECURITY.md: client artwork must not accumulate between sessions.
    if config.WORK_DIR.exists():
        shutil.rmtree(config.WORK_DIR, ignore_errors=True)
    config.WORK_DIR.mkdir(parents=True, exist_ok=True)

    stop_sweeping = threading.Event()
    _start_sweeper(stop_sweeping)
    print(
        f"  temp files: deleted {jobs.RESULT_TTL_SECONDS // 60} minutes after a job "
        f"finishes"
    )

    print(f"\n  Focus Toolkit → http://{config.HOST}:{config.PORT}\n")
    try:
        yield
    finally:
        stop_sweeping.set()
        jobs.registry.shutdown()
        shutil.rmtree(config.WORK_DIR, ignore_errors=True)


app = FastAPI(title="Focus Toolkit", version="0.1.0", lifespan=lifespan)

# The 127.0.0.1 bind stops the shop Wi-Fi. It does nothing about a page already
# open in the operator's own browser: a multipart POST is a CORS simple request,
# so any site could fire one at this port and make it spend money or burn the
# CPU. See backend/guard.py and SECURITY.md §1.
app.add_middleware(guard.LocalOnlyMiddleware, port=config.PORT)


# --- feature routers ------------------------------------------------------
#
# Mounted one at a time, and a missing dependency disables that feature rather
# than the whole app.
#
# `pyproject.toml` says the extras exist "so the font converter never pulls down
# 3.5 GB". That claim did not hold: this module imported every router at the top,
# so `api.images` → `features.images` → numpy and Pillow, `api.excel` → openpyxl,
# and `db` → `crypto` → cryptography were all required before the server would
# start at all (NEXT.md 2.6). On a 112 GB SSD that is exactly the cost the extras
# were meant to avoid.
#
# Feature 1 — the font converter, which is the one the shop uses every day — now
# genuinely runs on the base install.

# Which extra provides each feature, for the message the operator sees.
FEATURE_EXTRAS: dict[str, str] = {
    "images": "images",
    "excel": "translate",
    "posters": "images",
    "styles": "images",
    "ai": "ai",
}

_missing_features: dict[str, str] = {}


def _mount(name: str) -> None:
    """Import and mount one feature router, or record why it is unavailable."""
    try:
        module = import_module(f"backend.api.{name}")
    except ImportError as exc:
        extra = FEATURE_EXTRAS.get(name, name)
        _missing_features[name] = (
            f"not installed — run `uv sync --extra {extra}` ({exc.name} is missing)"
        )
        return
    app.include_router(module.router)


for _feature in ("images", "excel", "posters", "styles", "ai"):
    _mount(_feature)


@app.get("/api/features")
def features() -> dict[str, object]:
    """Which features this install can actually serve.

    So the UI can say "run `uv sync --extra images`" instead of showing a screen
    whose every button 404s.
    """
    return {
        "available": [
            name
            for name in ("fonts", "images", "excel", "posters", "styles", "ai")
            if name == "fonts" or name not in _missing_features
        ],
        "unavailable": _missing_features,
    }


class ConvertRequest(BaseModel):
    text: str = Field(max_length=MAX_INPUT_CHARS)
    direction: Literal["to_ascii", "to_unicode"] = "to_ascii"
    font: str = fonts.DEFAULT_FONT


class ConvertResponse(BaseModel):
    result: str
    direction: str
    font: str
    chars_in: int
    chars_out: int
    # Characters the print font has no glyph for — chiefly emoji, which arrive
    # constantly because WhatsApp is the primary input (NEXT.md 3.4).
    unconvertible: list[dict[str, object]] = Field(default_factory=list)


@app.post("/api/fonts/convert", response_model=ConvertResponse)
def convert(req: ConvertRequest) -> ConvertResponse:
    """Convert between Unicode Malayalam and a legacy ASCII print font.

    `to_ascii` is the direction the shop needs: WhatsApp → CorelDRAW.
    """
    convert_fn = (
        fonts.unicode_to_ascii if req.direction == "to_ascii" else fonts.ascii_to_unicode
    )
    try:
        result = convert_fn(req.text, req.font)
    except fonts.MapError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Only meaningful going into the print font; the other direction is
    # producing Unicode, which can represent anything.
    problems = (
        fonts.unconvertible(req.text, req.font)
        if req.direction == "to_ascii"
        else []
    )

    return ConvertResponse(
        result=result,
        unconvertible=problems,
        direction=req.direction,
        font=req.font,
        chars_in=len(req.text),
        chars_out=len(result),
    )


@app.get("/api/fonts/info")
def fonts_info() -> dict[str, object]:
    """Map diagnostics — surfaced in Settings and useful for a bad conversion."""
    return fonts.map_report()


class PreferencesIn(BaseModel):
    """A preferences write.

    CLAUDE.md and SECURITY.md §4 both require a Pydantic body on every route;
    this one took a bare `dict[str, str]` (NEXT.md 3.14). `set_preferences` did
    already refuse unknown keys, so this was a convention break rather than a
    hole — but the bounds below are new, and a preference value long enough to
    matter had nothing stopping it before.
    """

    updates: dict[str, str] = Field(max_length=MAX_PREFERENCE_KEYS)

    @model_validator(mode="before")
    @classmethod
    def _accept_a_bare_map(cls, data: object) -> object:
        """Take `{"theme": "dark"}` as well as `{"updates": {"theme": "dark"}}`.

        The UI has always sent the bare form. Wrapping it would be a nicer shape
        and is not worth a breaking change to a route the operator's browser
        calls on every settings edit — including from a `dist` bundle that may be
        older than this server.
        """
        if isinstance(data, dict) and "updates" not in data:
            return {"updates": data}
        return data

    @field_validator("updates")
    @classmethod
    def _bounded(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            if len(key) > MAX_PREFERENCE_KEY_CHARS:
                raise ValueError(f"preference name too long: {key[:40]}…")
            if len(item) > MAX_PREFERENCE_VALUE_CHARS:
                raise ValueError(f"value for {key} is too long")
        return value


@app.get("/api/settings/preferences")
def get_preferences() -> dict[str, object]:
    from backend import db

    return db.get_preferences()


@app.put("/api/settings/preferences")
def put_preferences(body: PreferencesIn) -> dict[str, object]:
    """Write preferences. Unknown keys are refused, never silently stored.

    Accepts either `{"updates": {...}}` or the bare `{...}` the UI has always
    sent.
    """
    from backend import db

    try:
        return db.set_preferences(body.updates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "device": config.device_name(),
        "phase": 5,
        "active_jobs": jobs.registry.active_count(),
    }


# The built React app. Mounted last so it never shadows /api routes.
if config.FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=config.FRONTEND_DIST, html=True), name="ui")
else:

    @app.get("/")
    def no_ui() -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "error": "Frontend not built.",
                "fix": "cd frontend && npm install && npm run build",
            },
        )


def main() -> None:
    import uvicorn

    if config.OPEN_BROWSER:
        url = f"http://{config.HOST}:{config.PORT}"
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
