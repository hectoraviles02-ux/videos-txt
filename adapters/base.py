from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StoryboardScene:
    id: str
    type: str
    title: str
    start_seconds: float
    end_seconds: float
    narration: str
    visual: dict[str, Any]
    notes: str = ""
    source_refs: list[str] = field(default_factory=list)


@dataclass
class Storyboard:
    storyboard_version: str
    metadata: dict[str, Any]
    scenes: list[StoryboardScene]

    @classmethod
    def from_dict(cls, data: dict) -> Storyboard:
        scenes = [StoryboardScene(**s) for s in data["scenes"]]
        return cls(
            storyboard_version=data["storyboard_version"],
            metadata=data["metadata"],
            scenes=scenes,
        )

    @classmethod
    def from_json(cls, path: str) -> Storyboard:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)


@dataclass
class NarrationInput:
    audio_path: str
    duration_seconds: float
    source: str  # "tts" | "recorded"
    tts_engine: str | None = None
    text_hash: str | None = None
    # Cuanto duro CADA escena, si el proveedor lo sabe. Los que sintetizan el
    # texto de corrido no pueden saberlo y lo dejan en None; los que van escena
    # por escena si, y con eso los cortes de imagen y los subtitulos caen donde
    # termina la frase en vez de repartirse a partes iguales.
    duraciones_por_escena: list[float] | None = None


@dataclass
class RenderConfig:
    resolution: str = "1920x1080"
    fps: int = 30
    background_music: str | None = None
    background_music_volume: float = 0.1
    transition: str = "fade"
    # Era 600 -diez minutos-. No habia razon tecnica: ffmpeg no tiene ese
    # limite y el unico efecto era cortar documentales largos. Se usa como
    # timeout de ffmpeg, asi que ahora es holgado y no un tope de duracion.
    max_duration_seconds: int = 7200
    output_filename: str = "output.mp4"
    thumbnail_frame: float | None = None
    language: str = "es"
    engine_specific: dict[str, Any] = field(default_factory=dict)
    """Engine-specific overrides.
    Known keys:
      - ``"image_provider"`` (str): image backend to use, e.g. ``"sd"``,
        ``"ffmpeg-pillow"``.
    """


@dataclass
class ValidationReport:
    codec_check: dict[str, Any]
    resolution_check: dict[str, Any]
    fps_check: dict[str, Any]
    duration_check: dict[str, Any]
    audio_check: dict[str, Any]
    file_integrity: dict[str, Any]
    min_frame_check: dict[str, Any]

    @property
    def all_pass(self) -> bool:
        checks = [
            self.codec_check,
            self.resolution_check,
            self.fps_check,
            self.duration_check,
            self.audio_check,
            self.file_integrity,
            self.min_frame_check,
        ]
        return all(c.get("pass", False) for c in checks)


@dataclass
class RenderResult:
    status: str  # "success" | "failed"
    video_path: str | None = None
    video_size_bytes: int | None = None
    video_duration_seconds: float | None = None
    video_hash: str | None = None
    actual_resolution: str | None = None
    actual_fps: float | None = None
    validation: ValidationReport | None = None
    warnings: list[str] = field(default_factory=list)
    error: dict[str, Any] | None = None
    engine_info: dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 0.0

    def validate(self) -> None:
        """Valida invariantes del resultado (success/failed)."""
        if self.status == "success":
            _require("video_path", self.video_path)
            _require("video_size_bytes", self.video_size_bytes)
            _require("video_hash", self.video_hash)
            _require("validation", self.validation)
            if self.validation is not None and not self.validation.all_pass:
                raise AdapterValidationError(
                    "RenderResult status=success but validation checks failed"
                )
            if self.error is not None:
                raise AdapterValidationError(
                    "RenderResult status=success but error is not None"
                )
        elif self.status == "failed":
            if self.error is None:
                raise AdapterValidationError(
                    "RenderResult status=failed but error is None"
                )
            if "message" not in self.error:
                raise AdapterValidationError(
                    "RenderResult error must contain 'message'"
                )
            if self.video_path is not None:
                raise AdapterValidationError(
                    "RenderResult status=failed but video_path is not None"
                )
        else:
            raise AdapterValidationError(
                f"Invalid RenderResult status: {self.status!r}. "
                f"Must be 'success' or 'failed'."
            )


def _require(name: str, value: Any) -> None:
    if value is None:
        raise AdapterValidationError(
            f"RenderResult status=success requires {name}"
        )


@dataclass
class RunContext:
    run_id: str
    output_dir: str
    log_dir: str
    config_path: str | None = None


class AdapterValidationError(Exception):
    """Inputs inválidos o incompletos para el render."""


class AdapterRenderError(Exception):
    """El motor falló durante la ejecución del render."""


class AdapterTimeoutError(Exception):
    """El render excedió el tiempo máximo permitido."""


class UnsupportedComponentError(AdapterValidationError):
    """Tipo de componente visual no soportado por el adaptador."""


class VideoAdapter(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Nombre único del motor (ej. 'scholium', 'ffmpeg-slides')."""
        ...

    @property
    @abstractmethod
    def version(self) -> str:
        """Versión del adaptador (semver)."""
        ...

    @property
    def deterministic(self) -> bool:
        """``True`` si el adaptador garantiza output reproducible bit-a-bit."""
        return True

    @abstractmethod
    def render(
        self,
        storyboard: Storyboard,
        narration: NarrationInput,
        config: RenderConfig,
        context: RunContext,
    ) -> RenderResult:
        """Ejecuta el render y produce video + metadata + validación.

        DEBE llamar ``self.validate_inputs(storyboard, narration, config)``
        como primer paso, antes de cualquier operación.

        Args:
            storyboard: Storyboard estructurado (N3).
            narration: Audio de narración pre-generado.
            config: Parámetros de render agnósticos.
            context: Contexto de la corrida actual.

        Returns:
            RenderResult con paths, hashes y reporte de validación.

        Raises:
            AdapterValidationError: Si los inputs no pasan validación.
            AdapterRenderError: Si el render falla durante ejecución.
            AdapterTimeoutError: Si el render excede el tiempo máximo.
        """
        ...

    def validate_inputs(
        self,
        storyboard: Storyboard,
        narration: NarrationInput,
        config: RenderConfig,
    ) -> None:
        """Valida que los inputs sean suficientes para renderizar.

        Fallo rápido antes de empezar el render. Cubre:
        - Integridad del storyboard (escenas, version, metadata)
        - Continuidad de escenas (orden, sin gaps >1s, sin solapamientos)
        - Duración total vs metadata vs narración
        - Componentes visuales mínimos
        - Existencia del archivo de audio
        - Configuración soportada

        Raises:
            AdapterValidationError: descripción del primer problema.
        """
        self._validate_storyboard_structure(storyboard)
        self._validate_scene_continuity(storyboard)
        self._validate_narration(narration)
        self._validate_narration_duration_match(storyboard, narration)
        self._validate_config(config)

    # ── validaciones internas ──────────────────────────────────────────

    def _validate_storyboard_structure(
        self, storyboard: Storyboard
    ) -> None:
        if not storyboard.storyboard_version:
            raise AdapterValidationError("Storyboard version is required")

        if not storyboard.scenes:
            raise AdapterValidationError(
                "Storyboard must contain at least one scene"
            )

        expected_count = storyboard.metadata.get("scene_count")
        if expected_count is not None and expected_count != len(storyboard.scenes):
            raise AdapterValidationError(
                f"metadata.scene_count ({expected_count}) "
                f"does not match actual scenes ({len(storyboard.scenes)})"
            )

        total = storyboard.metadata.get("total_duration_seconds")
        last_end = storyboard.scenes[-1].end_seconds
        if total is not None and abs(total - last_end) > 2.0:
            raise AdapterValidationError(
                f"metadata.total_duration_seconds ({total}) "
                f"does not match last scene end ({last_end})"
            )

        for scene in storyboard.scenes:
            if not scene.id:
                raise AdapterValidationError("Scene with empty id")

            if scene.end_seconds <= scene.start_seconds:
                raise AdapterValidationError(
                    f"Scene {scene.id}: end_seconds ({scene.end_seconds}) "
                    f"must be > start_seconds ({scene.start_seconds})"
                )

            if not scene.narration.strip():
                raise AdapterValidationError(
                    f"Scene {scene.id}: narration is empty"
                )

            components = scene.visual.get("components", [])
            if not components:
                raise AdapterValidationError(
                    f"Scene {scene.id}: visual must have at least one component"
                )

            for comp in components:
                ctype = comp.get("type", "")
                if ctype in ("image", "video_clip") and not comp.get("source"):
                    raise AdapterValidationError(
                        f"Scene {scene.id}: component type '{ctype}' requires 'source'"
                    )
                if ctype == "solid_color" and not comp.get("color"):
                    raise AdapterValidationError(
                        f"Scene {scene.id}: component type 'solid_color' requires 'color'"
                    )
                if ctype in ("text", "code") and not comp.get("content"):
                    raise AdapterValidationError(
                        f"Scene {scene.id}: component type '{ctype}' requires 'content'"
                    )

    def _validate_scene_continuity(
        self, storyboard: Storyboard
    ) -> None:
        prev_end = 0.0
        for i, scene in enumerate(storyboard.scenes):
            gap = scene.start_seconds - prev_end
            if gap < 0:
                raise AdapterValidationError(
                    f"Scene {scene.id} overlaps previous scene by {-gap:.1f}s"
                )
            if gap > 1.0 and i > 0:
                raise AdapterValidationError(
                    f"Gap of {gap:.1f}s before scene {scene.id} "
                    f"(max allowed: 1s)"
                )
            prev_end = scene.end_seconds

    def _validate_narration(
        self, narration: NarrationInput
    ) -> None:
        if narration.duration_seconds <= 0:
            raise AdapterValidationError(
                f"Invalid narration duration: {narration.duration_seconds}"
            )
        if narration.source not in ("tts", "recorded"):
            raise AdapterValidationError(
                f"Invalid narration source: {narration.source}"
            )
        if not os.path.isfile(narration.audio_path):
            raise AdapterValidationError(
                f"Narration audio file not found: {narration.audio_path}"
            )

    def _validate_narration_duration_match(
        self,
        storyboard: Storyboard,
        narration: NarrationInput,
    ) -> None:
        last_end = storyboard.scenes[-1].end_seconds
        diff = abs(narration.duration_seconds - last_end)
        if diff > 2.0:
            raise AdapterValidationError(
                f"Narration duration ({narration.duration_seconds}s) "
                f"does not match storyboard end ({last_end}s); "
                f"difference: {diff:.1f}s (max: 2s)"
            )

    def _validate_config(self, config: RenderConfig) -> None:
        # Verticales para shorts/reels: el quality gate ya contempla el modo
        # vertical (`_dim_formato(vertical=True)`), pero el contrato solo
        # admitia horizontales y bloqueaba el formato en el que se publica.
        valid_resolutions = {
            "1920x1080", "1280x720", "3840x2160",
            "1080x1920", "720x1280",
        }
        if config.resolution not in valid_resolutions:
            raise AdapterValidationError(
                f"Unsupported resolution: {config.resolution}. "
                f"Supported: {', '.join(sorted(valid_resolutions))}"
            )

        if config.fps not in (24, 30, 60):
            raise AdapterValidationError(
                f"Unsupported fps: {config.fps}. Supported: 24, 30, 60"
            )

    def validate_result(self, result: RenderResult) -> None:
        """Valida invariantes del resultado post-render.

        Raises:
            AdapterValidationError: si el resultado viola los invariantes.
        """
        result.validate()
