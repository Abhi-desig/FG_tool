"""The poster screen, end to end, without spending anything.

Every Google call is faked. What is under test is the wiring the operator
depends on and cannot see: that the copy is read the way they wrote it, that a
reference image replaces the concept call rather than adding to it, that a
refusal keeps their work, and that a change is applied to the poster they were
looking at rather than to a fresh one.
"""

from __future__ import annotations

import base64
import io
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend import db
from backend.features import ai, posters
from backend.main import app

client = TestClient(app, base_url="http://127.0.0.1:8000")

# The design shipped in `data/poster_prompts/` so the screen runs out of the box.
EXAMPLE = "example-festival"


def png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (30, 90, 160)).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeInline:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.mime_type = "image/png"


class FakePart:
    def __init__(self, data: bytes | None = None, text: str | None = None) -> None:
        self.inline_data = FakeInline(data) if data else None
        self.text = text


class FakeResponse:
    def __init__(self, parts: list[FakePart], text: str | None = None) -> None:
        self.candidates = [type("C", (), {"content": type("X", (), {"parts": parts})()})()]
        self.text = text


class Recorder:
    """Answers in order, and keeps every request so the test can inspect it."""

    def __init__(self, answers: list[Any], raises: Exception | None = None) -> None:
        self.answers = answers
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.raises:
            raise self.raises
        return self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]


def fake(monkeypatch: pytest.MonkeyPatch, *answers: Any, raises: Exception | None = None):
    models = Recorder(list(answers), raises)
    monkeypatch.setattr(ai, "_client", lambda: type("C", (), {"models": models})())
    return models


def an_image() -> FakeResponse:
    return FakeResponse([FakePart(data=b"POSTERBYTES")])


def some_text(body: str) -> FakeResponse:
    return FakeResponse([FakePart(text=body)], text=body)


@pytest.fixture(autouse=True)
def a_key() -> None:
    db.set_api_key("GEMINI_API_KEY", "AIza-not-a-real-key-at-all")


# --- the shop's own designs -----------------------------------------------


def test_the_designs_folder_is_listed_with_where_it_lives() -> None:
    body = client.get("/api/posters/designs").json()
    assert EXAMPLE in {d["key"] for d in body["designs"]}
    assert body["count"] == len(body["designs"])
    # The empty state shows this, so the operator knows where their files go.
    assert body["folder"].endswith("poster_prompts")
    assert body["tags"] == ["main", "h1", "h2"]


def test_the_prompt_body_never_reaches_the_browser() -> None:
    """It is the shop's own work, and this tool is shown to clients."""
    body = client.get("/api/posters/designs").json()
    for design in body["designs"]:
        assert set(design) == {"key", "name", "description"}


def test_the_folders_own_readme_is_not_offered_as_a_design() -> None:
    keys = {d["key"] for d in client.get("/api/posters/designs").json()["designs"]}
    assert "README" not in keys and "readme" not in keys


# --- reading the copy ------------------------------------------------------


def parse(text: str) -> dict[str, str]:
    return client.post("/api/posters/parse", data={"copy": text}).json()["copy"]


def test_tagged_copy_is_read_into_exactly_three_lines() -> None:
    assert parse("main: Onam Sale\nh1: Up to 40% off\nh2: Only this week") == {
        "main": "Onam Sale",
        "h1": "Up to 40% off",
        "h2": "Only this week",
    }


def test_an_untagged_first_line_is_the_headline() -> None:
    """It always is, and asking for a tag on the obvious line is friction."""
    assert parse("Onam Sale\nh1: 40% off")["main"] == "Onam Sale"


def test_tags_are_read_however_they_were_typed() -> None:
    """The copy is pasted from WhatsApp; nobody is careful about it there."""
    assert parse("MAIN : Onam Sale\nH1 - 40% off") == {
        "main": "Onam Sale",
        "h1": "40% off",
    }


def test_a_tag_the_app_does_not_know_is_not_invented() -> None:
    parsed = parse("main: Sale\noffer: buy one get one\nphone: 9847 000 000")
    assert set(parsed) == {"main"}


def test_a_second_line_under_one_tag_joins_it() -> None:
    assert parse("main: Onam\nSale is on")["main"] == "Onam Sale is on"


def test_the_parse_says_what_is_missing() -> None:
    body = client.post("/api/posters/parse", data={"copy": "main: Sale"}).json()
    assert body["missing"] == ["h1", "h2"]


# --- generating ------------------------------------------------------------


def test_a_poster_comes_back_with_its_copy_and_its_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = fake(monkeypatch, some_text("A warm lamp-lit courtyard."), an_image())
    body = client.post(
        "/api/posters/generate",
        data={"copy": "main: Onam Sale\nh1: 40% off", "design": EXAMPLE},
    ).json()

    assert body["ok"] is True
    assert base64.b64decode(body["image"]) == b"POSTERBYTES"
    # Both calls: the visual idea, then the picture. The operator is deciding
    # whether to press it again, and a figure missing the first is not that.
    assert len(models.calls) == 2
    assert body["cost_paise"] == 600 + 5
    # Shown beside the poster, because nothing else can check the words now.
    assert body["copy"] == {"main": "Onam Sale", "h1": "40% off"}
    assert any("SynthID" in w for w in body["warnings"])


def test_the_visual_idea_reaches_the_design_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = fake(monkeypatch, some_text("A warm lamp-lit courtyard."), an_image())
    client.post(
        "/api/posters/generate",
        data={"copy": "main: Onam Sale", "design": EXAMPLE},
    )
    drawing = models.calls[1]["contents"]
    assert "A warm lamp-lit courtyard." in drawing[0]
    assert "Onam Sale" in drawing[0]


def test_a_reference_picture_replaces_the_idea_call_rather_than_adding_to_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A picture the operator chose is a better brief than anything written from
    the words, and paying for both would be paying twice to be told less."""
    models = fake(monkeypatch, an_image())
    body = client.post(
        "/api/posters/generate",
        data={"copy": "main: Onam Sale", "design": EXAMPLE},
        files={"reference": ("ref.png", png(), "image/png")},
    ).json()

    assert body["ok"] is True
    assert len(models.calls) == 1, "the concept call should have been skipped"
    assert body["concept"] == ""
    assert body["cost_paise"] == 600


def test_copy_with_no_headline_is_refused_before_any_spend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = fake(monkeypatch, an_image())
    response = client.post(
        "/api/posters/generate",
        data={"copy": "h1: 40% off", "design": EXAMPLE},
    )
    assert response.status_code == 400
    assert "main:" in response.json()["detail"]
    assert models.calls == []


def test_an_unknown_design_names_the_ones_that_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = fake(monkeypatch, some_text("an idea"), an_image())
    body = client.post(
        "/api/posters/generate",
        data={"copy": "main: Sale", "design": "no-such-design"},
    ).json()
    assert body["ok"] is False
    assert EXAMPLE in body["error"]
    # The concept call ran; the picture never did.
    assert len(models.calls) == 1


def test_a_refused_call_is_200_and_keeps_the_operators_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ROADMAP.md Phase 5 exit gate. An HTTP error makes the browser discard the
    form; a 200 with `ok:false` keeps every word on screen."""
    fake(monkeypatch, raises=RuntimeError("blocked by safety filters"))
    response = client.post(
        "/api/posters/generate",
        data={"copy": "main: Onam Sale", "design": EXAMPLE},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "refused" in body["error"].lower()
    assert body["cost_rupees"] == 0
    assert body["copy"] == {"main": "Onam Sale"}


def test_a_failed_idea_does_not_lose_the_poster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The design's own prompt still describes a poster. Failing the whole job
    because the optional half failed would charge for nothing."""
    models = fake(monkeypatch, some_text(""), an_image())
    body = client.post(
        "/api/posters/generate",
        data={"copy": "main: Onam Sale", "design": EXAMPLE},
    ).json()
    assert body["ok"] is True
    assert len(models.calls) == 2
    assert any("visual idea could not be written" in w for w in body["warnings"])


def test_a_reference_that_is_not_an_image_is_refused_before_any_spend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = fake(monkeypatch, an_image())
    response = client.post(
        "/api/posters/generate",
        data={"copy": "main: Sale", "design": EXAMPLE},
        files={"reference": ("ref.png", b"not an image", "image/png")},
    )
    assert response.status_code == 400
    assert models.calls == []


# --- changing what came back ----------------------------------------------


def test_a_change_is_applied_to_the_poster_that_was_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = fake(monkeypatch, an_image())
    body = client.post(
        "/api/posters/refine",
        data={"note": "make the background darker"},
        files={"poster": ("poster.png", png(), "image/png")},
    ).json()

    assert body["ok"] is True
    contents = models.calls[0]["contents"]
    # The picture goes first and the instruction second, so the words say what
    # to do with the image rather than describing a new one.
    assert getattr(contents[0], "inline_data", None) is not None
    assert "make the background darker" in contents[1]
    assert "change nothing else" in contents[1].lower()


def test_a_change_with_no_note_is_refused_before_any_spend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = fake(monkeypatch, an_image())
    body = client.post(
        "/api/posters/refine",
        data={"note": "   "},
        files={"poster": ("poster.png", png(), "image/png")},
    ).json()
    assert body["ok"] is False
    assert models.calls == []


# --- the prompt set on disk ------------------------------------------------


def test_a_design_file_with_no_header_is_skipped_not_fatal(tmp_path, monkeypatch) -> None:
    """The operator drops these in by hand. One mistyped file must not empty
    the whole screen."""
    monkeypatch.setattr(posters, "DESIGNS_DIR", tmp_path)
    (tmp_path / "broken.md").write_text("no front matter here", encoding="utf-8")
    (tmp_path / "good.md").write_text(
        "name: Good\ndescription: fine\n---\nDraw {{main}}", encoding="utf-8"
    )
    assert [d.key for d in posters.designs()] == ["good"]


def test_a_missing_folder_is_an_empty_list_not_a_crash(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(posters, "DESIGNS_DIR", tmp_path / "nothing-here")
    assert posters.designs() == []
    body = client.get("/api/posters/designs").json()
    assert body["designs"] == []


def test_a_design_without_a_name_falls_back_to_its_filename(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(posters, "DESIGNS_DIR", tmp_path)
    (tmp_path / "big-flex.md").write_text("---\nDraw {{main}}", encoding="utf-8")
    assert posters.designs()[0].name == "Big Flex"


def test_a_placeholder_a_poster_cannot_fill_is_emptied_not_sent(
    tmp_path, monkeypatch
) -> None:
    """`{{offer}}` reaching Google would spend money on a confused request."""
    monkeypatch.setattr(posters, "DESIGNS_DIR", tmp_path)
    (tmp_path / "d.md").write_text(
        "name: D\n---\nHeadline: {{main}}\nOffer: {{offer}}", encoding="utf-8"
    )
    built = posters.prompt_for("d", {"main": "Onam Sale"})
    assert "Onam Sale" in built
    assert "{{" not in built
    # The bare label goes with it rather than being sent as "Offer:".
    assert "Offer:" not in built
