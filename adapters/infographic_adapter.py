"""Infographic image generator adapter.

Uses the AntV ``@antv/infographic`` CLI (``ifgc``) to render declarative
infographic DSLs to SVG, then converts to PNG via ImageMagick ``magick``.

Implements the ``ImageGenerator`` protocol defined in ``image_generator.py``.
Graceful fallback to solid-colour placeholders when ``ifgc`` or ``magick``
are not available (mirrors the pattern in ``SDImageGenerator``).

Configuration (via RenderConfig.engine_specific or YAML):

    render:
      image_provider: "infographic"
      image_providers:
        infographic:
          ifgc_path: auto-detect  # or /custom/path/to/ifgc
          magick_path: auto-detect  # or /custom/path/to/magick.exe
          default_template: "list-row-simple-horizontal-arrow"
          density: 150  # SVG render DPI
          fallback_to_solid: true
          timeout_seconds: 30
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Any

from adapters.base import Storyboard

logger = logging.getLogger(__name__)


_TEMPLATE_BY_SCENE_TYPE: dict[str, str] = {
    "hook": "list-row-simple-horizontal-arrow",
    "narrative": "list-row-simple-horizontal-arrow",
    "explanatory": "compare-quadrant-four-areas-card",
    "transitional": "list-row-simple-horizontal-arrow",
    "tension": "compare-quadrant-four-areas-card",
    "summary": "list-row-simple-horizontal-arrow",
    "closing": "list-row-simple-horizontal-arrow",
}

_DEFAULT_TEMPLATE = "list-row-simple-horizontal-arrow"


def _rasterizar_con_navegador(svg_path: str, output_path: str, width: int,
                              height: int, background: str) -> bool:
    """Convierte el SVG a PNG con chromium. Devuelve False si no se pudo.

    ImageMagick no sirve para estos SVG. MEDIDO el 2026-08-21: ifgc 0.5.1
    maqueta el texto con <foreignObject> y XHTML dentro, y la libreria que
    usa ImageMagick para SVG -RSVG 2.40.20- no soporta foreignObject. El PNG
    resultante sale de UN SOLO COLOR: en blanco, sin avisar. Con chromium el
    mismo archivo da 630 colores distintos.

    El fallo es silencioso a proposito: si no hay navegador se devuelve False
    y el llamador usa el camino viejo, que al menos no revienta.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("InfographicAdapter: sin playwright; se rasteriza con "
                       "magick, que no soporta foreignObject")
        return False

    with open(svg_path, encoding="utf-8") as f:
        svg = f.read()
    if "<svg" not in svg:
        return False
    svg = svg[svg.index("<svg"):]  # fuera el prologo xml para incrustarlo

    html = (
        "<!doctype html><meta charset='utf-8'><style>"
        "html,body{margin:0;height:100%%;background:%s;overflow:hidden}"
        ".w{height:100%%;display:flex;align-items:center;justify-content:center}"
        "svg{width:78vw;height:auto}"
        # La hoja de estilo de la fuente es remota; si no resuelve, el texto
        # queda en una serifa por defecto. Se fija una local y no se espera.
        "svg span{font-family:'Segoe UI',Arial,sans-serif !important}"
        "</style><div class='w'>%s</div>" % (background, svg)
    )
    html_path = svg_path.replace(".svg", ".html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    try:
        with sync_playwright() as p:
            navegador = p.chromium.launch()
            pagina = navegador.new_page(
                viewport={"width": width, "height": height})
            pagina.goto("file:///" + os.path.abspath(html_path).replace("\\", "/"))
            pagina.wait_for_timeout(600)
            pagina.screenshot(path=output_path)
            navegador.close()
        return True
    except Exception as e:
        logger.warning("InfographicAdapter: chromium fallo (%s); se usa magick", e)
        return False
    finally:
        if os.path.exists(html_path):
            os.remove(html_path)


class InfographicAdapter:
    """Render infographics from storyboard scene descriptions.

    For each scene, the adapter:
      1. Builds an infographic DSL (``infographic <template>\\ndata\\n  title ...``)
      2. Calls ``ifgc`` to produce SVG
      3. Calls ImageMagick ``magick`` to convert SVG → PNG at 1920×1080
      4. Returns the PNG path

    If either tool is missing or fails, falls back to a solid-colour PNG
    (same behaviour as ``SDImageGenerator``).
    """

    def __init__(
        self,
        ifgc_path: str | None = None,
        magick_path: str | None = None,
        default_template: str = _DEFAULT_TEMPLATE,
        density: int = 150,
        width: int = 1920,
        height: int = 1080,
        fallback_to_solid: bool = True,
        timeout_seconds: int = 30,
    ) -> None:
        self._ifgc_path = ifgc_path
        self._magick_path = magick_path
        self._default_template = default_template
        self._density = density
        self._width = width
        self._height = height
        self._fallback_to_solid = fallback_to_solid
        self._timeout_seconds = timeout_seconds

    @property
    def name(self) -> str:
        return "antv-infographic"

    def generate(
        self,
        storyboard: Storyboard,
        output_dir: str,
    ) -> dict[str, str]:
        """Generate one PNG per scene.

        Returns {scene_id: png_path} for every scene.
        """
        os.makedirs(output_dir, exist_ok=True)
        results: dict[str, str] = {}

        if not self._tools_available():
            logger.warning(
                "InfographicAdapter: ifgc/magick not found. "
                "Falling back to solid-colour placeholders for all scenes."
            )
            for scene in storyboard.scenes:
                png_path = self._solid_fallback(scene, output_dir)
                results[scene.id] = png_path
            return results

        for scene in storyboard.scenes:
            png_path = os.path.join(output_dir, f"{scene.id}.png")

            try:
                png_path = self._render_scene(scene, png_path)
            except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.CalledProcessError) as exc:
                logger.warning(
                    "InfographicAdapter: render failed for scene %s (%s). "
                    "Falling back to solid colour.",
                    scene.id,
                    exc,
                )
                if self._fallback_to_solid:
                    png_path = self._solid_fallback(scene, output_dir)
                else:
                    results[scene.id] = png_path
                    continue

            results[scene.id] = png_path

        return results

    def cleanup(self) -> None:
        """No persistent state to clean up."""

    # ── internals ─────────────────────────────────────────────────────

    def _tools_available(self) -> bool:
        if not self._ifgc_path:
            self._ifgc_path = shutil.which("ifgc")
        if not self._ifgc_path:
            return False
        if not self._magick_path:
            self._magick_path = self._find_magick()
        return self._magick_path is not None

    @staticmethod
    def _find_magick() -> str | None:
        for candidate in [
            "magick",
            "C:/Program Files/ImageMagick-7.1.1-Q16/magick.exe",
            "C:/Program Files/ImageMagick-7.1.1-47.x86_64/magick.exe",
        ]:
            path = shutil.which(candidate)
            if path:
                return path
        for known in [
            "C:/Program Files/ImageMagick-7.1.1-Q16/magick.exe",
            "C:/Program Files/ImageMagick-7.1.1-47.x86_64/magick.exe",
        ]:
            if os.path.isfile(known):
                return known
        return None

    def _render_scene(self, scene: Any, output_path: str) -> str:
        """Generate SVG via ifgc, convert to PNG via magick. Returns PNG path."""
        svg_path = output_path.replace(".png", ".svg")

        dsl = self._build_dsl(scene)
        # El DSL entra por stdin, no por -s. MEDIDO el 2026-08-21 con ifgc
        # 0.5.1: la opcion -s falla con "SSR render timeout" a los ~12s, tanto
        # con saltos de linea reales como con \n escapado; stdin resuelve en
        # 2.1s. Mientras se uso -s, este adaptador nunca produjo una
        # infografia: caia siempre al placeholder de color solido, en silencio.
        subprocess.run(
            [self._ifgc_path, "-o", svg_path],
            input=dsl,
            capture_output=True,
            text=True,
            timeout=self._timeout_seconds,
            check=True,
        )

        if not _rasterizar_con_navegador(
            svg_path, output_path, self._width, self._height,
            scene.visual.get("background_color", "#f6f8fa"),
        ):
            # Camino viejo. Se conserva porque no siempre hay navegador, pero
            # ya no es el preferido: ver _rasterizar_con_navegador.
            subprocess.run(
                [
                    self._magick_path,
                    "-density", str(self._density),
                    svg_path,
                    "-background", "none",
                    "-flatten",
                    "-gravity", "center",
                    "-extent", f"{self._width}x{self._height}",
                    "-background",
                    scene.visual.get("background_color", "#16213e"),
                    output_path,
                ],
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                check=True,
            )

        os.remove(svg_path)
        logger.debug("InfographicAdapter: scene %s → %s", scene.id, output_path)
        return output_path

    def _build_dsl(self, scene: Any) -> str:
        """Construct infographic DSL from storyboard scene.

        Maps scene visual_description to a template + data block.
        """
        scene_type = scene.type or "narrative"
        template = _TEMPLATE_BY_SCENE_TYPE.get(scene_type, self._default_template)

        title = scene.title or scene.visual.get("title", "")[:50]
        desc = scene.visual.get("description", "")[:100]

        items = self._extract_items(scene)

        lines = [
            f"infographic {template}",
            "data",
        ]
        if title:
            lines.append(f"  title {title}")
        if desc:
            lines.append(f"  desc {desc}")
        if items:
            lines.append("  items")
            for label, item_desc in items:
                lines.append(f"    - label {label}")
                if item_desc:
                    lines.append(f"      desc {item_desc}")

        return "\n".join(lines)

    @staticmethod
    def _extract_items(scene: Any) -> list[tuple[str, str]]:
        """Extract list items from scene components or narration.

        Looks for ``visual.components`` entries of type ``infographic_item``
        or splits narration into pseudo-sentences.
        """
        items: list[tuple[str, str]] = []
        components = scene.visual.get("components", [])

        for comp in components:
            if comp.get("type") == "infographic_item":
                items.append((
                    comp.get("label", ""),
                    comp.get("desc", ""),
                ))

        if not items and scene.narration:
            sentences = [s.strip() for s in scene.narration.split(".") if s.strip()]
            if sentences:
                items = [(f"Paso {i + 1}", s[:80]) for i, s in enumerate(sentences[:4])]

        if not items:
            items = [(scene.title or "Escena", desc)]

        return items

    def _solid_fallback(self, scene: Any, output_dir: str) -> str:
        """Generate a solid-colour placeholder PNG using Pillow."""
        try:
            from PIL import Image
        except ImportError:
            logger.warning("Pillow not available for solid fallback.")
            open(os.path.join(output_dir, f"{scene.id}.png"), "wb").close()
            return os.path.join(output_dir, f"{scene.id}.png")

        color_hex = scene.visual.get("background_color", "#16213e")
        if color_hex.startswith("#"):
            try:
                rgb = tuple(int(color_hex[1:][i:i + 2], 16) for i in (0, 2, 4))
            except (ValueError, IndexError):
                rgb = (22, 33, 62)
        else:
            rgb = (22, 33, 62)

        output_path = os.path.join(output_dir, f"{scene.id}.png")
        img = Image.new("RGB", (self._width, self._height), rgb)
        img.save(output_path, format="PNG")
        return output_path
