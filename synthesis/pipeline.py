from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from adapters.base import (
    NarrationInput,
    RenderConfig,
    RunContext,
    Storyboard,
    VideoAdapter,
)
from synthesis.script_generator import ScriptGenerator
from synthesis.storyboard_builder import build_from_script

logger = logging.getLogger(__name__)


class SynthesisPipeline:
    """Orchestrates transcript-to-video synthesis.

    Flow:
        transcript text
        → ScriptGenerator.generate_script() → JSON script
        → build_from_script() → Storyboard
        → TTSProvider.synthesize() → audio file
        → VideoAdapter.render() → video file
    """

    def __init__(
        self,
        script_generator: ScriptGenerator | None = None,
        tts_provider: Any | None = None,
        video_adapter: VideoAdapter | None = None,
    ):
        self._script_generator = script_generator or ScriptGenerator()
        self._tts_provider = tts_provider
        self._video_adapter = video_adapter

    @property
    def script_generator(self) -> ScriptGenerator:
        return self._script_generator

    @script_generator.setter
    def script_generator(self, value: ScriptGenerator) -> None:
        self._script_generator = value

    @property
    def tts_provider(self) -> Any | None:
        return self._tts_provider

    @tts_provider.setter
    def tts_provider(self, value: Any) -> None:
        self._tts_provider = value

    @property
    def video_adapter(self) -> VideoAdapter | None:
        return self._video_adapter

    @video_adapter.setter
    def video_adapter(self, value: VideoAdapter | None) -> None:
        self._video_adapter = value

    def run(
        self,
        transcript: str,
        output_dir: str | Path,
        *,
        style: str = "youtube",
        total_duration_seconds: int = 300,
        resolution: str = "1920x1080",
        fps: int = 30,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Run the full synthesis pipeline.

        Args:
            transcript: Plain text content to synthesize.
            output_dir: Directory for all output artifacts.
            style: Script style (``"youtube"``, ``"tutorial"``, ``"narrative"``).
            total_duration_seconds: Target video duration.
            resolution: Video resolution (e.g. ``"1920x1080"``).
            fps: Frames per second.
            run_id: Unique run identifier (auto-generated if omitted).

        Returns:
            Dict with keys:
            - ``storyboard`` (Storyboard): built storyboard
            - ``script_json`` (str): raw script JSON
            - ``audio_path`` (str | None): path to generated audio
            - ``video_path`` (str | None): path to rendered video
            - ``status`` (str): ``"success"`` or ``"failed"``
            - ``error`` (str | None): error message if failed
            - ``duration_seconds`` (float): wall-clock time
        """
        t0 = time.time()
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        _run_id = run_id or f"run-{int(t0)}"

        result: dict[str, Any] = {
            "storyboard": None,
            "script_json": None,
            "audio_path": None,
            "video_path": None,
            "status": "failed",
            "error": None,
            "duration_seconds": 0.0,
        }

        # ── Step 1: Generate script ──────────────────────────────────
        try:
            logger.info("Step 1/3: Generating script (style=%s, %d chars)", style, len(transcript))
            script_json_str = self._script_generator.generate_script(
                transcript, style=style,
            )
            script = __import__("json").loads(script_json_str)
            result["script_json"] = script_json_str
        except Exception as e:
            result["error"] = f"Script generation failed: {e}"
            result["duration_seconds"] = time.time() - t0
            return result

        # ── Step 2: Build storyboard ─────────────────────────────────
        try:
            logger.info("Step 2/3: Building storyboard")
            storyboard = build_from_script(
                script,
                total_duration_seconds=total_duration_seconds,
            )
            result["storyboard"] = storyboard
        except Exception as e:
            result["error"] = f"Storyboard build failed: {e}"
            result["duration_seconds"] = time.time() - t0
            return result

        # ── Step 3: Generate audio (if TTS provider is configured) ────
        if self._tts_provider is not None:
            try:
                logger.info("Step 3/3a: Synthesizing audio via %s", self._tts_provider.name)
                audio_path = output_path / "narration"
                generated = self._tts_provider.synthesize(
                    " ".join(s.narration for s in storyboard.scenes),
                    audio_path,
                )
                result["audio_path"] = str(generated)
            except Exception as e:
                logger.warning("TTS failed, continuing without audio: %s", e)
                result["audio_path"] = None
        else:
            logger.info("No TTS provider configured, skipping audio")

        # ── Step 4: Render video (if adapter is configured) ──────────
        if self._video_adapter is not None:
            try:
                logger.info("Step 3/3b: Rendering video via %s", self._video_adapter.name)
                narration = NarrationInput(
                    audio_path=result.get("audio_path") or "",
                    duration_seconds=total_duration_seconds,
                    source="tts",
                )
                render_config = RenderConfig(
                    resolution=resolution,
                    fps=fps,
                    output_filename="output.mp4",
                )
                run_context = RunContext(
                    run_id=_run_id,
                    output_dir=str(output_path / "render"),
                    log_dir=str(output_path / "logs"),
                )
                render_result = self._video_adapter.render(
                    storyboard, narration, render_config, run_context,
                )
                if render_result.status == "success" and render_result.video_path:
                    result["video_path"] = render_result.video_path
                else:
                    result["error"] = (
                        f"Video render failed: "
                        f"{render_result.error.get('message', 'unknown') if render_result.error else 'unknown'}"
                    )
                    result["duration_seconds"] = time.time() - t0
                    return result
            except Exception as e:
                result["error"] = f"Video render error: {e}"
                result["duration_seconds"] = time.time() - t0
                return result
        else:
            logger.info("No video adapter configured, skipping render")

        result["status"] = "success"
        result["duration_seconds"] = time.time() - t0
        logger.info(
            "Pipeline completed in %.2fs → %s",
            result["duration_seconds"],
            result.get("video_path") or result.get("audio_path") or "script only",
        )
        return result
