"""API tests for the Phase 2 endpoints."""

from __future__ import annotations

import io
import threading
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from backend import config, jobs, main, models
from backend.features import images
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


# --- job visibility and lifetime -----------------------------------------
#
# NEXT.md 0.4, 2.2, 2.3, 2.4. The work was never lost server-side; the UI simply
# never asked, could not show a cancel in progress, and had no way to say that a
# step reports no progress.


def test_job_state_the_ui_needs_is_exposed() -> None:
    """`cancelling` and `indeterminate` existed internally but never shipped.

    Without them the UI could show nothing but "Removing background…" for the
    11 seconds a cancel took on a GPU — minutes on the shop PC (NEXT.md 2.2).
    """
    job = jobs.run_sync("t", "test", lambda r: {"ok": True})
    body = job.as_dict()
    for key in ("cancelling", "indeterminate", "files_deleted", "finished_at"):
        assert key in body, f"{key} missing from the job payload"


def test_a_cancelling_job_says_so() -> None:
    started = threading.Event()
    release = threading.Event()

    def slow(reporter: jobs.Reporter) -> dict[str, object]:
        started.set()
        release.wait(5)
        reporter.check_cancelled()
        return {}

    job = jobs.submit("t", "slow", slow)
    assert started.wait(5)
    job.cancel()
    # Reported immediately, while the uninterruptible block is still running.
    assert job.as_dict()["cancelling"] is True
    release.set()


def test_a_finished_job_is_not_reported_as_cancelling() -> None:
    job = jobs.run_sync("t", "test", lambda r: {"ok": True})
    job.cancel()
    assert job.as_dict()["cancelling"] is False


def test_an_opaque_step_is_marked_indeterminate() -> None:
    """A bar frozen at 35% for a minute is worse than no bar (NEXT.md 2.3)."""

    def work(reporter: jobs.Reporter) -> dict[str, object]:
        reporter.opaque_step("Removing background — cannot report progress", 0.35)
        assert reporter._job.indeterminate is True  # noqa: SLF001
        return {}

    job = jobs.run_sync("t", "test", work)
    # Cleared once the job ends, so a finished job never looks stuck.
    assert job.as_dict()["indeterminate"] is False


def test_a_named_step_clears_indeterminate() -> None:
    def work(reporter: jobs.Reporter) -> dict[str, object]:
        reporter.opaque_step("opaque", 0.3)
        reporter.step("Writing PNG…", 0.9)
        assert reporter._job.indeterminate is False  # noqa: SLF001
        return {}

    jobs.run_sync("t", "test", work)


def test_the_cutout_step_says_it_cannot_report_progress(monkeypatch) -> None:
    """The operator must be told, in the step text, not left guessing.

    `rembg.remove` is stubbed rather than run: this is about what the operator
    is told, and a real BiRefNet pass would add a minute to every test run for
    one assertion about a string.
    """
    seen: list[str] = []

    class Spy(jobs.Reporter):
        def opaque_step(self, text: str, progress: float | None = None) -> None:
            seen.append(text)
            super().opaque_step(text, progress)

    import contextlib

    monkeypatch.setattr(
        models, "loaded", lambda key: contextlib.nullcontext("stub-session")
    )
    stub = type(
        "M", (), {"remove": staticmethod(lambda img, **kw: img.convert("RGBA"))}
    )
    monkeypatch.setitem(__import__("sys").modules, "rembg", stub)

    job = jobs.Job(id="spy", kind="t", label="t")
    images.cutout(Image.new("RGB", (64, 64), (10, 20, 30)), Spy(job))

    assert seen, "the background-removal step did not declare itself opaque"
    assert any("cannot report progress" in text for text in seen)
    # And it names the wait and the hardware, so a long one is expected.
    assert any("on " in text for text in seen)


def test_the_cutout_estimate_scales_with_the_hardware() -> None:
    """A minute on a GPU is minutes on the shop PC's i3, and it must say so."""
    seconds = images.estimate_cutout_seconds(4000, 3000)
    assert seconds > 0
    assert images._minutes_phrase(45) == "about 45 seconds"  # noqa: SLF001
    assert images._minutes_phrase(300) == "about 5 minutes"  # noqa: SLF001
    assert images._minutes_phrase(60) == "about 60 seconds"  # noqa: SLF001


def test_expired_jobs_are_swept_and_say_so() -> None:
    """SECURITY.md §5 promised this and it was not happening (NEXT.md 2.4)."""
    job = jobs.run_sync("t", "test", lambda r: {"file": "x.png"})
    directory = config.WORK_DIR / job.id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "x.png").write_bytes(png())

    # Pretend it finished an hour ago.
    job.finished_at = time.time() - 3601
    assert job in jobs.registry.expired()

    removed = main.sweep_expired_jobs()
    assert removed >= 1
    assert not directory.exists(), "client artwork was left on disk"
    assert job.files_deleted is True
    assert jobs.registry.expired() == [], "a swept job must not be swept twice"


def test_downloading_a_swept_result_explains_itself() -> None:
    """A bare 404 reads as a bug. This is a deliberate deletion."""
    job = jobs.run_sync("t", "test", lambda r: {"file": "x.png", "media_type": "image/png"})
    job.finished_at = time.time() - 3601
    main.sweep_expired_jobs()

    response = client.get(f"/api/jobs/{job.id}/result")
    assert response.status_code == 410
    assert "deleted" in response.json()["detail"].lower()


def test_a_fresh_job_is_never_swept() -> None:
    job = jobs.run_sync("t", "test", lambda r: {"file": "x.png"})
    directory = config.WORK_DIR / job.id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "x.png").write_bytes(png())
    main.sweep_expired_jobs()
    assert directory.exists(), "a result the operator may still want was deleted"
    assert job.files_deleted is False
