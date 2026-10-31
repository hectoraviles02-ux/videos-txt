from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from adapters.base import NarrationInput
from adapters.ffmpeg_tools import resolve_ffmpeg

logger = logging.getLogger(__name__)


@runtime_checkable
class TTSProvider(Protocol):
    """Contract any TTS provider must implement."""

    @property
    def name(self) -> str:
        ...

    def synthesize(self, text: str, output_path: Path) -> Path:
        """Synthesize text into an audio file.

        Args:
            text: Text to synthesize.
            output_path: Desired output path (extension determines format).

        Returns:
            Path to the generated audio file.

        Raises:
            RuntimeError: If synthesis fails.
        """
        ...


class GTTSProvider:
    """gTTS-based TTS provider for Spanish voice synthesis."""

    def __init__(
        self,
        lang: str = "es",
        tld: str = "com",
        slow: bool = False,
    ):
        self._lang = lang
        self._tld = tld
        self._slow = slow

    @property
    def name(self) -> str:
        return "gtts"

    def synthesize(self, text: str, output_path: Path) -> Path:
        from gtts import gTTS

        output_path = output_path.with_suffix(".mp3")
        try:
            tts = gTTS(text=text, lang=self._lang, tld=self._tld, slow=self._slow)
            tts.save(str(output_path))
        except Exception as e:
            raise RuntimeError(f"gTTS failed: {e}")

        logger.info(
            "gTTS synthesized %d chars -> %s",
            len(text),
            output_path,
        )
        return output_path

    def generate(self, storyboard: Any, output_dir: str) -> NarrationInput:
        """Produce NarrationInput from a storyboard (compat with TTSProvider protocol in pipeline)."""
        full_text = " ".join(scene.narration for scene in storyboard.scenes)
        wav_path = Path(output_dir) / "narration.wav"
        mp3_path = self.synthesize(full_text, wav_path.with_suffix(".mp3"))

        ffmpeg = resolve_ffmpeg()
        try:
            subprocess.run(
                [
                    ffmpeg, "-y",
                    "-i", str(mp3_path),
                    "-acodec", "pcm_s16le",
                    "-ar", "44100",
                    "-ac", "1",
                    str(wav_path),
                ],
                capture_output=True, text=True, timeout=120,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"ffmpeg MP3->WAV conversion failed: {e.stderr}")
        except subprocess.TimeoutExpired:
            raise RuntimeError("ffmpeg MP3->WAV conversion timed out")
        finally:
            if mp3_path.exists():
                mp3_path.unlink()

        import hashlib, wave

        duration = _get_wav_duration(str(wav_path))
        if duration <= 0:
            raise RuntimeError("Failed to determine narration duration")

        text_hash = hashlib.sha256(full_text.encode()).hexdigest()[:16]
        return NarrationInput(
            audio_path=str(wav_path),
            duration_seconds=duration,
            source="tts",
            tts_engine="gtts",
            text_hash=text_hash,
        )


class EdgeTTSProvider:
    """Edge-TTS provider for more natural-sounding voice synthesis.

    Requires the ``edge-tts`` package (optional dependency).
    """

    def __init__(
        self,
        voice: str = "es-MX-JorgeNeural",
        rate: str = "+0%",
        volume: str = "+0%",
    ):
        self._voice = voice
        self._rate = rate
        self._volume = volume

    @property
    def name(self) -> str:
        return "edge-tts"

    def synthesize(self, text: str, output_path: Path) -> Path:
        try:
            import edge_tts
        except ImportError:
            raise RuntimeError(
                "edge-tts is not installed. Run: pip install edge-tts"
            )

        output_path = output_path.with_suffix(".mp3")
        communicate = edge_tts.Communicate(
            text,
            voice=self._voice,
            rate=self._rate,
            volume=self._volume,
        )

        import asyncio

        try:
            asyncio.run(communicate.save(str(output_path)))
        except Exception as e:
            raise RuntimeError(f"Edge-TTS failed: {e}")

        logger.info(
            "Edge-TTS synthesized %d chars -> %s (voice=%s)",
            len(text),
            output_path,
            self._voice,
        )
        return output_path

    def generate(self, storyboard: Any, output_dir: str) -> NarrationInput:
        """Produce NarrationInput from a storyboard (compat with TTSProvider protocol in pipeline)."""
        full_text = " ".join(scene.narration for scene in storyboard.scenes)
        wav_path = Path(output_dir) / "narration.wav"
        mp3_path = self.synthesize(full_text, wav_path.with_suffix(".mp3"))

        ffmpeg = resolve_ffmpeg()
        try:
            subprocess.run(
                [
                    ffmpeg, "-y",
                    "-i", str(mp3_path),
                    "-acodec", "pcm_s16le",
                    "-ar", "44100",
                    "-ac", "1",
                    str(wav_path),
                ],
                capture_output=True, text=True, timeout=120,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"ffmpeg MP3->WAV failed: {e.stderr}")
        except subprocess.TimeoutExpired:
            raise RuntimeError("ffmpeg MP3->WAV conversion timed out")
        finally:
            if mp3_path.exists():
                mp3_path.unlink()

        import hashlib, wave

        duration = _get_wav_duration(str(wav_path))
        if duration <= 0:
            raise RuntimeError("Failed to determine narration duration")

        text_hash = hashlib.sha256(full_text.encode()).hexdigest()[:16]
        return NarrationInput(
            audio_path=str(wav_path),
            duration_seconds=duration,
            source="tts",
            tts_engine="edge-tts",
            text_hash=text_hash,
        )


def _get_wav_duration(wav_path: str) -> float:
    import wave

    try:
        with wave.open(wav_path, "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
            if rate > 0:
                return frames / rate
    except Exception:
        pass
    return 0.0
