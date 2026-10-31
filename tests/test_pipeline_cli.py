import json
import os
from unittest.mock import MagicMock, patch

import pytest

from adapters.base import NarrationInput, RenderResult, ValidationReport
from pipeline.tts_protocol import TTSProvider


@pytest.fixture
def storyboard_json(tmp_path) -> str:
    data = {
        "storyboard_version": "1.0",
        "metadata": {
            "source_script": "test",
            "title": "Test Pipeline",
            "total_duration_seconds": 10,
            "scene_count": 1,
            "generated_at": "2026-07-25T12:00:00Z",
        },
        "scenes": [
            {
                "id": "scene-001",
                "type": "hook",
                "title": "Test",
                "start_seconds": 0.0,
                "end_seconds": 10.0,
                "narration": "Esto es una prueba del pipeline.",
                "visual": {
                    "components": [
                        {"type": "solid_color", "color": "#000000"},
                    ],
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
        engine_info={"name": "ffmpeg-pillow", "version": "0.1.0"},
        duration_seconds=5.0,
    )


class FakeTTSProvider:
    @property
    def name(self) -> str:
        return "fake"

    def generate(self, storyboard, output_dir) -> NarrationInput:
        wav = os.path.join(output_dir, "narration.wav")
        os.makedirs(output_dir, exist_ok=True)
        with open(wav, "wb") as f:
            f.write(b"\x00" * 100)
        return NarrationInput(
            audio_path=wav,
            duration_seconds=10.0,
            source="tts",
            tts_engine="fake",
            text_hash="abc123",
        )


@pytest.fixture
def fake_provider() -> TTSProvider:
    return FakeTTSProvider()


def test_run_pipeline_ok(storyboard_json, fake_provider, tmp_path):
    from pipeline.cli import run_pipeline

    with patch("pipeline.cli.FFmpegPillowAdapter") as MockAdapter:
        mock_adapter = MagicMock()
        result = _make_success_result()
        result.video_path = str(tmp_path / "output.mp4")
        result.video_size_bytes = 500000
        tmp_path.joinpath("output.mp4").write_bytes(b"\x00" * 500000)
        mock_adapter.render.return_value = result
        MockAdapter.return_value = mock_adapter

        result = run_pipeline(storyboard_json, fake_provider)

    assert result.status == "TECHNICAL_PASS"
    assert result.narration_duration == 10.0


def test_run_pipeline_render_fails(storyboard_json, fake_provider):
    from pipeline.cli import run_pipeline

    with patch("pipeline.cli.FFmpegPillowAdapter") as MockAdapter:
        mock_adapter = MagicMock()
        mock_adapter.render.return_value = RenderResult(
            status="failed",
            error={"message": "render exploded", "exception_type": "AdapterRenderError"},
            engine_info={"name": "ffmpeg-pillow", "version": "0.1.0"},
        )
        MockAdapter.return_value = mock_adapter

        result = run_pipeline(storyboard_json, fake_provider)

    assert result.status == "FAILED"


def test_run_pipeline_missing_storyboard(fake_provider):
    from pipeline.cli import run_pipeline

    with pytest.raises(FileNotFoundError):
        run_pipeline("/nonexistent/storyboard.json", fake_provider)


def test_run_pipeline_generates_run_id(storyboard_json, fake_provider, tmp_path):
    from pipeline.cli import run_pipeline

    with patch("pipeline.cli.FFmpegPillowAdapter") as MockAdapter:
        mock_adapter = MagicMock()
        res = _make_success_result()
        res.video_path = str(tmp_path / "output.mp4")
        tmp_path.joinpath("output.mp4").write_bytes(b"\x00" * 500000)
        mock_adapter.render.return_value = res
        MockAdapter.return_value = mock_adapter

        result = run_pipeline(storyboard_json, fake_provider)

    import re
    assert re.match(r"^\d{8}T\d{6}Z_[a-f0-9]{6}$", result.run_id), f"Unexpected run_id format: {result.run_id}"


def test_run_pipeline_honors_custom_run_id(storyboard_json, fake_provider, tmp_path):
    from pipeline.cli import run_pipeline

    with patch("pipeline.cli.FFmpegPillowAdapter") as MockAdapter:
        mock_adapter = MagicMock()
        res = _make_success_result()
        res.video_path = str(tmp_path / "output.mp4")
        tmp_path.joinpath("output.mp4").write_bytes(b"\x00" * 500000)
        mock_adapter.render.return_value = res
        MockAdapter.return_value = mock_adapter

        result = run_pipeline(storyboard_json, fake_provider, run_id="custom-001")

    assert result.run_id == "custom-001"


def test_main_cli_ok(storyboard_json, tmp_path):
    from pipeline.cli import main
    import sys

    with (
        patch("pipeline.providers.gtts.GTTSAdapter") as MockProvider,
        patch("pipeline.cli.FFmpegPillowAdapter") as MockAdapter,
        patch.object(sys, "argv", ["pipeline", storyboard_json]),
    ):
        mock_provider = MagicMock()
        mock_provider.name = "gtts"
        mock_provider.generate.return_value = NarrationInput(
            audio_path=storyboard_json, duration_seconds=10.0, source="tts",
        )
        MockProvider.return_value = mock_provider

        res = _make_success_result()
        res.video_path = str(tmp_path / "output.mp4")
        tmp_path.joinpath("output.mp4").write_bytes(b"\x00" * 500000)
        mock_adapter = MagicMock()
        mock_adapter.render.return_value = res
        MockAdapter.return_value = mock_adapter

        main()


def test_main_cli_exits_on_missing_storyboard():
    from pipeline.cli import main
    import sys

    with (
        patch.object(sys, "argv", ["pipeline", "/nonexistent.json"]),
        pytest.raises(SystemExit) as exc,
    ):
        main()
    assert exc.value.code == 1


# ── PL-01a: run_pipeline con storyboard_path → PipelineResult ────────


def test_pl_01a_run_pipeline_returns_pipeline_result(storyboard_json, fake_provider, tmp_path):
    from pipeline.cli import run_pipeline

    with patch("pipeline.cli.FFmpegPillowAdapter") as MockAdapter:
        mock_adapter = MagicMock()
        res = _make_success_result()
        res.video_path = str(tmp_path / "output.mp4")
        res.video_size_bytes = 500000
        tmp_path.joinpath("output.mp4").write_bytes(b"\x00" * 500000)
        mock_adapter.render.return_value = res
        MockAdapter.return_value = mock_adapter

        result = run_pipeline(storyboard_json, fake_provider)

    assert isinstance(result.run_id, str)
    assert result.status == "TECHNICAL_PASS"
    assert isinstance(result.total_time, float)
    assert isinstance(result.tts_time, float)
    assert result.narration_duration == 10.0
    assert result.result is not None
    assert isinstance(result.manifest_path, str)


# ── PL-01c: sin path ni storyboard → ValueError ──────────────────────


def test_pl_01c_empty_path_no_storyboard_raises(fake_provider):
    from pipeline.cli import run_pipeline

    with pytest.raises(ValueError, match="storyboard_path or storyboard"):
        run_pipeline("", fake_provider)


# ── PL-02a: adapter=SDImageAdapter mock → usa SDImageAdapter ─────────


def test_pl_02a_sd_adapter(storyboard_json, fake_provider, tmp_path):
    from pipeline.cli import run_pipeline

    output_path = str(tmp_path / "output.mp4")
    tmp_path.joinpath("output.mp4").write_bytes(b"\x00" * 500000)

    mock_adapter = MagicMock()
    res = _make_success_result()
    res.video_path = output_path
    mock_adapter.render.return_value = res

    result = run_pipeline(storyboard_json, fake_provider, adapter=mock_adapter)

    assert result.status == "TECHNICAL_PASS"
    assert result.result is not None
    mock_adapter.render.assert_called_once()


# ── PL-02b: adapter=None → FFmpegPillowAdapter default ──────────────


def test_pl_02b_default_adapter(storyboard_json, fake_provider, tmp_path):
    from pipeline.cli import run_pipeline

    output_path = str(tmp_path / "output.mp4")
    tmp_path.joinpath("output.mp4").write_bytes(b"\x00" * 500000)

    with patch("pipeline.cli.FFmpegPillowAdapter") as MockAdapter:
        mock_adapter = MagicMock()
        res = _make_success_result()
        res.video_path = output_path
        mock_adapter.render.return_value = res
        MockAdapter.return_value = mock_adapter

        result = run_pipeline(storyboard_json, fake_provider, adapter=None)

    assert result.status == "TECHNICAL_PASS"
    MockAdapter.assert_called_once()


# ── PL-03a: CLI backward compat ──────────────────────────────────────


def test_pl_03a_cli_backward_compat(storyboard_json, tmp_path):
    from pipeline.cli import main
    import sys

    output_path = str(tmp_path / "output.mp4")
    tmp_path.joinpath("output.mp4").write_bytes(b"\x00" * 500000)

    with (
        patch("pipeline.providers.gtts.GTTSAdapter") as MockProvider,
        patch("pipeline.cli.FFmpegPillowAdapter") as MockAdapter,
        patch.object(sys, "argv", ["pipeline", storyboard_json]),
    ):
        mock_provider = MagicMock()
        mock_provider.name = "gtts"
        mock_provider.generate.return_value = NarrationInput(
            audio_path=storyboard_json, duration_seconds=10.0, source="tts",
        )
        MockProvider.return_value = mock_provider

        res = _make_success_result()
        res.video_path = output_path
        mock_adapter = MagicMock()
        mock_adapter.render.return_value = res
        MockAdapter.return_value = mock_adapter

        main()


# ── PL-04a: PipelineResult estructura correcta ───────────────────────


def test_pl_04a_pipeline_result_structure():
    from pipeline.cli import PipelineResult
    from adapters.base import RenderResult

    render_result = _make_success_result()
    pr = PipelineResult(
        run_id="test-001",
        status="TECHNICAL_PASS",
        total_time=10.5,
        tts_time=2.3,
        narration_duration=10.0,
        result=render_result,
        manifest_path="/runs/test-001/manifest.json",
    )

    assert pr.run_id == "test-001"
    assert pr.status == "TECHNICAL_PASS"
    assert isinstance(pr.total_time, float)
    assert isinstance(pr.tts_time, float)
    assert isinstance(pr.narration_duration, float)
    assert isinstance(pr.result, RenderResult)
    assert isinstance(pr.manifest_path, str)
    assert pr.total_time >= pr.tts_time
