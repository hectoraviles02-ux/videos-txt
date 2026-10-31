"""Smoke test: minimal end-to-end pipeline with mocked dependencies."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from adapters.base import (
    NarrationInput,
    RenderConfig,
    RenderResult,
    RunContext,
    Storyboard,
    ValidationReport,
)


@pytest.fixture
def minimal_storyboard(tmp_path):
    data = {
        "storyboard_version": "1.0",
        "metadata": {
            "source_script": "smoke-test",
            "title": "Smoke Test",
            "total_duration_seconds": 10,
            "scene_count": 1,
            "generated_at": "2026-07-25T12:00:00Z",
        },
        "scenes": [
            {
                "id": "scene-001",
                "type": "narrative",
                "title": "Smoke Scene",
                "start_seconds": 0.0,
                "end_seconds": 10.0,
                "narration": "Esto es una prueba de humo del pipeline.",
                "visual": {
                    "components": [{"type": "solid_color", "color": "#000000"}],
                },
            },
        ],
    }
    path = str(tmp_path / "storyboard.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return path


def _make_success_result() -> RenderResult:
    return RenderResult(
        status="success",
        video_path="/tmp/output.mp4",
        video_size_bytes=500000,
        video_duration_seconds=10.0,
        video_hash="a" * 64,
        actual_resolution="1920x1080",
        actual_fps=30.0,
        validation=ValidationReport(
            codec_check={"pass": True, "actual": "h264"},
            resolution_check={"pass": True, "actual": "1920x1080"},
            fps_check={"pass": True, "actual": 30.0},
            duration_check={"pass": True, "actual": 10.0, "expected": 10.0},
            audio_check={"pass": True, "channels": 2, "sample_rate": 44100},
            file_integrity={"pass": True, "size_bytes": 500000},
            min_frame_check={"pass": True, "message": "OK"},
        ),
        engine_info={"name": "smoke-adapter", "version": "0.1.0"},
        duration_seconds=5.0,
    )


def test_smoke_pipeline_ok(minimal_storyboard, tmp_path):
    """End-to-end smoke test: mock SDImageGenerator + gTTS, run_pipeline
    with minimal 1-scene storyboard, verify PipelineResult.status == TECHNICAL_PASS."""
    from pipeline.cli import run_pipeline

    output_path = str(tmp_path / "output.mp4")
    tmp_path.joinpath("output.mp4").write_bytes(b"\x00" * 500000)

    with (
        patch("pipeline.cli.FFmpegPillowAdapter") as MockAdapter,
        patch("pipeline.providers.gtts.GTTSAdapter") as MockProvider,
    ):
        mock_adapter = MagicMock()
        result = _make_success_result()
        result.video_path = output_path
        mock_adapter.render.return_value = result
        MockAdapter.return_value = mock_adapter

        mock_provider = MagicMock()
        mock_provider.name = "gtts"
        mock_provider.generate.return_value = NarrationInput(
            audio_path=str(tmp_path / "narration.wav"),
            duration_seconds=10.0,
            source="tts",
        )
        MockProvider.return_value = mock_provider

        pipeline_result = run_pipeline(minimal_storyboard, mock_provider)

    assert pipeline_result.status == "TECHNICAL_PASS"
    assert pipeline_result.narration_duration == 10.0
    assert pipeline_result.run_id is not None
    assert pipeline_result.total_time > 0
    assert pipeline_result.tts_time >= 0
    assert pipeline_result.manifest_path is not None
    assert pipeline_result.result.status == "success"
