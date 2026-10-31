"""Adaptador Edge-TTS — implementa TTSProvider usando Microsoft Edge TTS.

Voces neurales gratuitas, mucha mejor calidad que gTTS.
Soporta español mexicano (es-MX-JorgeNeural, es-MX-DaliaNeural).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import wave
from typing import Any

from adapters.base import NarrationInput
from adapters.ffmpeg_tools import resolve_ffmpeg


class EdgeTTSAdapter:
    """Adaptador TTS que usa edge-tts + FFmpeg (WAV como formato interno)."""

    def __init__(
        self,
        voice: str = "es-MX-JorgeNeural",
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
        ffmpeg_path: str | None = None,
    ):
        self._voice = voice
        self._rate = rate
        self._volume = volume
        self._pitch = pitch
        self._ffmpeg_path = ffmpeg_path

    @property
    def name(self) -> str:
        return f"edge-tts-{self._voice}"

    def generate(
        self,
        storyboard: Any,
        output_dir: str,
    ) -> NarrationInput:
        os.makedirs(output_dir, exist_ok=True)

        full_text = " ".join(scene.narration for scene in storyboard.scenes)
        if not full_text.strip():
            raise RuntimeError("Storyboard has no narration text")

        mp3_path = os.path.join(output_dir, "_edge_raw.mp3")

        try:
            asyncio.run(
                self._run_edge_tts(full_text, mp3_path)
            )
        except Exception as e:
            raise RuntimeError(f"Edge-TTS failed: {e}")

        if not os.path.isfile(mp3_path) or os.path.getsize(mp3_path) == 0:
            raise RuntimeError("Edge-TTS produced empty output")

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
            tts_engine=f"edge-tts-{self._voice}",
            text_hash=text_hash,
        )

    async def _run_edge_tts(self, text: str, output_path: str) -> None:
        import edge_tts
        communicate = edge_tts.Communicate(
            text,
            self._voice,
            rate=self._rate,
            volume=self._volume,
            pitch=self._pitch,
        )
        await communicate.save(output_path)


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
