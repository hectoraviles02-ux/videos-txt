"""StockImageGenerator — implementa ImageGenerator usando Pexels/Pixabay.

Sigue el mismo protocolo que SDImageGenerator en adapters/image_generator.py.
Usa image_search.py para buscar y descargar imagenes de stock.
Si no hay resultado, genera un PNG de color solido (nunca rompe el pipeline).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from pipeline.providers.image_search import obtener_imagen

logger = logging.getLogger(__name__)


class StockImageGenerator:
    """Genera imagenes por escena desde APIs de stock (Pexels > Pixabay).

    Si no hay API keys configuradas o no hay resultados para el query,
    fallback a color solido (como SDImageGenerator).
    """

    def __init__(
        self,
        fallback_to_solid: bool = True,
    ):
        self._prompt_cache: dict[str, str] = {}
        self.fallback_to_solid = fallback_to_solid

    @property
    def name(self) -> str:
        return "stock-pexels-pixabay"

    def generate(
        self,
        storyboard: Any,
        output_dir: str,
    ) -> dict[str, str]:
        os.makedirs(output_dir, exist_ok=True)
        results: dict[str, str] = {}

        for scene in storyboard.scenes:
            output_path = os.path.join(output_dir, f"{scene.id}.png")

            raw = (
                scene.visual.get("search_keywords")
                or scene.visual.get("visual_description")
                or scene.visual.get("description")
                or scene.title
            )
            query = ", ".join(raw) if isinstance(raw, list) else str(raw)

            if query in self._prompt_cache:
                cached = self._prompt_cache[query]
                if os.path.isfile(cached):
                    results[scene.id] = cached
                    continue

            resultado = obtener_imagen(query)
            if resultado.local_path:
                results[scene.id] = resultado.local_path
                self._prompt_cache[query] = resultado.local_path
                logger.info(
                    "Escena %s: imagen desde %s para '%s'",
                    scene.id, resultado.source, resultado.query_usada[:60],
                )
            else:
                if self.fallback_to_solid:
                    output_path = self._solid_fallback(scene, output_path)
                else:
                    output_path = self._solid_fallback(scene, output_path)
                self._prompt_cache[query] = output_path
                results[scene.id] = output_path

        return results

    def cleanup(self) -> None:
        self._prompt_cache.clear()
        logger.info("StockImageGenerator cleanup done")

    def _solid_fallback(self, scene: Any, output_path: str) -> str:
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

            img = Image.new("RGB", (1024, 576), rgb)
            img.save(output_path, format="PNG")
            logger.debug("Solid fallback for %s -> %s", scene.id, output_path)
        except ImportError:
            logger.warning(
                "Pillow not available — cannot generate solid fallback for %s", scene.id,
            )
            open(output_path, "wb").close()

        return output_path
