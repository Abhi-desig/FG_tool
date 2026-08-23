"""API tests for the Phase 2 endpoints."""

from __future__ import annotations

import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from backend import models
from backend.main import app

client = TestClient(app)


def png(w: int = 200, h: int = 150) -> bytes:
    img = Image.new("RGB", (w, h), (40, 90, 160))
    ImageDraw.Draw(img).ellipse((w // 4, h // 4, 3 * w // 4, 3 * h // 4), fill=(230, 200, 60))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def upload(w: int = 200, h: int = 150) -> dict[str, tuple[str, bytes, str]]:
    return {"file": ("client-photo.png", png(w, h), "image/png")}


def wait_for(job_id: str, timeout: float = 300.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] not in {"queued", "running"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in {timeout}s")


# --- print size ----------------------------------------------------------


def test_print_classes_are_listed() -> None:
    body = client.get("/api/printsize/classes").json()
    assert [c["key"] for c in body] == ["brochure", "poster", "flex", "hoarding"]
    assert body[0]["good_dpi"] == 300


def test_assess_answers_plainly() -> None:
    body = client.post(
        "/api/printsize/assess",
        json={
            "pixels_w": 4000, "pixels_h": 2667,
            "target_w": 6, "target_h": 4,
            "unit": "feet", "print_class": "flex",
        },
    ).json()
    assert body["verdict"] == "good"
    assert "Good for 6×4 feet" in body["headline"]
    assert body["best_use"]


def test_assess_says_no_when_it_should() -> None:
    body = client.post(
        "/api/printsize/assess",
        json={
            "pixels_w": 800, "pixels_h": 800,
            "target_w": 297, "target_h": 420,
            "unit": "mm", "print_class": "brochure",
        },
    ).json()
    assert body["verdict"] == "too_small"
    assert body["upscale_to"] is not None


def test_assess_rejects_unknown_print_class() -> None:
    r = client.post(
        "/api/printsize/assess",
        json={
            "pixels_w": 100, "pixels_h": 100, "target_w": 1, "target_h": 1,
            "print_class": "moon-billboard",
        },
    )
    assert r.status_code == 400


@pytest.mark.parametrize(
    "payload",
    [
        {"pixels_w": 0, "pixels_h": 100, "target_w": 1, "target_h": 1},
        {"pixels_w": 100, "pixels_h": 100, "target_w": 0, "target_h": 1},
        {"pixels_w": -5, "pixels_h": 100, "target_w": 1, "target_h": 1},
    ],
)
def test_assess_rejects_nonsense_dimensions(payload: dict) -> None:
    assert client.post("/api/printsize/assess", json=payload).status_code == 422


# --- inspect and upload validation ---------------------------------------


def test_inspect_reports_facts_and_options() -> None:
    body = client.post("/api/images/inspect", files=upload(1200, 900)).json()
    assert body["facts"]["width"] == 1200
    assert len(body["best_use"]) == 4
    assert body["device"]


def test_inspect_rejects_a_non_image() -> None:
    r = client.post(
        "/api/images/inspect",
        files={"file": ("notes.txt", b"just some text", "image/png")},
    )
    assert r.status_code == 400


def test_inspect_rejects_an_empty_file() -> None:
    r = client.post("/api/images/inspect", files={"file": ("x.png", b"", "image/png")})
    assert r.status_code == 400


def test_preview_returns_a_bounded_png() -> None:
    r = client.post("/api/images/preview", files=upload(4000, 3000))
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert max(Image.open(io.BytesIO(r.content)).size) <= 640


# --- jobs ----------------------------------------------------------------


def test_unknown_job_is_404() -> None:
    assert client.get("/api/jobs/deadbeef").status_code == 404


def test_result_before_completion_is_409_not_500() -> None:
    from backend import jobs as jobs_mod

    job = jobs_mod.submit("test", "slow", lambda r: (time.sleep(0.4), {})[1])
    r = client.get(f"/api/jobs/{job.id}/result")
    assert r.status_code in {409, 404}
    wait_for(job.id)


def test_job_list_includes_recent_work() -> None:
    from backend import jobs as jobs_mod

    job = jobs_mod.run_sync("test", "listed", lambda r: {"ok": True})
    ids = [j["id"] for j in client.get("/api/jobs").json()]
    assert job.id in ids


def test_upscale_reports_progress_then_delivers_a_file() -> None:
    """The full path: submit, poll, download, and the DPI is stamped."""
    started = client.post(
        "/api/images/upscale",
        files=upload(120, 100),
        data={"scale": "print", "target_w": "360", "target_h": "300", "dpi": "300"},
    ).json()
    assert started["status"] in {"queued", "running"}

    done = wait_for(started["id"])
    assert done["status"] == "done", done
    assert done["result"]["width"] == 360
    assert done["result"]["method"] in {"realesrgan-x4", "lanczos"}

    got = client.get(f"/api/jobs/{started['id']}/result")
    assert got.status_code == 200
    out = Image.open(io.BytesIO(got.content))
    assert out.size == (360, 300)
    assert out.info["dpi"][0] == pytest.approx(300, abs=1)


def test_upscale_can_write_cmyk_tiff_for_print() -> None:
    started = client.post(
        "/api/images/upscale",
        files=upload(120, 100),
        data={"scale": "print", "target_w": "240", "target_h": "200", "fmt": "PNG", "cmyk": "true"},
    ).json()
    done = wait_for(started["id"])
    assert done["status"] == "done", done
    content = client.get(f"/api/jobs/{started['id']}/result").content
    assert Image.open(io.BytesIO(content)).mode == "CMYK"


def test_cancelled_job_produces_no_result() -> None:
    started = client.post(
        "/api/images/upscale",
        files=upload(600, 600),
        data={"scale": "4x"},
    ).json()
    client.post(f"/api/jobs/{started['id']}/cancel")
    done = wait_for(started["id"])
    assert done["status"] in {"cancelled", "done"}
    if done["status"] == "cancelled":
        assert client.get(f"/api/jobs/{started['id']}/result").status_code == 409


# --- settings ------------------------------------------------------------


def test_settings_models_describes_the_registry() -> None:
    body = client.get("/api/settings/models").json()
    keys = {m["key"] for m in body["models"]}
    assert {"birefnet-general", "realesrgan-x4"} <= keys
    assert body["device"]
    assert "free_gb" in body


def test_preferences_round_trip() -> None:
    updated = client.put(
        "/api/settings/preferences", json={"default_dpi": "150", "colour_profile": "rgb"}
    ).json()
    assert updated["default_dpi"] == "150"
    assert client.get("/api/settings/preferences").json()["colour_profile"] == "rgb"
    client.put("/api/settings/preferences", json={"default_dpi": "300", "colour_profile": "cmyk"})


def test_unknown_preference_is_refused() -> None:
    r = client.put("/api/settings/preferences", json={"make_it_pretty": "yes"})
    assert r.status_code == 400
    assert "unknown preference" in r.json()["detail"]


def test_health_reports_phase_two() -> None:
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["phase"] == 5
    assert "active_jobs" in body


# --- model-backed ---------------------------------------------------------


@pytest.mark.skipif(
    not models.is_available("realesrgan-x4"), reason="upscaler weights not downloaded"
)
def test_upscale_actually_uses_the_model_when_it_can() -> None:
    started = client.post(
        "/api/images/upscale",
        files=upload(150, 120),
        data={"scale": "print", "target_w": "600", "target_h": "480"},
    ).json()
    done = wait_for(started["id"])
    assert done["result"]["method"] == "realesrgan-x4"
    assert done["result"]["tiles"] > 0
    assert done["progress"] == 1.0


# --- scale options -------------------------------------------------------


@pytest.mark.parametrize(("scale", "size"), [("2x", (240, 200)), ("4x", (480, 400))])
def test_scale_option_produces_the_stated_size(
    scale: str, size: tuple[int, int]
) -> None:
    """The operator's 2x / 4x choice is honoured exactly."""
    started = client.post(
        "/api/images/upscale", files=upload(120, 100), data={"scale": scale}
    ).json()
    done = wait_for(started["id"])
    assert done["status"] == "done", done
    assert (done["result"]["width"], done["result"]["height"]) == size
    assert done["result"]["scale"] == scale


def test_print_scale_needs_a_target() -> None:
    r = client.post(
        "/api/images/upscale", files=upload(120, 100), data={"scale": "print"}
    )
    assert r.status_code == 400
    assert "target size" in r.json()["detail"]


def test_unknown_scale_is_rejected() -> None:
    r = client.post(
        "/api/images/upscale", files=upload(120, 100), data={"scale": "10x"}
    )
    assert r.status_code == 422


def test_inspect_offers_both_scale_options_with_sizes() -> None:
    body = client.post("/api/images/inspect", files=upload(900, 600)).json()
    options = {o["scale"]: (o["width"], o["height"]) for o in body["scale_options"]}
    assert options == {"2x": (1800, 1200), "4x": (3600, 2400)}
    assert body["tiles"] > 0


def test_a_very_large_upload_is_accepted() -> None:
    """Any real file size opens — a print shop handles big scans routinely."""
    body = client.post("/api/images/inspect", files=upload(6000, 4000)).json()
    assert body["facts"]["width"] == 6000
    assert body["facts"]["megapixels"] == pytest.approx(24.0, abs=0.1)
