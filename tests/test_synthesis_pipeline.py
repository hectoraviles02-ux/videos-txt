"""Tests for synthesis/pipeline.py (SynthesisPipeline)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from synthesis.pipeline import SynthesisPipeline


# ── SP-01a: run generates script only (no TTS, no adapter) ─────────────


@patch("synthesis.pipeline.ScriptGenerator._call_ollama_api")
def test_sp_01a_run_script_only(mock_api, tmp_path):
    mock_api.return_value = (
        json.dumps({
            "title": "Test",
            "scenes": [
                {"narration": "Scene one.", "visual_description": "Visual one"},
                {"narration": "Scene two.", "visual_description": "Visual two"},
            ],
        }),
        {"eval_count": 10},
    )

    pipeline = SynthesisPipeline()
    result = pipeline.run("Some transcript text.", str(tmp_path))

    assert result["status"] == "success"
    assert result["script_json"] is not None
    assert result["storyboard"] is not None
    assert result["audio_path"] is None  # no TTS provider
    assert result["video_path"] is None  # no video adapter
    assert result["duration_seconds"] > 0


# ── SP-01b: run with TTS provider ──────────────────────────────────────


@patch("synthesis.pipeline.ScriptGenerator._call_ollama_api")
def test_sp_01b_run_with_tts(mock_api, tmp_path):
    mock_api.return_value = (
        json.dumps({
            "title": "Test",
            "scenes": [
                {"narration": "Scene one.", "visual_description": "Visual one"},
            ],
        }),
        {"eval_count": 5},
    )

    mock_tts = MagicMock()
    mock_tts.name = "mock-tts"
    mock_tts.synthesize.return_value = tmp_path / "narration.mp3"

    pipeline = SynthesisPipeline(tts_provider=mock_tts)
    result = pipeline.run("Text.", str(tmp_path))

    assert result["status"] == "success"
    assert result["audio_path"] is not None


# ── SP-01c: run with video adapter ─────────────────────────────────────


@patch("synthesis.pipeline.ScriptGenerator._call_ollama_api")
def test_sp_01c_run_with_adapter(mock_api, tmp_path):
    mock_api.return_value = (
        json.dumps({
            "title": "Test",
            "scenes": [
                {"narration": "Scene.", "visual_description": "Visual"},
            ],
        }),
        {},
    )

    mock_adapter = MagicMock()
    mock_adapter.name = "mock-adapter"
    mock_render_result = MagicMock()
    mock_render_result.status = "success"
    mock_render_result.video_path = str(tmp_path / "output.mp4")
    mock_render_result.error = None
    mock_adapter.render.return_value = mock_render_result

    pipeline = SynthesisPipeline(video_adapter=mock_adapter)
    result = pipeline.run("Text.", str(tmp_path))

    assert result["status"] == "success"
    assert result["video_path"] is not None


# ── SP-01d: run with TTS + adapter (full pipeline) ─────────────────────


@patch("synthesis.pipeline.ScriptGenerator._call_ollama_api")
def test_sp_01d_run_full_pipeline(mock_api, tmp_path):
    mock_api.return_value = (
        json.dumps({
            "title": "Test",
            "scenes": [
                {"narration": "Scene.", "visual_description": "Visual"},
            ],
        }),
        {},
    )

    mock_tts = MagicMock()
    mock_tts.name = "mock-tts"
    mock_tts.synthesize.return_value = tmp_path / "narration.mp3"

    mock_adapter = MagicMock()
    mock_adapter.name = "mock-adapter"
    mock_render_result = MagicMock()
    mock_render_result.status = "success"
    mock_render_result.video_path = str(tmp_path / "output.mp4")
    mock_render_result.error = None
    mock_adapter.render.return_value = mock_render_result

    pipeline = SynthesisPipeline(tts_provider=mock_tts, video_adapter=mock_adapter)
    result = pipeline.run("Full pipeline text.", str(tmp_path))

    assert result["status"] == "success"
    assert result["script_json"] is not None
    assert result["storyboard"] is not None
    assert result["audio_path"] is not None
    assert result["video_path"] is not None


# ── SP-01e: script generation failure ──────────────────────────────────


@patch("synthesis.pipeline.ScriptGenerator._call_ollama_api")
def test_sp_01e_script_fails(mock_api, tmp_path):
    from synthesis.script_generator import OllamaConnectionError

    mock_api.side_effect = OllamaConnectionError("Cannot connect")

    pipeline = SynthesisPipeline()
    result = pipeline.run("Some text.", str(tmp_path))

    assert result["status"] == "failed"
    assert "Script generation" in (result.get("error") or "")


# ── SP-01f: video adapter failure → failed status ──────────────────────


@patch("synthesis.pipeline.ScriptGenerator._call_ollama_api")
def test_sp_01f_adapter_fails(mock_api, tmp_path):
    mock_api.return_value = (
        json.dumps({
            "title": "Test",
            "scenes": [
                {"narration": "Scene.", "visual_description": "Visual"},
            ],
        }),
        {},
    )

    mock_adapter = MagicMock()
    mock_adapter.name = "mock-adapter"
    mock_render_result = MagicMock()
    mock_render_result.status = "failed"
    mock_render_result.video_path = None
    mock_render_result.error = {"message": "GPU OOM"}
    mock_adapter.render.return_value = mock_render_result

    pipeline = SynthesisPipeline(video_adapter=mock_adapter)
    result = pipeline.run("Text.", str(tmp_path))

    assert result["status"] == "failed"
    assert "GPU OOM" in (result.get("error") or "")


# ── SP-02a: properties ─────────────────────────────────────────────────


def test_sp_02a_properties():
    pipeline = SynthesisPipeline()
    assert pipeline.script_generator is not None
    assert pipeline.tts_provider is None
    assert pipeline.video_adapter is None

    mock_tts = MagicMock()
    pipeline.tts_provider = mock_tts
    assert pipeline.tts_provider is mock_tts
