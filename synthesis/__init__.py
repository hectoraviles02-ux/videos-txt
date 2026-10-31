"""Content synthesis: Ollama script generation, storyboard building, TTS, and pipeline orchestration."""

from synthesis.pipeline import SynthesisPipeline
from synthesis.script_generator import (
    OllamaConnectionError,
    ScriptGenerationError,
    ScriptGenerator,
)
from synthesis.storyboard_builder import build_from_script, process, validate
from synthesis.tts_provider import EdgeTTSProvider, GTTSProvider, TTSProvider

__all__ = [
    "EdgeTTSProvider",
    "GTTSProvider",
    "OllamaConnectionError",
    "ScriptGenerationError",
    "ScriptGenerator",
    "SynthesisPipeline",
    "TTSProvider",
    "build_from_script",
    "process",
    "validate",
]
