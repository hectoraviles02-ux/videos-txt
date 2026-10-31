"""Image generation adapters for pipeline storyboard scenes.

Defines a ``Protocol`` for image providers and a reference implementation
using Stable Diffusion (diffusers).  When the SD model is not yet cached
locally or the hardware is unavailable the generator falls back to
solid-colour placeholders.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

try:
    import torch as _torch
except ImportError:
    _torch = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from adapters.base import Storyboard

logger = logging.getLogger(__name__)


# ── Protocol ──────────────────────────────────────────────────────────────


@runtime_checkable
class ImageGenerator(Protocol):
    """Contract any image provider must fulfil."""

    @property
    def name(self) -> str:
        ...

    def generate(
        self,
        storyboard: Storyboard,
        output_dir: str,
    ) -> dict[str, str]:
        ...

    def cleanup(self) -> None:
        ...


# ── SD Implementation ─────────────────────────────────────────────────────


class SDImageGenerator:
    """Stable Diffusion image generator with graceful fallback.

    Uses ``diffusers.StableDiffusionPipeline`` when available.
    If the model is not cached locally, or if ``torch``/``diffusers`` are
    not installed, every scene silently falls back to a solid-colour PNG.
    """

    def __init__(
        self,
        model_id: str = "SG161222/Realistic_Vision_V5.1_noVAE",
        device: str = "cuda",
        cache_dir: str | None = None,
        fallback_to_solid: bool = True,
        sd_params: dict[str, Any] | None = None,
    ) -> None:
        self.model_id = model_id
        self.device = device
        self.cache_dir = cache_dir
        self.fallback_to_solid = fallback_to_solid

        params = sd_params or {}
        self._width: int = params.get("width", 1024)
        self._height: int = params.get("height", 576)
        self._num_inference_steps: int = params.get("num_inference_steps", 25)
        self._guidance_scale: float = params.get("guidance_scale", 7.5)
        self._negative_prompt: str = params.get(
            "negative_prompt", "blurry, low quality, distorted, ugly, deformed"
        )
        self._quality_prefix: str = params.get(
            "quality_prefix", "masterpiece, best quality, highly detailed, "
        )
        self._low_vram: bool = params.get("low_vram", True)

        self._prompt_cache: dict[str, str] = {}
        self._pipeline: Any = None

    # ── public API ────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "sd-realistic-vision"

    def generate(
        self,
        storyboard: Storyboard,
        output_dir: str,
    ) -> dict[str, str]:
        os.makedirs(output_dir, exist_ok=True)
        pipeline = self._load_pipeline()
        results: dict[str, str] = {}

        for scene in storyboard.scenes:
            output_path = os.path.join(output_dir, f"{scene.id}.png")

            visual_desc = (
                scene.visual.get("visual_description")
                or scene.visual.get("description")
                or scene.title
            )

            prompt = self._quality_prefix + visual_desc

            if prompt in self._prompt_cache:
                cached = self._prompt_cache[prompt]
                if os.path.isfile(cached):
                    results[scene.id] = cached
                    continue

            try:
                if pipeline is not None:
                    image = pipeline(
                        prompt=prompt,
                        width=self._width,
                        height=self._height,
                        num_inference_steps=self._num_inference_steps,
                        guidance_scale=self._guidance_scale,
                        negative_prompt=self._negative_prompt,
                    ).images[0]
                    image.save(output_path, format="PNG")
                else:
                    output_path = self._solid_fallback(scene, output_path)

            except (RuntimeError, MemoryError) as exc:
                logger.warning(
                    "SD inference failed for scene %s: %s. Falling back to solid colour.",
                    scene.id,
                    exc,
                )
                if _torch is not None:
                    _torch.cuda.empty_cache()
                output_path = self._solid_fallback(scene, output_path)

            self._prompt_cache[prompt] = output_path
            results[scene.id] = output_path

        return results

    def cleanup(self) -> None:
        """Release pipeline and clear GPU memory."""
        if self._pipeline is not None:
            del self._pipeline
            self._pipeline = None
        if _torch is not None:
            _torch.cuda.empty_cache()
        self._prompt_cache.clear()
        logger.info("SDImageGenerator cleanup done")

    # ── internals ─────────────────────────────────────────────────────

    def _load_pipeline(self) -> Any:
        """Lazy-load the diffusers pipeline.

        Returns ``None`` if the model cannot be loaded (not cached, missing
        deps, hardware issue) — callers must handle ``None`` gracefully.
        """
        if self._pipeline is not None:
            return self._pipeline

        if self._low_vram:
            logger.info("low_vram mode active — will enable attention slicing")

        try:
            import torch
            from diffusers import StableDiffusionPipeline
        except ImportError:
            logger.warning(
                "torch/diffusers not installed. SD disabled, falling back to solid colour."
            )
            return None

        if self.fallback_to_solid and not self._model_is_cached():
            logger.warning(
                "Model '%s' not found in cache. SD disabled, falling back to solid colour. "
                "Run 'descargar_modelo.ps1' or download manually.",
                self.model_id,
            )
            return None

        try:
            pipe = StableDiffusionPipeline.from_pretrained(
                self.model_id,
                torch_dtype=torch.float16,
                cache_dir=self.cache_dir,
            )
            pipe = pipe.to(self.device)

            if self._low_vram:
                pipe.enable_attention_slicing()

            self._pipeline = pipe
            return pipe

        except Exception as exc:
            logger.warning(
                "Failed to load SD pipeline '%s': %s. Falling back to solid colour.",
                self.model_id,
                exc,
            )
            return None

    def _solid_fallback(self, scene: Any, output_path: str) -> str:
        """Generate a solid-colour placeholder PNG."""
        try:
            from PIL import Image

            color_str = scene.visual.get("background_color", "#333333")
            if color_str.startswith("#"):
                hex_color = color_str[1:]
                try:
                    rgb = tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
                except ValueError:
                    rgb = (51, 51, 51)
            else:
                rgb = (51, 51, 51)

            img = Image.new("RGB", (self._width, self._height), rgb)
            img.save(output_path, format="PNG")
            logger.debug("Solid fallback for %s -> %s", scene.id, output_path)
        except ImportError:
            logger.warning(
                "Pillow not available — cannot generate solid fallback for %s",
                scene.id,
            )
            open(output_path, "wb").close()

        return output_path

    def _model_is_cached(self) -> bool:
        safe = self.model_id.replace("/", "--")
        candidates = [
            os.path.join(self._hf_home(), "models--" + safe),
            os.path.join(self._hf_home(), "hub", "models--" + safe),
        ]
        return any(os.path.isdir(c) for c in candidates)

    @staticmethod
    def _hf_home() -> str:
        return os.environ.get(
            "HF_HOME",
            os.path.join(
                os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
                "huggingface",
            ),
        )
