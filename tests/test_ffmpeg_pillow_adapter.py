import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from adapters.base import (
    AdapterRenderError,
    AdapterValidationError,
    NarrationInput,
    RenderConfig,
    RenderResult,
    RunContext,
    Storyboard,
    StoryboardScene,
    ValidationReport,
)
from adapters.ffmpeg_pillow_adapter import FFmpegPillowAdapter, _find_default_font, _parse_resolution
from tests.fixtures import load_storyboard_fixture, make_image, make_wav, patch_storyboard_image_ref


@pytest.fixture
def adapter():
    return FFmpegPillowAdapter()


@pytest.fixture
def lab_storyboard(tmp_path) -> Storyboard:
    data = load_storyboard_fixture()
    img_path = str(tmp_path / "test_image.png")
    make_image(img_path)
    data = patch_storyboard_image_ref(data, img_path)
    return Storyboard.from_dict(data)


@pytest.fixture
def lab_narration(tmp_path) -> NarrationInput:
    wav_path = str(tmp_path / "narration.wav")
    make_wav(wav_path, duration_seconds=25.0)
    return NarrationInput(
        audio_path=wav_path,
        duration_seconds=25.0,
        source="tts",
    )


@pytest.fixture
def lab_config() -> RenderConfig:
    return RenderConfig(
        resolution="1280x720",
        fps=30,
        output_filename="lab_test.mp4",
        max_duration_seconds=60,
    )


@pytest.fixture
def lab_context(tmp_path) -> RunContext:
    return RunContext(
        run_id="lab-test-001",
        output_dir=str(tmp_path / "output"),
        log_dir=str(tmp_path / "logs"),
    )


def test_adapter_name(adapter):
    assert adapter.name == "ffmpeg-pillow"


def test_adapter_version(adapter):
    assert adapter.version == "0.1.0"


def test_adapter_deterministic(adapter):
    assert adapter.deterministic is True


def test_adapter_implements_video_adapter(adapter):
    from adapters.base import VideoAdapter
    assert isinstance(adapter, VideoAdapter)


def test_validate_inputs_passes(adapter, lab_storyboard, lab_narration, lab_config):
    adapter.validate_inputs(lab_storyboard, lab_narration, lab_config)


def test_validate_inputs_missing_audio(adapter, lab_storyboard, lab_config):
    nar = NarrationInput(
        audio_path="/nonexistent.wav", duration_seconds=25.0, source="tts"
    )
    with pytest.raises(AdapterValidationError, match="audio file not found"):
        adapter.validate_inputs(lab_storyboard, nar, lab_config)


def test_render_empty_storyboard(adapter, lab_narration, lab_config, lab_context, tmp_path):
    sb = Storyboard(
        storyboard_version="1.0",
        metadata={"source_script": "t", "title": "T", "total_duration_seconds": 10, "scene_count": 0, "generated_at": "2026-07-25T12:00:00Z"},
        scenes=[],
    )
    result = adapter.render(sb, lab_narration, lab_config, lab_context)
    assert result.status == "failed"
    assert result.error is not None


def test_render_ffmpeg_not_available(adapter, lab_storyboard, lab_narration, lab_config, lab_context):
    adapter._ffmpeg_path = "/nonexistent/ffmpeg"
    adapter._ffprobe_path = "/nonexistent/ffprobe"
    result = adapter.render(lab_storyboard, lab_narration, lab_config, lab_context)
    assert result.status == "failed"
    assert result.error is not None


def test_render_ffprobe_not_available(adapter, lab_storyboard, lab_narration, lab_config, lab_context):
    adapter._ffmpeg_path = "/nonexistent/ffmpeg"
    adapter._ffprobe_path = "/nonexistent/ffprobe"
    result = adapter.render(lab_storyboard, lab_narration, lab_config, lab_context)
    assert result.status == "failed"
    assert "ffmpeg" in (result.error.get("message", "") or "").lower()


@patch("adapters.ffmpeg_pillow_adapter.compose_video")
def test_render_ffmpeg_fails(mock_compose, adapter, lab_storyboard, lab_narration, lab_config, lab_context):
    mock_compose.return_value = {"success": False, "error": "simulated failure"}
    result = adapter.render(lab_storyboard, lab_narration, lab_config, lab_context)
    assert result.status == "failed"


@patch("adapters.ffmpeg_pillow_adapter.resolve_ffmpeg")
@patch("adapters.ffmpeg_pillow_adapter.resolve_ffprobe")
@patch("adapters.ffmpeg_tools.subprocess.run")
def test_render_compose_fails(
    mock_run, mock_ffprobe, mock_ffmpeg,
    adapter, lab_storyboard, lab_narration, lab_config, lab_context,
):
    mock_ffmpeg.return_value = "/fake/ffmpeg"
    mock_ffprobe.return_value = "/fake/ffprobe"
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="ffmpeg error"
    )
    result = adapter.render(lab_storyboard, lab_narration, lab_config, lab_context)
    assert result.status == "failed"


@patch("adapters.ffmpeg_pillow_adapter.resolve_ffmpeg")
@patch("adapters.ffmpeg_pillow_adapter.resolve_ffprobe")
@patch("adapters.ffmpeg_tools.subprocess.run")
def test_render_timeout(
    mock_run, mock_ffprobe, mock_ffmpeg,
    adapter, lab_storyboard, lab_narration, lab_config, lab_context,
):
    mock_ffmpeg.return_value = "/fake/ffmpeg"
    mock_ffprobe.return_value = "/fake/ffprobe"
    mock_run.side_effect = subprocess.TimeoutExpired(cmd=[], timeout=10)
    result = adapter.render(lab_storyboard, lab_narration, lab_config, lab_context)
    assert result.status == "failed"


def test_render_missing_image_source(adapter, lab_config, lab_context, tmp_path):
    sb = Storyboard(
        storyboard_version="1.0",
        metadata={"source_script": "t", "title": "T", "total_duration_seconds": 10, "scene_count": 1, "generated_at": "2026-07-25T12:00:00Z"},
        scenes=[
            StoryboardScene(
                id="scene-001", type="hook", title="T",
                start_seconds=0.0, end_seconds=10.0,
                narration="Text",
                visual={"components": [{"type": "image", "source": "/nonexistent/img.png"}]},
            )
        ],
    )
    wav = str(tmp_path / "n.wav")
    make_wav(wav, duration_seconds=10.0)
    nar = NarrationInput(audio_path=wav, duration_seconds=10.0, source="tts")
    result = adapter.render(sb, nar, lab_config, lab_context)
    assert result.status == "failed"


def test_render_unsupported_component_type(adapter, lab_narration, lab_config, lab_context, tmp_path):
    sb = Storyboard(
        storyboard_version="1.0",
        metadata={"source_script": "t", "title": "T", "total_duration_seconds": 10, "scene_count": 1, "generated_at": "2026-07-25T12:00:00Z"},
        scenes=[
            StoryboardScene(
                id="scene-001", type="hook", title="T",
                start_seconds=0.0, end_seconds=10.0,
                narration="Text",
                visual={"components": [{"type": "animation"}]},
            )
        ],
    )
    wav = str(tmp_path / "n.wav")
    make_wav(wav, duration_seconds=10.0)
    nar = NarrationInput(audio_path=wav, duration_seconds=10.0, source="tts")
    result = adapter.render(sb, nar, lab_config, lab_context)
    assert result.status == "failed"
    assert "UnsupportedComponent" in result.error.get("exception_type", "")


def test_render_invalid_color(adapter, lab_narration, lab_config, lab_context, tmp_path):
    sb = Storyboard(
        storyboard_version="1.0",
        metadata={"source_script": "t", "title": "T", "total_duration_seconds": 10, "scene_count": 1, "generated_at": "2026-07-25T12:00:00Z"},
        scenes=[
            StoryboardScene(
                id="scene-001", type="hook", title="T",
                start_seconds=0.0, end_seconds=10.0,
                narration="Text",
                visual={"components": [{"type": "solid_color", "color": "bad"}]},
            )
        ],
    )
    wav = str(tmp_path / "n.wav")
    make_wav(wav, duration_seconds=10.0)
    nar = NarrationInput(audio_path=wav, duration_seconds=10.0, source="tts")
    result = adapter.render(sb, nar, lab_config, lab_context)
    assert result.status == "failed"


def test_render_empty_text_content(adapter, lab_config, lab_context, tmp_path):
    sb = Storyboard(
        storyboard_version="1.0",
        metadata={"source_script": "t", "title": "T", "total_duration_seconds": 10, "scene_count": 1, "generated_at": "2026-07-25T12:00:00Z"},
        scenes=[
            StoryboardScene(
                id="scene-001", type="hook", title="T",
                start_seconds=0.0, end_seconds=10.0,
                narration="Text",
                visual={"components": [
                    {"type": "solid_color", "color": "#000000"},
                    {"type": "text", "content": ""},
                ]},
            )
        ],
    )
    wav = str(tmp_path / "n.wav")
    make_wav(wav, duration_seconds=10.0)
    nar = NarrationInput(audio_path=wav, duration_seconds=10.0, source="tts")
    result = adapter.render(sb, nar, lab_config, lab_context)
    assert result.status == "failed"


def test_render_missing_audio(adapter, lab_storyboard, lab_config, lab_context):
    nar = NarrationInput(
        audio_path="/nonexistent/audio.wav", duration_seconds=25.0, source="tts"
    )
    result = adapter.render(lab_storyboard, nar, lab_config, lab_context)
    assert result.status == "failed"
    assert result.error is not None


def test_duration_mismatch(adapter, lab_storyboard, lab_config, lab_context, tmp_path):
    wav = str(tmp_path / "n.wav")
    make_wav(wav, duration_seconds=5.0)
    nar = NarrationInput(audio_path=wav, duration_seconds=5.0, source="tts")
    with pytest.raises(AdapterValidationError, match="Narration duration"):
        adapter.validate_inputs(lab_storyboard, nar, lab_config)


def test_render_result_success_invariant(adapter):
    r = RenderResult(
        status="success",
        video_path="/fake.mp4",
        video_size_bytes=100,
        video_hash="abc",
        validation=ValidationReport(
            codec_check={"pass": True, "actual": "h264"},
            resolution_check={"pass": True, "actual": "1920x1080"},
            fps_check={"pass": True, "actual": 30.0},
            duration_check={"pass": True, "actual": 60.0, "expected": 60.0},
            audio_check={"pass": True, "channels": 2, "sample_rate": 44100},
            file_integrity={"pass": True, "hash_match": True},
            min_frame_check={"pass": True, "message": "OK"},
        ),
        engine_info={"name": "ffmpeg-pillow", "version": "0.1.0"},
        duration_seconds=5.0,
    )
    adapter.validate_result(r)


def test_render_result_failed_invariant(adapter):
    r = RenderResult(
        status="failed",
        error={"message": "render failed", "exception_type": "AdapterRenderError"},
        engine_info={"name": "ffmpeg-pillow", "version": "0.1.0"},
    )
    adapter.validate_result(r)


def test_check_pillow_available():
    from adapters.ffmpeg_tools import check_pillow
    result = check_pillow()
    assert result["available"] is True
    assert "version" in result


def test_check_ffmpeg_not_in_path():
    from adapters.ffmpeg_tools import check_ffmpeg
    with patch("adapters.ffmpeg_tools.shutil.which", return_value=None):
        result = check_ffmpeg(ffmpeg_path=None)
        assert result["available"] is False


def test_check_ffprobe_not_in_path():
    from adapters.ffmpeg_tools import check_ffprobe
    with patch("adapters.ffmpeg_tools.shutil.which", return_value=None):
        result = check_ffprobe(ffprobe_path=None)
        assert result["available"] is False


def test_parse_resolution():
    assert _parse_resolution("1920x1080") == (1920, 1080)
    assert _parse_resolution("640x360") == (640, 360)


def test_parse_resolution_invalid():
    with pytest.raises(AdapterValidationError, match="Invalid resolution"):
        _parse_resolution("bad")


def test_find_default_font():
    font = _find_default_font()
    if os.name == "nt":
        assert font is not None, "Should find a font on Windows"
        assert os.path.isfile(font)
    else:
        assert font is None or os.path.isfile(font)


@pytest.mark.skipif(
    not __import__("shutil").which("ffmpeg") and not os.path.isfile(
        os.path.join(os.path.dirname(__file__), "..", "bin", "ffmpeg.exe")
    ),
    reason="ffmpeg not available in PATH or bin/",
)
def test_integration_render_full(
    adapter, lab_storyboard, lab_narration, lab_config, lab_context,
):
    result = adapter.render(lab_storyboard, lab_narration, lab_config, lab_context)
    assert result.status == "success", f"Render failed: {result.error}"
    assert result.video_path is not None
    assert os.path.isfile(result.video_path)
    assert result.video_size_bytes > 1000
    assert result.video_hash is not None
    assert len(result.video_hash) == 64
    assert result.validation is not None
    assert result.validation.all_pass is True
    result.validate()


@pytest.mark.skipif(
    not __import__("shutil").which("ffmpeg") and not os.path.isfile(
        os.path.join(os.path.dirname(__file__), "..", "bin", "ffmpeg.exe")
    ),
    reason="ffmpeg not available in PATH or bin/",
)
def test_integration_probe_real_video(
    adapter, lab_storyboard, lab_narration, lab_config, lab_context,
):
    result = adapter.render(lab_storyboard, lab_narration, lab_config, lab_context)
    assert result.status == "success"
    assert result.actual_resolution == "1280x720"
    assert abs(result.actual_fps - 30) < 1.0
    assert result.validation.codec_check["pass"] is True


@pytest.mark.skipif(
    not __import__("shutil").which("ffmpeg") and not os.path.isfile(
        os.path.join(os.path.dirname(__file__), "..", "bin", "ffmpeg.exe")
    ),
    reason="ffmpeg not available in PATH or bin/",
)
def test_integration_existing_output_fails(
    adapter, lab_storyboard, lab_narration, lab_config, lab_context,
):
    os.makedirs(lab_context.output_dir, exist_ok=True)
    existing = os.path.join(lab_context.output_dir, lab_config.output_filename)
    with open(existing, "w") as f:
        f.write("pretend existing file")
    result = adapter.render(lab_storyboard, lab_narration, lab_config, lab_context)
    assert result.status == "failed"


@pytest.mark.skipif(
    not __import__("shutil").which("ffmpeg") and not os.path.isfile(
        os.path.join(os.path.dirname(__file__), "..", "bin", "ffmpeg.exe")
    ),
    reason="ffmpeg not available in PATH or bin/",
)
def test_integration_missing_audio_fails(
    adapter, lab_storyboard, lab_config, lab_context,
):
    nar = NarrationInput(
        audio_path="/nonexistent/audio.wav", duration_seconds=25.0, source="tts"
    )
    result = adapter.render(lab_storyboard, nar, lab_config, lab_context)
    assert result.status == "failed"
