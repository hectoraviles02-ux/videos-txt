"""WebImageGenerator — Busca imágenes reales en Google Images vía Playwright.

A diferencia de Pexels/Pixabay (fotos genéricas de stock), esto busca en
Google Images lo que realmente existe en la web sobre el tema específico.
Mucho más relevante para noticias, personas, lugares y eventos reales.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

CACHE_DIR = Path("cache/web_images")


class WebImageGenerator:
    """Genera imágenes buscando en Google Images con Playwright.

    Cachea resultados por hash del query para no repetir búsquedas.
    """

    def __init__(
        self,
        max_results: int = 5,
        headless: bool = True,
        timeout_ms: int = 30000,
    ):
        self._max_results = max_results
        self._headless = headless
        self._timeout_ms = timeout_ms
        self._cache: dict[str, str] = {}
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return "web-google-images"

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

            cached = self._cache.get(query)
            if cached and os.path.isfile(cached):
                results[scene.id] = cached
                logger.info("Escena %s: imagen desde cache", scene.id)
                continue

            img_path = self._search_and_download(query, scene.id, output_dir)
            if img_path:
                results[scene.id] = img_path
                self._cache[query] = img_path
            else:
                fallback = self._solid_fallback(scene, output_path)
                results[scene.id] = fallback
                self._cache[query] = fallback

        return results

    def _search_and_download(
        self, query: str, scene_id: str, output_dir: str
    ) -> str | None:
        from playwright.sync_api import sync_playwright

        query_hash = hashlib.md5(query.encode()).hexdigest()[:12]
        cached = CACHE_DIR / f"{query_hash}.jpg"
        if cached.is_file():
            dest = os.path.join(output_dir, f"{scene_id}.png")
            import shutil
            shutil.copy2(str(cached), dest)
            logger.info("Escena %s: cache hit para '%s'", scene_id, query[:60])
            return dest

        logger.info(
            "Escena %s: buscando en Google Images '%s'", scene_id, query[:80]
        )

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self._headless)
                page = browser.new_page()
                page.set_viewport_size({"width": 1280, "height": 800})

                search_url = (
                    "https://www.google.com/search?"
                    f"tbm=isch&q={requests.utils.quote(query)}"
                )

                page.goto(search_url, timeout=self._timeout_ms)

                # Esperar a que carguen resultados (Google ya no usa src directo)
                page.wait_for_selector("img", timeout=10000)

                # Scroll para cargar más imágenes
                for _ in range(3):
                    page.evaluate(
                        "window.scrollTo(0, document.body.scrollHeight)"
                    )
                    time.sleep(1.5)

                # Extraer URLs reales de imágenes vía JS (data-src / data-iurl / src)
                urls = page.evaluate(
                    """() => {
                        const seen = new Set();
                        const out = [];
                        document.querySelectorAll('img').forEach((img) => {
                            const candidates = [
                                img.getAttribute('data-src'),
                                img.getAttribute('data-iurl'),
                                img.getAttribute('data-lt'),
                                img.getAttribute('src'),
                            ];
                            for (const c of candidates) {
                                if (!c) continue;
                                if (c.startsWith('data:')) continue;
                                if (c.startsWith('http') && !seen.has(c)) {
                                    seen.add(c);
                                    out.push(c);
                                }
                            }
                        });
                        return out;
                    }"""
                )

                downloaded = None
                for src in urls[: self._max_results + 10]:
                    try:
                        if not src or not src.startswith("http"):
                            continue
                        if any(
                            ext in src
                            for ext in [
                                "google",
                                "gstatic",
                                "favicon",
                                "logo",
                            ]
                        ):
                            continue

                        ext = os.path.splitext(src.split("?")[0])[1] or ".jpg"
                        if ext.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                            continue

                        temp_path = CACHE_DIR / f"{query_hash}{ext}"
                        self._download_image(src, str(temp_path))
                        if temp_path.is_file() and temp_path.stat().st_size > 1024:
                            dest = os.path.join(output_dir, f"{scene_id}.png")
                            self._convert_to_png(str(temp_path), dest)
                            downloaded = dest
                            # Guardar también en cache
                            shutil.copy2(dest, str(cached))
                            logger.info(
                                "Escena %s: imagen descargada desde Google Images",
                                scene_id,
                            )
                            break
                    except Exception:
                        continue

                browser.close()

                if downloaded:
                    return downloaded

                logger.warning(
                    "Escena %s: no se encontraron imágenes para '%s'",
                    scene_id,
                    query[:60],
                )
                return None

        except ImportError:
            logger.error("playwright no instalado. pip install playwright")
            return None
        except Exception as e:
            logger.error(
                "Escena %s: error en Playwright: %s", scene_id, e
            )
            return None

    def _download_image(self, url: str, path: str) -> None:
        import requests as req
        try:
            resp = req.get(url, timeout=15, stream=True)
            resp.raise_for_status()
            with open(path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
        except Exception as e:
            logger.debug("Error descargando imagen %s: %s", url[:50], e)
            if os.path.isfile(path):
                os.remove(path)

    def _convert_to_png(self, src: str, dst: str) -> None:
        try:
            from PIL import Image
            img = Image.open(src)
            img.save(dst, format="PNG")
            if src != dst and os.path.isfile(src):
                os.remove(src)
        except Exception as e:
            logger.debug("Error convirtiendo a PNG: %s", e)
            if os.path.isfile(src):
                import shutil
                shutil.copy2(src, dst)

    def _solid_fallback(self, scene: Any, output_path: str) -> str:
        try:
            from PIL import Image
            color_str = scene.visual.get("background_color", "#333333")
            if color_str.startswith("#"):
                hex_color = color_str[1:]
                try:
                    rgb = tuple(
                        int(hex_color[i : i + 2], 16) for i in (0, 2, 4)
                    )
                except ValueError:
                    rgb = (51, 51, 51)
            else:
                rgb = (51, 51, 51)
            img = Image.new("RGB", (1024, 576), rgb)
            img.save(output_path, format="PNG")
        except ImportError:
            open(output_path, "wb").close()
        return output_path

    def cleanup(self) -> None:
        self._cache.clear()
        logger.info("WebImageGenerator cleanup done")