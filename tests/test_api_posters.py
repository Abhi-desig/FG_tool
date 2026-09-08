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

from backend import config, db
from backend.features import ai, posters
from backend.main import app

client = TestClient(app, base_url="http://127.0.0.1:8000")

# One of the nine shipped styles, used where a test needs a real key.
STYLE = "04-offer-block"


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
    assert STYLE in {d["key"] for d in body["designs"]}
    assert body["count"] == len(body["designs"])
    # The empty state shows this, so the operator knows where their files go.
    assert body["folder"].endswith("poster_prompts")
    assert body["tags"] == ["main", "h1", "h2"]


def test_the_prompt_body_never_reaches_the_browser() -> None:
    """It is the shop's own work, and this tool is shown to clients."""
    body = client.get("/api/posters/designs").json()
    for design in body["designs"]:
        # An exact allowlist, not a `"body" not in design` check. The point is
        # that adding a field to what the browser sees has to be a decision
        # somebody made on purpose, and this line is where they notice.
        assert set(design) == {"key", "name", "description", "aspect"}


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
        data={"copy": "main: Onam Sale\nh1: 40% off"},
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
        data={"copy": "main: Onam Sale"},
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
        data={"copy": "main: Onam Sale"},
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
        data={"copy": "h1: 40% off"},
    )
    assert response.status_code == 400
    assert "main:" in response.json()["detail"]
    assert models.calls == []


def test_pinning_a_style_is_refused_unless_dev_tools_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refused, not ignored.

    A silently dropped override would draw in whatever style the model chose and
    report that style back — which reads exactly like the auto-selection
    agreeing with whoever pinned it. That is the one wrong answer available
    here, so the route says no instead (ADR-037).
    """
    models = fake(monkeypatch, some_text("an idea"), an_image())
    response = client.post(
        "/api/posters/generate",
        data={"copy": "main: Sale", "force_style": STYLE},
    )

    assert response.status_code == 403
    assert "DEV_TOOLS" in response.json()["detail"]
    assert models.calls == [], "nothing was sent, so nothing was charged"


def test_an_unknown_pinned_style_names_the_ones_that_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "DEV_TOOLS", True)
    models = fake(monkeypatch, some_text("an idea"), an_image())
    body = client.post(
        "/api/posters/generate",
        data={"copy": "main: Sale", "force_style": "no-such-style"},
    ).json()
    assert body["ok"] is False
    assert STYLE in body["error"]
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
        data={"copy": "main: Onam Sale"},
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
        data={"copy": "main: Onam Sale"},
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
        data={"copy": "main: Sale"},
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
    # The freeze clause, checked by what it protects rather than by its wording:
    # it comes from the `poster-edit` template in Settings and the operator is
    # free to rephrase it. What must survive rephrasing is that the change is
    # scoped to one thing and the poster's existing text is named as untouchable.
    instruction = contents[1].lower()
    assert "change only" in instruction
    assert "text element" in instruction and "unchanged" in instruction


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




# --- the shape of the output (ADR-036) -------------------------------------


def test_the_designs_aspect_ratio_is_sent_as_a_parameter_not_only_as_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ratio written in the prompt is advice; 4:5 drifts to square without this."""
    models = fake(monkeypatch, some_text("a warm courtyard at dusk"), an_image())
    body = client.post(
        "/api/posters/generate",
        data={"copy": "main: Onam Sale", "batch": "true"},
    ).json()

    assert body["ok"] is True
    # Call 0 is the text concept, call 1 draws — only the drawing carries a shape.
    drawing = models.calls[1]
    assert drawing["config"].image_config.aspect_ratio == "4:5"


def test_styles_that_disagree_on_shape_force_no_shape_at_all(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In auto mode the ratio is sent before the style is known.

    So it can only be forced when every style agrees. If they disagree, forcing
    one would reshape whichever styles lost the vote — silently, and only
    visibly on a printed poster. Nothing is forced and each spec's own words are
    left to argue for the shape.
    """
    monkeypatch.setattr(posters, "DESIGNS_DIR", tmp_path)
    (tmp_path / "tall.md").write_text(
        "name: Tall\naspect: 4:5\n---\nA tall poster. No watermarks.", encoding="utf-8"
    )
    (tmp_path / "wide.md").write_text(
        "name: Wide\naspect: 16:9\n---\nA wide poster. No watermarks.", encoding="utf-8"
    )
    models = fake(monkeypatch, some_text("an idea"), an_image())
    client.post("/api/posters/generate", data={"copy": "main: Onam Sale"})

    # The config is still sent — it carries the request for a text part — but it
    # names no shape.
    assert models.calls[1]["config"].image_config is None


def test_a_pinned_style_is_drawn_in_its_own_shape_at_full_spec_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The override exists to exercise one style properly, not approximately."""
    monkeypatch.setattr(config, "DEV_TOOLS", True)
    models = fake(monkeypatch, some_text("an idea"), an_image())
    body = client.post(
        "/api/posters/generate",
        data={"copy": "main: Onam Sale", "force_style": STYLE},
    ).json()

    assert body["ok"] is True
    assert body["style"] == STYLE
    assert "pinned" in body["style_reason"]
    drawing = models.calls[1]
    assert drawing["config"].image_config.aspect_ratio == "4:5"
    # One spec, not nine: the prompt is a fraction of the selection prompt's size.
    assert len(drawing["contents"][0].split()) < 600
    # And no text part is asked for, because there is no choice to report.
    assert drawing["config"].response_modalities is None


def test_a_change_asks_for_the_shape_the_poster_already_has(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Measured from the poster's own pixels.

    The design that made it is not part of a refine request — the operator may
    be three changes deep — so the image is the only honest source for its shape.
    """
    models = fake(monkeypatch, an_image())
    tall = io.BytesIO()
    Image.new("RGB", (1024, 1280), (30, 90, 160)).save(tall, format="PNG")

    body = client.post(
        "/api/posters/refine",
        data={"note": "warmer light"},
        files={"poster": ("poster.png", tall.getvalue(), "image/png")},
    ).json()

    assert body["ok"] is True
    assert models.calls[0]["config"].image_config.aspect_ratio == "4:5"


def test_every_digit_in_the_copy_reaches_the_prompt_quoted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The positive form of the engine's one hard rule.

    The rule itself — refuse if a supplied number went missing — cannot fire
    through this route any more, and that is an improvement rather than a gap.
    Style files no longer carry the copy; the app writes it, once, into quoted
    lines. So the digits are present by construction, and what is worth testing
    is that construction rather than a refusal that can no longer happen.

    The refusal is still enforced at the unit level (`test_posterspec.py`) and
    still earns its place: it guards the day something *writes* a prompt instead
    of assembling one.
    """
    models = fake(monkeypatch, some_text("an idea"), an_image())
    client.post(
        "/api/posters/generate",
        data={"copy": "main: Onam Sale\nh1: Save 78,000\nh2: 9656 00 3244"},
    )

    sent = models.calls[1]["contents"][0]
    for digits in ("78,000", "9656", "3244"):
        assert digits in sent
    # Quoted, which is what tells the model these runs are lettering and what
    # lets the checker compare them against what was pasted.
    assert '"9656 00 3244"' in sent


def test_the_engines_softer_rules_are_shown_beside_the_poster_not_enforced(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A style can break a rule and still make the poster they wanted."""
    monkeypatch.setattr(posters, "DESIGNS_DIR", tmp_path)
    (tmp_path / "generic.md").write_text(
        "name: Generic\naspect: 4:5\n---\nA stunning poster. No watermarks.",
        encoding="utf-8",
    )
    models = fake(monkeypatch, some_text("an idea"), an_image())

    body = client.post(
        "/api/posters/generate", data={"copy": "main: Onam Sale"}
    ).json()

    assert body["ok"] is True
    assert len(models.calls) == 2
    assert any("stunning" in w for w in body["warnings"])


def test_a_style_file_that_still_carries_a_placeholder_is_called_out(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """It would print those six characters on the poster.

    Style files used to be whole prompts with `{{main}}` in them. They are specs
    now and the app writes the words, so nothing substitutes any more — a
    leftover placeholder reaches Google verbatim. Loud in the log, because the
    symptom is a poster with `{{main}}` lettered onto it.
    """
    monkeypatch.setattr(posters, "DESIGNS_DIR", tmp_path)
    (tmp_path / "stale.md").write_text(
        'name: Stale\n---\nA poster reading "{{main}}". No watermarks.', encoding="utf-8"
    )
    with caplog.at_level("WARNING"):
        found = posters.designs()

    assert [d.key for d in found] == ["stale"], "still usable, just complained about"
    assert "{{main}}" in caplog.text
    assert "the app writes the words" in caplog.text


# --- choosing the style (ADR-037) ------------------------------------------


def test_the_operator_sends_copy_and_nothing_else(monkeypatch: pytest.MonkeyPatch) -> None:
    """No style in the request. That is the whole point of the change."""
    models = fake(monkeypatch, some_text("an idea"), an_image())
    body = client.post(
        "/api/posters/generate", data={"copy": "main: Onam Sale"}
    ).json()

    assert body["ok"] is True
    assert len(models.calls) == 2, "one idea call, one call that chooses and draws"


def test_all_nine_styles_are_offered_to_the_model_with_when_and_tone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The catalogue is what makes the choice possible.

    `when` and `tone` are the fields the model judges against — the look alone
    cannot say that a style built for seven short lines fails on one long one.
    """
    models = fake(monkeypatch, some_text("an idea"), an_image())
    client.post("/api/posters/generate", data={"copy": "main: Onam Sale"})

    sent = models.calls[1]["contents"][0]
    for style in posters.designs():
        assert f"[{style.key}]" in sent
        assert style.when in sent
        assert style.tone in sent
    assert "STYLE:" in sent and "WHY:" in sent


def test_the_chosen_style_comes_back_as_a_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit, not inferred from the picture — so it can be logged."""
    reply = FakeResponse(
        [
            FakePart(text="STYLE: 07-numbers-grid\nWHY: the copy carries two figures."),
            FakePart(data=b"POSTERBYTES"),
        ]
    )
    fake(monkeypatch, some_text("an idea"), reply)

    body = client.post(
        "/api/posters/generate",
        data={"copy": "main: Solar\nh1: Save 78,000\nh2: Rate 5.75%"},
    ).json()

    assert body["ok"] is True
    assert body["style"] == "07-numbers-grid"
    assert body["style_reason"] == "the copy carries two figures."


def test_a_text_part_is_asked_for_when_the_model_must_report_its_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without this the reply is an image only and the field can never arrive."""
    models = fake(monkeypatch, some_text("an idea"), an_image())
    client.post("/api/posters/generate", data={"copy": "main: Onam Sale"})

    assert models.calls[1]["config"].response_modalities == ["TEXT", "IMAGE"]


def test_a_style_the_model_invented_is_not_believed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key that is not a real style is worse than none — it would be logged."""
    reply = FakeResponse(
        [
            FakePart(text="STYLE: 12-does-not-exist\nWHY: made this one up."),
            FakePart(data=b"POSTERBYTES"),
        ]
    )
    fake(monkeypatch, some_text("an idea"), reply)

    body = client.post(
        "/api/posters/generate", data={"copy": "main: Onam Sale"}
    ).json()

    assert body["ok"] is True, "the poster is real and was paid for"
    assert body["style"] == ""
    assert body["style_reason"] == "", "no reason for a choice that did not happen"
    assert any("did not say which style" in w for w in body["warnings"])


def test_a_poster_with_no_style_line_is_kept_and_the_reply_is_quoted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Losing a paid poster over a missing label would be the worse trade.

    The warning carries what the model actually replied, because that is what
    tells whoever debugs it whether the contract needs rewording.
    """
    reply = FakeResponse(
        [FakePart(text="Here is your poster!"), FakePart(data=b"POSTERBYTES")]
    )
    fake(monkeypatch, some_text("an idea"), reply)

    body = client.post(
        "/api/posters/generate", data={"copy": "main: Onam Sale"}
    ).json()

    assert body["ok"] is True
    assert base64.b64decode(body["image"]) == b"POSTERBYTES"
    assert body["style"] == ""
    assert any("Here is your poster!" in w for w in body["warnings"])


def test_a_change_request_never_reports_a_style(monkeypatch: pytest.MonkeyPatch) -> None:
    """No catalogue was sent, so any `STYLE:` line would be an invention."""
    reply = FakeResponse(
        [FakePart(text="STYLE: 01-quiet-premium"), FakePart(data=b"POSTERBYTES")]
    )
    fake(monkeypatch, reply)

    body = client.post(
        "/api/posters/refine",
        data={"note": "warmer light"},
        files={"poster": ("poster.png", png(), "image/png")},
    ).json()

    assert body["ok"] is True
    assert "style" not in body


def test_an_empty_styles_folder_is_a_plain_message_not_a_crash(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(posters, "DESIGNS_DIR", tmp_path)
    models = fake(monkeypatch, some_text("an idea"), an_image())

    body = client.post(
        "/api/posters/generate", data={"copy": "main: Onam Sale"}
    ).json()

    assert body["ok"] is False
    assert "no poster styles" in (body["error"] or "").casefold()
    assert str(tmp_path) in (body["error"] or "")
    assert len(models.calls) == 1, "the drawing call never went out"
