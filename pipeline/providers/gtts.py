"""Adaptador gTTS — implementa TTSProvider usando Google Text-to-Speech.

gTTS genera MP3. Este adaptador convierte internamente a WAV mediante
FFmpeg para mantener un formato universal. Si en el futuro un proveedor
genera WAV nativamente (Piper, etc.), ese paso se omite.

El resto del sistema NO conoce este detalle interno.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import wave
from typing import Any

from adapters.base import NarrationInput
from adapters.ffmpeg_tools import resolve_ffmpeg


class GTTSAdapter:
    """Adaptador TTS que usa gTTS + FFmpeg (WAV como formato interno)."""

    def __init__(
        self,
        lang: str = "es",
        tld: str = "com",
        slow: bool = False,
        ffmpeg_path: str | None = None,
    ):
        self._lang = lang
        self._tld = tld
        self._slow = slow
        self._ffmpeg_path = ffmpeg_path

    @property
    def name(self) -> str:
        return "gtts"

    def generate(
        self,
        storyboard: Any,
        output_dir: str,
    ) -> NarrationInput:
        from gtts import gTTS

        os.makedirs(output_dir, exist_ok=True)

        full_text = " ".join(scene.narration for scene in storyboard.scenes)
        if not full_text.strip():
            raise RuntimeError("Storyboard has no narration text")

        # Paso 1: gTTS genera MP3
        mp3_path = os.path.join(output_dir, "_gtts_raw.mp3")
        try:
            tts = gTTS(text=full_text, lang=self._lang, tld=self._tld, slow=self._slow)
            tts.save(mp3_path)
        except Exception as e:
            raise RuntimeError(f"gTTS failed: {e}")

        # Paso 2: FFmpeg convierte MP3 → WAV (detalle interno del adaptador)
        wav_path = os.path.join(output_dir, "narration.wav")
        ffmpeg = resolve_ffmpeg(self._ffmpeg_path)

        try:
            subprocess.run(
                [
                    ffmpeg, "-y",
                    "-i", mp3_path,
                    "-acodec", "pcm_s16le",
                    "-ar", "44100",
                    "-ac", "1",
                    wav_path,
                ],
                capture_output=True, text=True, timeout=120,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"ffmpeg MP3->WAV conversion failed: {e.stderr}")
        except subprocess.TimeoutExpired:
            raise RuntimeError("ffmpeg MP3->WAV conversion timed out")
        finally:
            if os.path.exists(mp3_path):
                os.remove(mp3_path)

        duration = _get_wav_duration(wav_path)
        if duration <= 0:
            raise RuntimeError("Failed to determine narration duration")

        expected = _get_storyboard_duration(storyboard)
        if duration < expected:
            padded = min(expected, duration + 2.0)
            _pad_wav_with_silence(wav_path, padded)
            duration = padded

        text_hash = hashlib.sha256(full_text.encode()).hexdigest()[:16]

        return NarrationInput(
            audio_path=wav_path,
            duration_seconds=duration,
            source="tts",
            tts_engine="gtts",
            text_hash=text_hash,
        )


def _get_wav_duration(wav_path: str) -> float:
    try:
        with wave.open(wav_path, "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
            if rate > 0:
                return frames / rate
    except Exception:
        pass
    return 0.0


def _get_storyboard_duration(storyboard: Any) -> float:
    total = storyboard.metadata.get("total_duration_seconds")
    if total:
        return float(total)
    return float(storyboard.scenes[-1].end_seconds)


def _pad_wav_with_silence(wav_path: str, target_duration: float) -> None:
    import struct

    with wave.open(wav_path, "rb") as w:
        params = w.getparams()
        frames = w.readframes(w.getnframes())

    current = params.nframes / params.framerate if params.framerate > 0 else 0
    if current >= target_duration:
        return

    pad_frames = int((target_duration - current) * params.framerate)
    silence = b"\x00\x00" * pad_frames if params.sampwidth == 2 else b"\x00" * (pad_frames * params.sampwidth)

    with wave.open(wav_path, "wb") as w:
        w.setparams(params)
        w.writeframes(frames + silence)
