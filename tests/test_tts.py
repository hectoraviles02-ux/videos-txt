import os
from unittest.mock import MagicMock, patch

import pytest

from adapters.base import NarrationInput, Storyboard, StoryboardScene
from pipeline.tts_protocol import TTSProvider


@pytest.fixture
def sample_storyboard() -> Storyboard:
    return Storyboard(
        storyboard_version="1.0",
        metadata={
            "source_script": "t", "title": "T",
            "total_duration_seconds": 15, "scene_count": 2,
            "generated_at": "2026-07-25T12:00:00Z",
        },
        scenes=[
            StoryboardScene(
                id="scene-001", type="hook", title="Intro",
                start_seconds=0.0, end_seconds=8.0,
                narration="Bienvenidos a este video.",
                visual={"components": [{"type": "solid_color", "color": "#000000"}]},
            ),
            StoryboardScene(
                id="scene-002", type="closing", title="Cierre",
                start_seconds=8.0, end_seconds=15.0,
                narration="Gracias por vernos.",
                visual={"components": [{"type": "solid_color", "color": "#000000"}]},
            ),
        ],
    )


# ── Contrato TTSProvider ────────────────────────────────────────────

def test_gtts_adapter_is_tts_provider():
    from pipeline.providers.gtts import GTTSAdapter
    assert isinstance(GTTSAdapter(), TTSProvider)


def test_tts_provider_is_runtime_checkable():
    class FakeProvider:
        @property
        def name(self) -> str:
            return "fake"
        def generate(self, storyboard, output_dir):
            return NarrationInput(
                audio_path="/fake.wav", duration_seconds=10.0, source="tts",
            )
    assert isinstance(FakeProvider(), TTSProvider)


# ── gTTS adapter ────────────────────────────────────────────────────

def test_gtts_adapter_name():
    from pipeline.providers.gtts import GTTSAdapter
    assert GTTSAdapter().name == "gtts"


def test_gtts_generate_returns_narration_input(sample_storyboard, tmp_path):
    from pipeline.providers.gtts import GTTSAdapter

    adapter = GTTSAdapter()
    with (
        patch("gtts.gTTS") as mock_gtts,
        patch("pipeline.providers.gtts.subprocess.run"),
        patch("pipeline.providers.gtts._get_wav_duration", return_value=15.0),
    ):
        mock_gtts.return_value = MagicMock()
        result = adapter.generate(sample_storyboard, str(tmp_path))

    assert isinstance(result, NarrationInput)
    assert result.source == "tts"
    assert result.tts_engine == "gtts"
    assert result.duration_seconds == 15.0
    assert result.audio_path.endswith("narration.wav")
    assert result.text_hash is not None


def test_gtts_generate_concatenates_all_text(sample_storyboard, tmp_path):
    from pipeline.providers.gtts import GTTSAdapter

    adapter = GTTSAdapter()
    with (
        patch("gtts.gTTS") as mock_gtts,
        patch("pipeline.providers.gtts.subprocess.run"),
        patch("pipeline.providers.gtts._get_wav_duration", return_value=15.0),
    ):
        mock_gtts.return_value = MagicMock()
        adapter.generate(sample_storyboard, str(tmp_path))

    text_arg = mock_gtts.call_args[1]["text"]
    assert "Bienvenidos" in text_arg
    assert "Gracias" in text_arg


def test_gtts_generate_passes_lang_and_tld(sample_storyboard, tmp_path):
    from pipeline.providers.gtts import GTTSAdapter

    adapter = GTTSAdapter(lang="en", tld="com.au")
    with (
        patch("gtts.gTTS") as mock_gtts,
        patch("pipeline.providers.gtts.subprocess.run"),
        patch("pipeline.providers.gtts._get_wav_duration", return_value=15.0),
    ):
        mock_gtts.return_value = MagicMock()
        adapter.generate(sample_storyboard, str(tmp_path))

    assert mock_gtts.call_args[1]["lang"] == "en"
    assert mock_gtts.call_args[1]["tld"] == "com.au"


def test_gtts_generate_empty_storyboard_raises(tmp_path):
    from pipeline.providers.gtts import GTTSAdapter

    sb = Storyboard(
        storyboard_version="1.0",
        metadata={"source_script": "t", "title": "T", "total_duration_seconds": 5, "scene_count": 1, "generated_at": "2026-07-25T12:00:00Z"},
        scenes=[
            StoryboardScene(
                id="scene-001", type="hook", title="T",
                start_seconds=0.0, end_seconds=5.0,
                narration="   ",
                visual={"components": [{"type": "solid_color", "color": "#000000"}]},
            ),
        ],
    )
    adapter = GTTSAdapter()
    with pytest.raises(RuntimeError, match="no narration text"):
        adapter.generate(sb, str(tmp_path))


def test_gtts_generate_gtts_failure(sample_storyboard, tmp_path):
    from pipeline.providers.gtts import GTTSAdapter

    adapter = GTTSAdapter()
    with patch("gtts.gTTS", side_effect=Exception("API error")):
        with pytest.raises(RuntimeError, match="gTTS failed"):
            adapter.generate(sample_storyboard, str(tmp_path))


def test_gtts_generate_ffmpeg_conversion_failure(sample_storyboard, tmp_path):
    from pipeline.providers.gtts import GTTSAdapter

    adapter = GTTSAdapter()
    with (
        patch("gtts.gTTS") as mock_gtts,
        patch("pipeline.providers.gtts.subprocess.run",
              side_effect=__import__("subprocess").CalledProcessError(1, ["ffmpeg"])),
    ):
        mock_gtts.return_value = MagicMock()
        with pytest.raises(RuntimeError):
            adapter.generate(sample_storyboard, str(tmp_path))


# ── Utilidad interna _get_wav_duration ──────────────────────────────

def test_get_wav_duration_from_wave(tmp_path):
    from pipeline.providers.gtts import _get_wav_duration
    from tests.fixtures import make_wav

    wav_path = make_wav(str(tmp_path / "test.wav"), duration_seconds=3.0)
    d = _get_wav_duration(wav_path)
    assert abs(d - 3.0) < 0.1


def test_get_wav_duration_zero_on_failure(tmp_path):
    from pipeline.providers.gtts import _get_wav_duration

    d = _get_wav_duration(str(tmp_path / "nonexistent.wav"))
    assert d == 0.0
