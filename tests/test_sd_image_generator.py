"""Tests for adapters/image_generator.py (SDImageGenerator)."""

import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def storyboard():
    from adapters.base import Storyboard, StoryboardScene

    return Storyboard(
        storyboard_version="1.0",
        metadata={
            "source_script": "test",
            "title": "SD Test",
            "total_duration_seconds": 30,
            "scene_count": 2,
            "generated_at": "2026-07-25T12:00:00Z",
        },
        scenes=[
            StoryboardScene(
                id="scene-001",
                type="narrative",
                title="Scene 1",
                start_seconds=0.0,
                end_seconds=15.0,
                narration="Primera escena.",
                visual={"description": "A cat sitting on a chair", "components": [{"type": "solid_color", "color": "#0f0f23"}]},
            ),
            StoryboardScene(
                id="scene-002",
                type="narrative",
                title="Scene 2",
                start_seconds=15.0,
                end_seconds=30.0,
                narration="Segunda escena.",
                visual={"description": "A dog running in the park", "components": [{"type": "solid_color", "color": "#1a1a3e"}]},
            ),
        ],
    )


@pytest.fixture
def generator():
    from adapters.image_generator import SDImageGenerator

    return SDImageGenerator(
        model_id="test-model",
        device="cpu",
        fallback_to_solid=False,
        sd_params={"width": 512, "height": 512, "num_inference_steps": 1},
    )


# ── ImageGenerator Protocol ───────────────────────────────────────────


def test_isinstance_image_generator():
    from adapters.image_generator import ImageGenerator, SDImageGenerator

    gen = SDImageGenerator()
    assert isinstance(gen, ImageGenerator)


# ── generate() output format ──────────────────────────────────────────


@patch("adapters.image_generator.SDImageGenerator._load_pipeline")
def test_generate_output_format(mock_load_pipeline, generator, storyboard, tmp_path):
    mock_load_pipeline.return_value = None

    from adapters.image_generator import SDImageGenerator

    gen = SDImageGenerator(
        model_id="test-model",
        device="cpu",
        fallback_to_solid=True,
        sd_params={"width": 512, "height": 512},
    )
    with patch("adapters.image_generator.SDImageGenerator._solid_fallback") as mock_fb:
        mock_fb.side_effect = lambda scene, path: path

        result = gen.generate(storyboard, str(tmp_path))

    assert isinstance(result, dict)
    assert len(result) == 2
    assert "scene-001" in result
    assert "scene-002" in result
    assert all(isinstance(v, str) for v in result.values())


# ── Image cache: prompts duplicados reusan archivo ────────────────────


@patch("adapters.image_generator.SDImageGenerator._load_pipeline")
def test_image_cache_reuses_file(mock_load_pipeline, storyboard, tmp_path):
    from adapters.image_generator import SDImageGenerator

    mock_load_pipeline.return_value = None

    gen = SDImageGenerator(
        model_id="test-model",
        device="cpu",
        fallback_to_solid=True,
        sd_params={"width": 64, "height": 64},
    )

    same_desc = "Same description"
    storyboard.scenes[0].visual["description"] = same_desc
    storyboard.scenes[1].visual["description"] = same_desc

    output_path = str(tmp_path / "scene-001.png")
    with patch("adapters.image_generator.SDImageGenerator._solid_fallback") as mock_fb:
        mock_fb.side_effect = [
            output_path,
            output_path,
        ]

        result = gen.generate(storyboard, str(tmp_path))

    assert result["scene-001"] == result["scene-002"]


# ── cleanup() llama torch.cuda.empty_cache() ──────────────────────────


def test_cleanup_calls_empty_cache():
    from adapters.image_generator import SDImageGenerator

    gen = SDImageGenerator()
    gen._pipeline = MagicMock()

    with patch("adapters.image_generator._torch") as mock_torch:
        gen.cleanup()

    assert gen._pipeline is None
    mock_torch.cuda.empty_cache.assert_called_once()


def test_cleanup_no_pipeline():
    from adapters.image_generator import SDImageGenerator

    gen = SDImageGenerator()
    gen._pipeline = None

    with patch("adapters.image_generator._torch") as mock_torch:
        gen.cleanup()

    mock_torch.cuda.empty_cache.assert_called_once()
    assert gen._pipeline is None


# ── OOM fallback: diffuser raise OOM → solid_color sin crash ──────────


@patch("adapters.image_generator.SDImageGenerator._load_pipeline")
def test_oom_fallback(mock_load_pipeline, storyboard, tmp_path):
    from adapters.image_generator import SDImageGenerator

    mock_load_pipeline.return_value = None

    gen = SDImageGenerator(
        model_id="test-model",
        device="cpu",
        fallback_to_solid=True,
        sd_params={"width": 64, "height": 64},
    )

    with patch("adapters.image_generator.SDImageGenerator._solid_fallback") as mock_fb:
        mock_fb.return_value = str(tmp_path / "fallback.png")

        result = gen.generate(storyboard, str(tmp_path))

    assert len(result) == 2
    assert mock_fb.call_count == 2


@patch("adapters.image_generator.SDImageGenerator._load_pipeline")
def test_oom_no_crash(mock_load_pipeline, storyboard, tmp_path):
    from adapters.image_generator import SDImageGenerator, logger

    mock_pipe = MagicMock()
    mock_pipe.side_effect = RuntimeError("CUDA out of memory")
    mock_load_pipeline.return_value = mock_pipe

    gen = SDImageGenerator(
        model_id="test-model",
        device="cuda",
        fallback_to_solid=True,
        sd_params={"width": 64, "height": 64},
    )

    with patch.object(logger, "warning") as mock_warn:
        with patch("adapters.image_generator.SDImageGenerator._solid_fallback") as mock_fb:
            mock_fb.return_value = str(tmp_path / "fallback.png")

            result = gen.generate(storyboard, str(tmp_path))

    assert len(result) == 2
