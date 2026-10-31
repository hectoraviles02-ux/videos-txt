from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from adapters.base import AdapterValidationError, StoryboardScene, UnsupportedComponentError


_SUPPORTED_TYPES = frozenset(["solid_color", "text", "image"])


@dataclass
class SceneRenderer:
    resolution: tuple[int, int] = (1920, 1080)
    font_path: str | None = None
    font_size_title: int = 72
    font_size_body: int = 48
    font_size_small: int = 36
    bg_color: tuple[int, int, int] = (0, 0, 0)
    margin: int = 80

    def __post_init__(self):
        self._font_cache: dict[str, ImageFont.FreeTypeFont] = {}

    def _get_font(self, size: int) -> ImageFont.FreeTypeFont:
        key = str(size)
        if key not in self._font_cache:
            if self.font_path:
                if not os.path.isfile(self.font_path):
                    raise AdapterValidationError(
                        f"Font file not found: {self.font_path}"
                    )
                self._font_cache[key] = ImageFont.truetype(self.font_path, size)
            else:
                self._font_cache[key] = ImageFont.load_default()
        return self._font_cache[key]

    def render_scene(self, scene: StoryboardScene, output_path: str) -> None:
        components = scene.visual.get("components", [])
        for comp in components:
            ctype = comp.get("type", "")
            if ctype not in _SUPPORTED_TYPES:
                raise UnsupportedComponentError(
                    f"Unsupported component type: '{ctype}' in scene {scene.id}. "
                    f"Supported: solid_color, text, image"
                )

        canvas = Image.new("RGB", self.resolution, self.bg_color)
        draw = ImageDraw.Draw(canvas)

        for comp in components:
            ctype = comp["type"]
            if ctype == "solid_color":
                self._render_solid_color(comp, canvas, draw)
            elif ctype == "text":
                self._render_text(comp, canvas, draw)
            elif ctype == "image":
                self._render_image(comp, canvas, draw)

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        canvas.save(output_path, "PNG")

    def _render_solid_color(
        self,
        component: dict[str, Any],
        canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
    ) -> None:
        color_hex = component.get("color", "#000000")
        rgb = self._parse_color(color_hex)
        draw.rectangle([(0, 0), self.resolution], fill=rgb)

    def _render_text(
        self,
        component: dict[str, Any],
        canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
    ) -> None:
        text = component.get("content", "")
        if not text:
            return

        position = component.get("position", "center")
        color_hex = component.get("color", "#ffffff")
        fg = self._parse_color(color_hex)

        font_size = component.get("font_size", self.font_size_body)
        font = self._get_font(font_size)

        lines = self._wrap_text(text, font, self.resolution[0] - 2 * self.margin)
        line_height = self._line_height(font)
        total_h = len(lines) * line_height

        cx, cy = self._resolve_position(position, total_h)

        for i, line in enumerate(lines):
            bbox = draw.textbbox((0, 0), line, font=font)
            line_w = bbox[2] - bbox[0]
            x = cx - line_w // 2
            y = cy + i * line_height - total_h // 2
            draw.text((x, y), line, fill=fg, font=font)

    def _render_image(
        self,
        component: dict[str, Any],
        canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
    ) -> None:
        source = component.get("source", "")
        if not source:
            raise AdapterValidationError("Image component requires 'source'")
        if not os.path.isfile(source):
            raise AdapterValidationError(f"Image file not found: {source}")

        img = Image.open(source).convert("RGB")
        # cover, NO contain: la imagen debe LLENAR el cuadro. Con contain, una
        # foto de stock de 970x650 dejaba el 70% de la pantalla en negro.
        img = self._fit_cover(img, canvas.size)

        position = component.get("position", "center")
        cx, cy = self._resolve_position(position, img.height)

        x = cx - img.width // 2
        y = cy - img.height // 2

        canvas.paste(img, (x, y))

        overlay = component.get("overlay_text", "")
        if overlay:
            overlay_color = component.get("overlay_color", "#ffffff")
            fg = self._parse_color(overlay_color)
            font = self._get_font(self.font_size_small)
            bbox = draw.textbbox((0, 0), overlay, font=font)
            tw = bbox[2] - bbox[0]
            tx = cx - tw // 2
            ty = y + img.height + 10
            draw.text((tx, ty), overlay, fill=fg, font=font)

    @staticmethod
    def _parse_color(hex_color: str) -> tuple[int, int, int]:
        m = re.match(r"^#([0-9A-Fa-f]{6})$", hex_color)
        if not m:
            raise AdapterValidationError(f"Invalid color: {hex_color!r}")
        h = m.group(1)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    def _resolve_position(
        self, position: str, element_height: int
    ) -> tuple[int, int]:
        w, h = self.resolution
        positions = {
            "center": (w // 2, h // 2),
            "top": (w // 2, self.margin + element_height // 2),
            "bottom": (w // 2, h - self.margin - element_height // 2),
            "left": (self.margin + w // 6, h // 2),
            "right": (w - self.margin - w // 6, h // 2),
            "top-left": (self.margin + w // 6, self.margin + element_height // 2),
            "top-right": (w - self.margin - w // 6, self.margin + element_height // 2),
            "bottom-left": (self.margin + w // 6, h - self.margin - element_height // 2),
            "bottom-right": (
                w - self.margin - w // 6,
                h - self.margin - element_height // 2,
            ),
        }
        return positions.get(position, (w // 2, h // 2))

    def _wrap_text(
        self, text: str, font: ImageFont.FreeTypeFont, max_width: int
    ) -> list[str]:
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            test = f"{current} {word}".strip()
            bbox = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox(
                (0, 0), test, font=font
            )
            if (bbox[2] - bbox[0]) <= max_width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines or [text]

    @staticmethod
    def _line_height(font: ImageFont.FreeTypeFont) -> int:
        if hasattr(font, "getmetrics"):
            ascent, descent = font.getmetrics()
            return ascent + descent
        return font.size + 4

    @staticmethod
    def _fit_contain(
        img: Image.Image, canvas_size: tuple[int, int]
    ) -> Image.Image:
        """Escala para ENTRAR en el lienzo (deja bandas). Se conserva por si
        alguna escena la necesita, pero NO es el default: ver _fit_cover."""
        img.thumbnail(canvas_size, Image.LANCZOS)
        return img

    @staticmethod
    def _fit_cover(
        img: Image.Image, canvas_size: tuple[int, int]
    ) -> Image.Image:
        """Escala para LLENAR el lienzo y recorta el sobrante por el centro.

        `_fit_contain` usaba Image.thumbnail(), que SOLO ACHICA: una foto de
        stock de 970x650 se quedaba en 970x650 sobre un lienzo de 1920x1080,
        dejando el 70% del cuadro en negro. Medido sobre un video real:
        contenido util 969x647, es decir 30% del area.

        Aca se escala por el lado que falta (factor = max, no min) y se recorta
        centrado, de modo que la imagen SIEMPRE cubre el cuadro completo.
        """
        cw, ch = canvas_size
        if img.width <= 0 or img.height <= 0:
            return img

        factor = max(cw / img.width, ch / img.height)
        nw = max(cw, int(round(img.width * factor)))
        nh = max(ch, int(round(img.height * factor)))
        img = img.resize((nw, nh), Image.LANCZOS)

        left = (nw - cw) // 2
        top = (nh - ch) // 2
        return img.crop((left, top, left + cw, top + ch))
