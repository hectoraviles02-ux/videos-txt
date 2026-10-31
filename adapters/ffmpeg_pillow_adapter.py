from __future__ import annotations

import hashlib
import os

from adapters.base import (
    AdapterRenderError,
    AdapterValidationError,
    NarrationInput,
    RenderConfig,
    RenderResult,
    RunContext,
    Storyboard,
    VideoAdapter,
)
from adapters.ffmpeg_tools import (
    compose_video,
    probe_video,
    quality_gate,
    resolve_ffmpeg,
    resolve_ffprobe,
)
from adapters.scene_renderer import SceneRenderer


class FFmpegPillowAdapter(VideoAdapter):
    def __init__(
        self,
        ffmpeg_path: str | None = None,
        ffprobe_path: str | None = None,
        font_path: str | None = None,
    ):
        self._ffmpeg_path = ffmpeg_path or self._find_local_tool("ffmpeg.exe")
        self._ffprobe_path = ffprobe_path or self._find_local_tool("ffprobe.exe")
        self._font_path = font_path

    @property
    def name(self) -> str:
        return "ffmpeg-pillow"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def deterministic(self) -> bool:
        return True

    def render(
        self,
        storyboard: Storyboard,
        narration: NarrationInput,
        config: RenderConfig,
        context: RunContext,
    ) -> RenderResult:
        import time
        t0 = time.time()

        output_dir = os.path.abspath(context.output_dir)
        log_dir = os.path.abspath(context.log_dir)
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(log_dir, exist_ok=True)

        resolution = _parse_resolution(config.resolution)
        renderer = SceneRenderer(
            resolution=resolution,
            font_path=self._font_path or _find_default_font(),
        )

        scene_paths: list[str] = []
        scene_durations: list[float] = []

        try:
            self.validate_inputs(storyboard, narration, config)
            self._validate_engine_available()

            for scene in storyboard.scenes:
                duration = scene.end_seconds - scene.start_seconds
                scene_path = os.path.join(
                    output_dir, f"{scene.id}.png"
                )
                if os.path.exists(scene_path):
                    raise AdapterValidationError(
                        f"Output already exists: {scene_path}"
                    )
                renderer.render_scene(scene, scene_path)
                scene_paths.append(scene_path)
                scene_durations.append(duration)

            output_path = os.path.join(output_dir, config.output_filename)

            comp_result = compose_video(
                scene_paths=scene_paths,
                scene_durations=scene_durations,
                narration_path=narration.audio_path,
                output_path=output_path,
                resolution=resolution,
                fps=config.fps,
                ffmpeg_path=self._ffmpeg_path,
                log_dir=log_dir,
                timeout=config.max_duration_seconds,
                background_music_path=config.background_music,
                background_music_volume=config.background_music_volume,
                ffprobe_path=self._ffprobe_path,
                subtitles_path=config.engine_specific.get("subtitles_path"),
            )

            if not comp_result["success"]:
                return RenderResult(
                    status="failed",
                    error={
                        "message": comp_result.get("error", "ffmpeg composition failed"),
                        "exception_type": "AdapterRenderError",
                        "details": comp_result.get("stderr", ""),
                    },
                    engine_info={"name": self.name, "version": self.version},
                    duration_seconds=time.time() - t0,
                )

            video_path = comp_result["output_path"]
            video_hash = _sha256(video_path)
            probe = probe_video(video_path, self._ffprobe_path)

            if "error" in probe:
                return RenderResult(
                    status="failed",
                    error={
                        "message": f"ffprobe failed: {probe['error']}",
                        "exception_type": "AdapterRenderError",
                    },
                    engine_info={"name": self.name, "version": self.version},
                    duration_seconds=time.time() - t0,
                )

            from adapters.base import ValidationReport

            qg = quality_gate(
                probe,
                expected_resolution=resolution,
                expected_fps=config.fps,
                expected_duration_seconds=storyboard.metadata.get("total_duration_seconds", 0),
            )

            validation = ValidationReport(
                codec_check=qg["codec_check"],
                resolution_check=qg["resolution_check"],
                fps_check=qg["fps_check"],
                duration_check=qg["duration_check"],
                audio_check={
                    "pass": qg["audio_check"]["pass"],
                    "channels": qg["audio_check"]["channels"],
                    "sample_rate": qg["audio_check"]["sample_rate"],
                    "audio_codec": qg["audio_codec_check"]["actual"],
                },
                file_integrity=qg["file_integrity"],
                min_frame_check={"pass": True, "message": "No frame integrity check"},
            )

            result = RenderResult(
                status="success",
                video_path=video_path,
                video_size_bytes=probe.get("size_bytes", 0),
                video_duration_seconds=probe.get("duration_seconds", 0.0),
                video_hash=video_hash,
                actual_resolution=qg["resolution_check"]["actual"],
                actual_fps=probe.get("fps", 0.0),
                validation=validation,
                warnings=comp_result.get("warnings", []),
                engine_info={
                    "name": self.name,
                    "version": self.version,
                    # El quality gate no distingue un video con música de uno sin
                    # ella: en ambos casos hay pista de audio. Se declara aquí.
                    "bgm_applied": comp_result.get("bgm_applied", False),
                },
                duration_seconds=time.time() - t0,
            )

            self.validate_result(result)
            return result

        except (AdapterValidationError, AdapterRenderError, Exception) as e:
            return RenderResult(
                status="failed",
                error={
                    "message": str(e),
                    "exception_type": type(e).__name__,
                },
                engine_info={"name": self.name, "version": self.version},
                duration_seconds=time.time() - t0,
            )

    def _validate_engine_available(self) -> None:
        try:
            resolve_ffmpeg(self._ffmpeg_path)
        except RuntimeError as e:
            raise AdapterRenderError(str(e))
        try:
            resolve_ffprobe(self._ffprobe_path)
        except RuntimeError as e:
            raise AdapterRenderError(str(e))
        try:
            from PIL import Image
        except ImportError as e:
            raise AdapterRenderError(f"Pillow not installed: {e}")

    @staticmethod
    def _find_local_tool(name: str) -> str | None:
        local = os.path.join(os.path.dirname(__file__), "..", "bin", name)
        if os.path.isfile(local):
            return local
        return None


def _parse_resolution(resolution_str: str) -> tuple[int, int]:
    parts = resolution_str.split("x")
    if len(parts) != 2:
        raise AdapterValidationError(f"Invalid resolution format: {resolution_str}")
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        raise AdapterValidationError(f"Invalid resolution values: {resolution_str}")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_default_font() -> str | None:
    candidates = [
        "C:\\Windows\\Fonts\\arial.ttf",
        "C:\\Windows\\Fonts\\segoeui.ttf",
        "C:\\Windows\\Fonts\\calibri.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None
