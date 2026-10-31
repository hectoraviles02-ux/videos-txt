"""Tests for adapters/sd_image_adapter.py"""

import os
from unittest.mock import MagicMock, patch

import pytest

from adapters.base import (
    AdapterValidationError,
    NarrationInput,
    RenderConfig,
    RenderResult,
    RunContext,
    Storyboard,
    ValidationReport,
)


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def audio_file(tmp_path):
    p = tmp_path / "narration.wav"
    p.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
    return str(p)


@pytest.fixture
def storyboard():
    from adapters.base import StoryboardScene

    return Storyboard(
        storyboard_version="1.0",
        metadata={
            "source_script": "test",
            "title": "SD Adapter Test",
            "total_duration_seconds": 10,
            "scene_count": 1,
            "generated_at": "2026-07-25T12:00:00Z",
        },
        scenes=[
            StoryboardScene(
                id="scene-001",
                type="narrative",
                title="Test Scene",
                start_seconds=0.0,
                end_seconds=10.0,
                narration="Test narration.",
                visual={
                    "description": "A test image",
                    "components": [{"type": "solid_color", "color": "#0f0f23"}],
                },
                notes="",
                source_refs=[],
            )
        ],
    )


@pytest.fixture
def narration(audio_file):
    return NarrationInput(
        audio_path=audio_file,
        duration_seconds=10.0,
        source="tts",
        tts_engine="piper",
    )


@pytest.fixture
def config():
    return RenderConfig(resolution="1920x1080", fps=30)


@pytest.fixture
def context(tmp_path):
    return RunContext(
        run_id="test-run-001",
        output_dir=str(tmp_path / "render"),
        log_dir=str(tmp_path / "logs"),
    )


@pytest.fixture
def mock_image_generator():
    gen = MagicMock()
    gen.generate.return_value = {"scene-001": "/tmp/scene-001.png"}
    gen.name = "mock-image-gen"
    return gen


@pytest.fixture
def mock_ffmpeg_tools():
    with patch("adapters.sd_image_adapter.resolve_ffmpeg") as mock_ff:
        with patch("adapters.sd_image_adapter.resolve_ffprobe") as mock_fp:
            with patch("adapters.sd_image_adapter.probe_video") as mock_probe:
                with patch("adapters.sd_image_adapter.quality_gate") as mock_qg:
                    mock_ff.return_value = "ffmpeg.exe"
                    mock_fp.return_value = "ffprobe.exe"

                    mock_probe.return_value = {
                        "size_bytes": 500000,
                        "duration_seconds": 10.0,
                        "fps": 30.0,
                    }

                    mock_qg.return_value = {
                        "codec_check": {"pass": True, "actual": "h264"},
                        "resolution_check": {"pass": True, "actual": "1920x1080"},
                        "fps_check": {"pass": True, "actual": 30.0},
                        "duration_check": {"pass": True, "actual": 10.0, "expected": 10.0},
                        "audio_check": {"pass": True, "channels": 2, "sample_rate": 44100},
                        "audio_codec_check": {"pass": True, "actual": "aac"},
                        "file_integrity": {"pass": True, "size_bytes": 500000},
                    }

                    yield {
                        "ffmpeg": mock_ff,
                        "ffprobe": mock_fp,
                        "probe": mock_probe,
                        "quality_gate": mock_qg,
                    }


# ── SD-01a: name, version, deterministic ──────────────────────────────


def test_sd_01a_properties():
    from adapters.sd_image_adapter import SDImageAdapter

    adapter = SDImageAdapter(
        image_generator=MagicMock(),
        ffmpeg_path=None,
        ffprobe_path=None,
    )

    assert adapter.name == "sd-image-adapter"
    assert adapter.version == "0.1.0"
    assert adapter.deterministic is False


# ── SD-01b: render con mock ImageGenerator + mock ffmpeg_tools ────────


def test_sd_01b_render_ok(
    storyboard, narration, config, context, mock_image_generator, mock_ffmpeg_tools, tmp_path
):
    from adapters.sd_image_adapter import SDImageAdapter

    output_path = str(tmp_path / "output.mp4")
    tmp_path.joinpath("output.mp4").write_bytes(b"\x00" * 100)
    img_path = str(tmp_path / "scene-001.png")
    from PIL import Image as _PIL
    _PIL.new("RGB", (64, 64), (100, 100, 100)).save(img_path)
    mock_image_generator.generate.return_value = {"scene-001": img_path}

    with patch.object(SDImageAdapter, "_apply_ken_burns") as mock_kb:
        mock_kb.return_value = {"success": True, "output_path": str(tmp_path / "clip.mp4"), "duration": 10.0}
        with patch.object(SDImageAdapter, "_concat_clips") as mock_concat:
            mock_concat.return_value = {"success": True, "output_path": output_path}

            adapter = SDImageAdapter(
                image_generator=mock_image_generator,
                ffmpeg_path=None,
                ffprobe_path=None,
            )
            result = adapter.render(storyboard, narration, config, context)

    assert isinstance(result, RenderResult)
    assert result.status == "success"
    assert result.video_path == output_path
    assert result.video_hash is not None
    assert result.video_size_bytes is not None
    assert result.engine_info["name"] == "sd-image-adapter"


# ── SD-02c: GPU OOM fallback en render ────────────────────────────────


def test_sd_02c_oom_fallback(
    storyboard, narration, config, context, mock_ffmpeg_tools, tmp_path
):
    from adapters.sd_image_adapter import SDImageAdapter

    mock_gen = MagicMock()
    mock_gen.generate.side_effect = RuntimeError("CUDA out of memory")

    adapter = SDImageAdapter(
        image_generator=mock_gen,
        ffmpeg_path=None,
        ffprobe_path=None,
    )
    result = adapter.render(storyboard, narration, config, context)

    assert result.status == "failed"
    assert "CUDA out of memory" in str(result.error.get("message", ""))


# ── SD-04b: FFmpeg not found → fallback a static image ───────────────


def test_sd_04b_ffmpeg_fallback_static(
    storyboard, narration, config, context, mock_image_generator, tmp_path
):
    from adapters.sd_image_adapter import SDImageAdapter

    img_path = str(tmp_path / "scene-001.png")
    from PIL import Image as _PIL
    _PIL.new("RGB", (64, 64), (100, 100, 100)).save(img_path)
    mock_image_generator.generate.return_value = {"scene-001": img_path}
    tmp_path.joinpath("output.mp4").write_bytes(b"\x00" * 100)

    fake_static_result = {"success": True, "output_path": str(tmp_path / "static.mp4"), "duration": 10.0}

    with patch("adapters.sd_image_adapter.resolve_ffmpeg", return_value="ffmpeg.exe"):
        with patch("adapters.sd_image_adapter.resolve_ffprobe", return_value="ffprobe.exe"):
            with patch.object(SDImageAdapter, "_apply_ken_burns") as mock_kb:
                mock_kb.return_value = {"success": True, "output_path": str(tmp_path / "clip.mp4"), "duration": 10.0}
                with patch.object(SDImageAdapter, "_static_fallback") as mock_static:
                    mock_static.return_value = fake_static_result
                    with patch.object(SDImageAdapter, "_concat_clips") as mock_concat:
                        mock_concat.return_value = {
                            "success": True,
                            "output_path": str(tmp_path / "output.mp4"),
                        }
                        with patch("adapters.sd_image_adapter.probe_video") as mock_probe:
                            mock_probe.return_value = {
                                "size_bytes": 500000,
                                "duration_seconds": 10.0,
                                "fps": 30.0,
                            }
                            with patch("adapters.sd_image_adapter.quality_gate") as mock_qg:
                                mock_qg.return_value = {
                                    "codec_check": {"pass": True, "actual": "h264"},
                                    "resolution_check": {"pass": True, "actual": "1920x1080"},
                                    "fps_check": {"pass": True, "actual": 30.0},
                                    "duration_check": {"pass": True, "actual": 10.0, "expected": 10.0},
                                    "audio_check": {"pass": True, "channels": 2, "sample_rate": 44100},
                                    "audio_codec_check": {"pass": True, "actual": "aac"},
                                    "file_integrity": {"pass": True, "size_bytes": 500000},
                                }

                                adapter = SDImageAdapter(
                                    image_generator=mock_image_generator,
                                    ffmpeg_path=None,
                                    ffprobe_path=None,
                                )
                                result = adapter.render(storyboard, narration, config, context)

    assert result.status == "success"


# ── SD-05a: image cache hit ───────────────────────────────────────────


def test_sd_05a_image_cache_hit(
    storyboard, narration, config, context, mock_image_generator, mock_ffmpeg_tools, tmp_path
):
    from adapters.sd_image_adapter import SDImageAdapter

    output_path = str(tmp_path / "output.mp4")
    tmp_path.joinpath("output.mp4").write_bytes(b"\x00" * 100)

    img_path = str(tmp_path / "scene-001.png")
    from PIL import Image as _PIL
    _PIL.new("RGB", (64, 64), (100, 100, 100)).save(img_path)

    cached_gen = MagicMock()
    cached_gen.generate.return_value = {"scene-001": img_path}
    cached_gen.name = "mock-image-gen"

    with patch.object(SDImageAdapter, "_apply_ken_burns") as mock_kb:
        mock_kb.return_value = {"success": True, "output_path": str(tmp_path / "clip.mp4"), "duration": 10.0}
        with patch.object(SDImageAdapter, "_concat_clips") as mock_concat:
            mock_concat.return_value = {"success": True, "output_path": output_path}

            adapter = SDImageAdapter(
                image_generator=cached_gen,
                ffmpeg_path=None,
                ffprobe_path=None,
            )
            result1 = adapter.render(storyboard, narration, config, context)

            cached_gen.generate.assert_called_once()

            result2 = adapter.render(storyboard, narration, config, context)

    assert result1.status == "success"
    assert result2.status == "success"


# ── 6.9: Contract test ─────────────────────────────────────────────────


class TestSDAdapterContract:
    """Contract validation tests for SDImageAdapter, following the pattern from
    test_adapter_contract.py but with mocked dependencies."""

    @pytest.fixture
    def adapter(self):
        from adapters.sd_image_adapter import SDImageAdapter

        return SDImageAdapter(
            image_generator=MagicMock(),
            ffmpeg_path=None,
            ffprobe_path=None,
        )

    def test_adapter_is_concrete(self, adapter):
        assert adapter.name == "sd-image-adapter"
        assert adapter.version == "0.1.0"

    def test_adapter_has_render_method(self, adapter):
        assert hasattr(adapter, "render")
        assert callable(adapter.render)

    def test_validate_inputs_passes_minimal(self, adapter, audio_file):
        from tests.test_adapter_contract import make_storyboard, make_narration, make_default_config

        sb = make_storyboard()
        nar = make_narration(audio_file)
        cfg = make_default_config()
        adapter.validate_inputs(sb, nar, cfg)

    def test_validate_empty_storyboard_raises(self, adapter, audio_file):
        from tests.test_adapter_contract import make_narration, make_default_config

        sb = Storyboard(storyboard_version="1.0", metadata={}, scenes=[])
        with pytest.raises(AdapterValidationError, match="at least one scene"):
            adapter.validate_inputs(sb, make_narration(audio_file), make_default_config())

    def test_validate_scene_count_mismatch(self, adapter, audio_file):
        from tests.test_adapter_contract import make_storyboard, make_narration, make_default_config

        sb = make_storyboard()
        sb.metadata["scene_count"] = 99
        with pytest.raises(AdapterValidationError, match="scene_count"):
            adapter.validate_inputs(sb, make_narration(audio_file), make_default_config())

    def test_validate_unsupported_resolution(self, adapter, audio_file):
        from tests.test_adapter_contract import make_storyboard, make_narration

        cfg = RenderConfig(resolution="640x480")
        with pytest.raises(AdapterValidationError, match="Unsupported resolution"):
            adapter.validate_inputs(make_storyboard(), make_narration(audio_file), cfg)
