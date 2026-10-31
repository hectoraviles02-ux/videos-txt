"""Tests for synthesis/storyboard_builder.py"""

from unittest.mock import MagicMock, patch

import pytest


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def script_6_scenes():
    return {
        "title": "Test Video",
        "scenes": [
            {"narration": f"Scene {i} narration.", "visual_description": f"Visual {i}"}
            for i in range(1, 7)
        ],
    }


@pytest.fixture
def script_5_scenes():
    return {
        "title": "Five Scene Video",
        "scenes": [
            {"narration": f"Narration {i}.", "visual_description": f"Visual {i}"}
            for i in range(1, 6)
        ],
    }


@pytest.fixture
def valid_storyboard_dict():
    return {
        "type": "storyboard",
        "storyboard_version": "1.0",
        "metadata": {
            "source_script": "test",
            "title": "Valid",
            "total_duration_seconds": 30,
            "scene_count": 1,
            "generated_at": "2026-07-25T12:00:00Z",
        },
        "scenes": [
            {
                "id": "scene-001",
                "type": "narrative",
                "title": "Scene 1",
                "start_seconds": 0.0,
                "end_seconds": 30.0,
                "narration": "Test narration.",
                "visual": {
                    "description": "A test visual",
                    "components": [{"type": "solid_color", "color": "#000000"}],
                },
            }
        ],
    }


# ── SY-03a: 6 scenes, 180s → Storyboard con 6 escenas ────────────────


def test_sy_03a_build_from_script_6_scenes_180s(script_6_scenes):
    from synthesis.storyboard_builder import build_from_script

    sb = build_from_script(script_6_scenes, total_duration_seconds=180)

    assert len(sb.scenes) == 6
    assert sb.storyboard_version == "1.0"
    assert sb.metadata["scene_count"] == 6
    assert sb.metadata["total_duration_seconds"] == 180.0
    assert sb.metadata["title"] == "Test Video"
    for i, scene in enumerate(sb.scenes):
        assert scene.id == f"scene-{i + 1:03d}"
        assert scene.type == "narrative"
        assert scene.visual["description"] == f"Visual {i + 1}"


# ── SY-03b: JSON válido → validate() retorna [] ──────────────────────


def test_sy_03b_validate_valid(valid_storyboard_dict):
    from synthesis.storyboard_builder import validate

    errors = validate(valid_storyboard_dict)
    assert errors == []


# ── SY-03c: missing duration → validate() retorna errores ─────────────


def test_sy_03c_validate_missing_duration():
    from synthesis.storyboard_builder import validate

    invalid = {
        "storyboard_version": "1.0",
        "metadata": {},
        "scenes": [
            {
                "id": "scene-001",
                "narration": "Test.",
                "visual": {"components": [{"type": "solid_color", "color": "#000"}]},
            }
        ],
    }
    errors = validate(invalid)
    assert len(errors) > 0
    assert any("duration" in e["field"] for e in errors)


# ── SY-04a: 120s / 5 scenes → 24.0s cada una ─────────────────────────


def test_sy_04a_uniform_timing(script_5_scenes):
    from synthesis.storyboard_builder import build_from_script

    sb = build_from_script(script_5_scenes, total_duration_seconds=120)

    assert len(sb.scenes) == 5
    for i, scene in enumerate(sb.scenes):
        dur = scene.end_seconds - scene.start_seconds
        if i < 4:
            assert dur == pytest.approx(24.0, abs=0.01)
        else:
            assert dur == pytest.approx(24.0, abs=0.01)
    assert sb.scenes[0].start_seconds == pytest.approx(0.0)
    assert sb.scenes[-1].end_seconds == pytest.approx(120.0)


# ── SY-04b: 125s / 6 scenes → 5 de 20.83s + última de 20.85s ─────────


def test_sy_04b_remaining_to_last_scene(script_6_scenes):
    from synthesis.storyboard_builder import build_from_script

    sb = build_from_script(script_6_scenes, total_duration_seconds=125)

    assert len(sb.scenes) == 6
    first_5_dur = sum(s.end_seconds - s.start_seconds for s in sb.scenes[:5])
    last_dur = sb.scenes[-1].end_seconds - sb.scenes[-1].start_seconds

    assert first_5_dur == pytest.approx(5 * 125 / 6, abs=0.1)
    assert sb.scenes[-1].end_seconds == pytest.approx(125.0, abs=0.01)


# ── SY-05a: dispatch transcript ───────────────────────────────────────


@patch("synthesis.storyboard_builder.ScriptGenerator")
def test_sy_05a_dispatch_transcript(MockGenerator, script_5_scenes):
    from synthesis.storyboard_builder import process

    mock_gen = MagicMock()
    mock_gen.generate.return_value = script_5_scenes
    MockGenerator.return_value = mock_gen

    content = {
        "type": "transcript",
        "segmentos": [{"inicio_seg": 0.0, "texto": "Test transcript."}],
    }
    sb = process(content, generator=mock_gen)

    assert sb.metadata["scene_count"] == 5
    mock_gen.generate.assert_called_once_with(content)


# ── SY-05b: dispatch article ──────────────────────────────────────────


@patch("synthesis.storyboard_builder.ScriptGenerator")
def test_sy_05b_dispatch_article(MockGenerator):
    from synthesis.storyboard_builder import process

    mock_gen = MagicMock()
    mock_gen.generate.return_value = {
        "title": "Article Video",
        "scenes": [{"narration": "Narration.", "visual_description": "Visual"}],
    }
    MockGenerator.return_value = mock_gen

    content = {"type": "article", "text": "Article content."}
    sb = process(content, generator=mock_gen)

    assert sb.metadata["scene_count"] == 1
    mock_gen.generate.assert_called_once_with(content)


# ── SY-05c: dispatch storyboard ───────────────────────────────────────


def test_sy_05c_dispatch_storyboard(valid_storyboard_dict):
    from synthesis.storyboard_builder import process

    sb = process(valid_storyboard_dict)

    assert sb.metadata["scene_count"] == 1
    assert sb.metadata["title"] == "Valid"
    assert sb.scenes[0].id == "scene-001"
