"""Helpers para pruebas del adaptador ffmpeg-pillow."""

import json
import struct
import wave
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent
STORYBOARD_PATH = FIXTURES_DIR / "storyboard_minimal.json"


def load_storyboard_fixture() -> dict:
    with open(STORYBOARD_PATH) as f:
        return json.load(f)


def make_wav(path: str, duration_seconds: float = 1.0, sample_rate: int = 44100):
    """Generate a valid WAV file with silence for testing."""
    n_frames = int(sample_rate * duration_seconds)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * n_frames)
    return path


def make_tone_wav(
    path: str,
    duration_seconds: float = 1.0,
    frequency: float = 880.0,
    sample_rate: int = 44100,
):
    """WAV con un tono audible. A diferencia de make_wav (silencio), sirve para
    comprobar que un audio realmente llego a la mezcla y no solo que existe."""
    import math

    n_frames = int(sample_rate * duration_seconds)
    amplitude = 16000
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        frames = b"".join(
            struct.pack(
                "<h",
                int(amplitude * math.sin(2 * math.pi * frequency * i / sample_rate)),
            )
            for i in range(n_frames)
        )
        w.writeframes(frames)
    return path


def make_image(path: str, width: int = 1920, height: int = 1080, color: tuple = (100, 150, 200)):
    """Generate a solid-color PNG for testing."""
    from PIL import Image
    img = Image.new("RGB", (width, height), color)
    img.save(path, "PNG")
    return path


def patch_storyboard_image_ref(storyboard: dict, image_path: str) -> dict:
    """Replace __TEST_IMAGE__ placeholder with actual path."""
    import copy
    sb = copy.deepcopy(storyboard)
    for scene in sb["scenes"]:
        for comp in scene["visual"]["components"]:
            if comp.get("source") == "__TEST_IMAGE__":
                comp["source"] = image_path
    return sb
