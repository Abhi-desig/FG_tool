"""API tests for Phase 4 posters."""

from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from backend.main import app

# base_url is a loopback address deliberately: `guard.LocalOnlyMiddleware`
# refuses any Host that is not this machine, and TestClient's default
# `http://testserver` is exactly the DNS-rebinding shape it exists to stop.
client = TestClient(app, base_url="http://127.0.0.1:8000")
SVG_NS = "{http://www.w3.org/2000/svg}"


def background(dark_top: bool = True) -> bytes:
    img = Image.new("RGB", (400, 600), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, 400, 300), fill=(20, 20, 30) if dark_top else (240, 240, 245))
    draw.rectangle((0, 300, 400, 600), fill=(240, 240, 245) if dark_top else (20, 20, 30))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


LAYOUT = {
    "canvas": "a4-portrait",
    "blocks": [
        {"id": "h", "text": "കേരളം ഗ്രാൻഡ് സെയിൽ", "y": 0.1, "size": "large"},
        {"id": "o", "text": "50% OFF", "y": 0.45, "size": "huge"},
        {"id": "p", "text": "9847 000 000", "y": 0.85, "size": "small"},
    ],
}


def test_presets_are_listed() -> None:
    body = client.get("/api/posters/presets").json()
    keys = {c["key"] for c in body["canvases"]}
    assert {"a4-portrait", "flex-6x4", "square-social"} <= keys
    assert body["fonts"]["ascii"] == "ML-TTKarthika"


def test_analyse_finds_calm_regions() -> None:
    body = client.post(
        "/api/posters/analyse", files={"file": ("bg.png", background(), "image/png")}
    ).json()
    assert body["width"] == 400
    assert len(body["calm_regions"]) == 4
    assert all("busyness" in r for r in body["calm_regions"])


def test_analyse_rejects_a_non_image() -> None:
    r = client.post(
        "/api/posters/analyse", files={"file": ("x.png", b"nope", "image/png")}
    )
    assert r.status_code == 400


def test_check_reports_safe_zone_and_overflow() -> None:
    body = client.post("/api/posters/check", json={"layout": LAYOUT}).json()
    assert body["canvas"]["width_px"] == 2480
    assert {r["id"] for r in body["safe_zone"]} == {"h", "o", "p"}


def test_check_flags_text_over_the_trim_edge() -> None:
    """Left-aligned, so the glyphs really do start at the trim.

    The check measures the drawn text rather than the box that holds it
    (NEXT.md 3.8) — a centred line inside an edge-to-edge box is safe, and
    `test_check_does_not_flag_a_centred_line_in_a_wide_box` pins that.
    """
    layout = {
        "canvas": "a4-portrait",
        "blocks": [{"id": "e", "text": "EDGE", "x": 0.0, "y": 0.5, "align": "left"}],
    }
    body = client.post("/api/posters/check", json={"layout": layout}).json()
    assert body["safe_zone"][0]["outside_safe_zone"] is True


def test_check_does_not_flag_a_centred_line_in_a_wide_box() -> None:
    layout = {
        "canvas": "a4-portrait",
        "blocks": [{"id": "e", "text": "EDGE", "x": 0.0, "y": 0.5, "align": "centre"}],
    }
    body = client.post("/api/posters/check", json={"layout": layout}).json()
    assert body["safe_zone"][0]["outside_safe_zone"] is False


def test_check_reports_what_auto_fit_changed() -> None:
    """A shrunk or wrapped block must be declared, not silently different."""
    layout = {
        "canvas": "a4-portrait",
        "blocks": [{"id": "h", "text": "ഫോക്കസ് ഡിജിറ്റൽസ്", "size": "huge", "y": 0.1}],
    }
    body = client.post("/api/posters/check", json={"layout": layout}).json()
    assert body["fitted"], "the headline was adjusted and nothing said so"
    assert body["blocks"][0]["lines"]


def test_check_prefers_a_browser_measurement_over_its_own_estimate() -> None:
    """The server has no shaping engine; a real measurement must win."""
    layout = {
        "canvas": "a4-portrait",
        "blocks": [{"id": "h", "text": "ഓണം ആശംസകൾ", "size": "medium", "y": 0.1}],
    }
    # 1897 px at the medium size on A4 — the measured value from NEXT.md 0.2.
    measured = {"h": 1897 / (0.055 * 3508)}
    body = client.post(
        "/api/posters/check", json={"layout": layout, "measured": measured}
    ).json()
    assert body["overflow"] == []
    assert body["blocks"][0]["scale"] == 1.0


def test_check_ignores_an_impossible_measurement() -> None:
    """A stale or broken number must not be able to make a block 'fit'."""
    layout = {
        "canvas": "a4-portrait",
        "blocks": [{"id": "h", "text": "ഫോക്കസ് ഡിജിറ്റൽസ്", "size": "huge", "y": 0.1}],
    }
    body = client.post(
        "/api/posters/check", json={"layout": layout, "measured": {"h": -5}}
    ).json()
    # Falls back to the estimate, which wraps it — not to "1 em wide, fits".
    assert body["blocks"][0]["lines"] != ["ഫോക്കസ് ഡിജിറ്റൽസ്"]


def test_check_rejects_an_unknown_canvas() -> None:
    r = client.post(
        "/api/posters/check", json={"layout": {"canvas": "nope", "blocks": []}}
    )
    assert r.status_code == 400


def test_bad_colour_is_rejected() -> None:
    layout = {"blocks": [{"id": "a", "text": "x", "colour": "red"}]}
    r = client.post("/api/posters/check", json={"layout": layout})
    assert r.status_code == 422


def test_svg_download_has_editable_text() -> None:
    r = client.post(
        "/api/posters/svg", data={"layout": json.dumps(LAYOUT), "safe_zone": "false"}
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    root = ET.fromstring(r.content)
    # Auto-fit may have wrapped the headline into <tspan>s (NEXT.md 0.2), so the
    # words are collected across them. The gate is editable text, not one line.
    texts: list[str] = []
    for element in root.iter(f"{SVG_NS}text"):
        spans = element.findall(f"{SVG_NS}tspan")
        texts.append(
            " ".join((s.text or "") for s in spans) if spans else (element.text or "")
        )
    assert "കേരളം ഗ്രാൻഡ് സെയിൽ" in texts
    assert not list(root.iter(f"{SVG_NS}path"))


def test_svg_embeds_the_background() -> None:
    r = client.post(
        "/api/posters/svg",
        data={"layout": json.dumps(LAYOUT)},
        files={"background": ("bg.png", background(), "image/png")},
    )
    assert r.status_code == 200
    assert b"data:image/png;base64," in r.content


def test_svg_rejects_malformed_layout_json() -> None:
    r = client.post("/api/posters/svg", data={"layout": "{not json"})
    assert r.status_code == 400
    assert "Bad layout" in r.json()["detail"]


def test_auto_places_and_colours_against_the_photo() -> None:
    r = client.post(
        "/api/posters/auto",
        data={"layout": json.dumps(LAYOUT), "place": "true"},
        files={"background": ("bg.png", background(dark_top=True), "image/png")},
    )
    assert r.status_code == 200
    blocks = r.json()["blocks"]
    assert len(blocks) == 3
    assert all(b["colour"] in {"#ffffff", "#16161d"} for b in blocks)
    # Reading order preserved.
    assert [b["y"] for b in blocks] == sorted(b["y"] for b in blocks)


def test_auto_needs_a_background() -> None:
    r = client.post("/api/posters/auto", data={"layout": json.dumps(LAYOUT)})
    assert r.status_code == 400
    assert "background" in r.json()["detail"]


def test_too_many_blocks_is_refused() -> None:
    layout = {
        "blocks": [{"id": f"b{i}", "text": "x"} for i in range(60)],
    }
    r = client.post("/api/posters/check", json={"layout": layout})
    assert r.status_code == 422
