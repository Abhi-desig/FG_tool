"""Focus Toolkit server.

Serves the built React app and the feature API from one local process. Start it
with `uv run python -m backend.main`, or from the desktop shortcut.
"""

from __future__ import annotations

import shutil
import threading
import webbrowser
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import config, db, jobs, models
from backend.api import ai as ai_api
from backend.api import excel as excel_api
from backend.api import images as images_api
from backend.api import posters as posters_api
from backend.api import styles as styles_api
from backend.features import fonts, prompts, styles

MAX_INPUT_CHARS = 20_000


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Fail loudly at startup if something is broken, not on first use."""
    report = fonts.map_report()
    print(f"  font map: {report['unicode_entries']} letters ({report['font']})")
    for warning in report["warnings"]:  # type: ignore[union-attr]
        print(f"  map warning: {warning}")

    db.init()
    prompts.seed_defaults()
    styles.seed_defaults()
    print(f"  device:   {config.device_name()}")
    for model in models.describe():
        state = "ready" if model["available"] else "not downloaded"
        print(f"  model:    {model['key']} — {state}")

    # SECURITY.md: client artwork must not accumulate between sessions.
    if config.WORK_DIR.exists():
        shutil.rmtree(config.WORK_DIR, ignore_errors=True)
    config.WORK_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n  Focus Toolkit → http://{config.HOST}:{config.PORT}\n")
    try:
        yield
    finally:
        jobs.registry.shutdown()
        shutil.rmtree(config.WORK_DIR, ignore_errors=True)


app = FastAPI(title="Focus Toolkit", version="0.1.0", lifespan=lifespan)
app.include_router(images_api.router)
app.include_router(excel_api.router)
app.include_router(posters_api.router)
app.include_router(styles_api.router)
app.include_router(ai_api.router)


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

    return ConvertResponse(
        result=result,
        direction=req.direction,
        font=req.font,
        chars_in=len(req.text),
        chars_out=len(result),
    )


@app.get("/api/fonts/info")
def fonts_info() -> dict[str, object]:
    """Map diagnostics — surfaced in Settings and useful for a bad conversion."""
    return fonts.map_report()


@app.get("/api/settings/preferences")
def get_preferences() -> dict[str, object]:
    return db.get_preferences()


@app.put("/api/settings/preferences")
def put_preferences(updates: dict[str, str]) -> dict[str, object]:
    """Write preferences. Unknown keys are refused, never silently stored."""
    try:
        return db.set_preferences(updates)
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
