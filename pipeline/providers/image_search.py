"""Busqueda de imagenes de stock (Pexels primario, Pixabay como fallback).
Ambas APIs son gratis, sin atribucion requerida, uso comercial permitido.
"""

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import requests

logger = logging.getLogger("image_search")

CACHE_DIR = Path("cache/images")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _api_keys() -> tuple[str | None, str | None]:
    """Read Pexels/Pixabay keys lazily (after ``.env`` is loaded at runtime).

    The keys are read on every call instead of at import time because the
    bot loads ``.env`` after the providers module is imported.
    """
    pexels = os.getenv("PEXELS_API_KEY")
    pixabay = os.getenv("PIXABAY_API_KEY")
    if not pexels or not pixabay:
        _load_env_if_present()
        pexels = os.getenv("PEXELS_API_KEY") or pexels
        pixabay = os.getenv("PIXABAY_API_KEY") or pixabay
    return pexels, pixabay


def _load_env_if_present() -> None:
    """Load ``.env`` from project root into ``os.environ`` (stdlib, no dotenv)."""
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if not env_path.is_file():
        return
    for ln in env_path.read_text(encoding="utf-8-sig").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, _, v = ln.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and not os.environ.get(k):
            os.environ.setdefault(k, v)


@dataclass
class ImageResult:
    local_path: str
    source: str  # "pexels" | "pixabay" | "none"
    query_usada: str


def _cache_path(query: str) -> Path:
    hash_query = hashlib.md5(query.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{hash_query}.jpg"


def _descargar(url: str, destino: Path) -> bool:
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        destino.write_bytes(resp.content)
        return True
    except Exception:
        logger.exception(f"Error descargando imagen de {url}")
        return False


def buscar_pexels(query: str) -> str | None:
    pexels_key, _ = _api_keys()
    if not pexels_key:
        return None
    try:
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": pexels_key},
            params={"query": query, "per_page": 1, "orientation": "landscape"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        photos = data.get("photos", [])
        if photos:
            return photos[0]["src"]["large"]
    except Exception:
        logger.exception(f"Error consultando Pexels para '{query}'")
    return None


def buscar_pixabay(query: str) -> str | None:
    _, pixabay_key = _api_keys()
    if not pixabay_key:
        return None
    try:
        resp = requests.get(
            "https://pixabay.com/api/",
            params={
                "key": pixabay_key,
                "q": query,
                "image_type": "photo",
                "orientation": "horizontal",
                "per_page": 3,
                "safesearch": "true",
            },
            timeout=10,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
        if hits:
            return hits[0]["largeImageURL"]
    except Exception:
        logger.exception(f"Error consultando Pixabay para '{query}'")
    return None


def obtener_imagen(query: str) -> ImageResult:
    """
    Busca una imagen para el query dado. Orden: cache > Pexels > Pixabay > None.
    Si no hay resultado en ninguna fuente, el caller debe hacer fallback a
    solid_color (NO debe romper el pipeline).
    """
    cache_file = _cache_path(query)
    if cache_file.exists():
        return ImageResult(str(cache_file), source="cache", query_usada=query)

    url = buscar_pexels(query)
    fuente = "pexels"
    if not url:
        url = buscar_pixabay(query)
        fuente = "pixabay"

    if not url:
        logger.warning(f"Sin resultados de imagen para: '{query}'")
        return ImageResult(local_path="", source="none", query_usada=query)

    if _descargar(url, cache_file):
        return ImageResult(str(cache_file), source=fuente, query_usada=query)

    return ImageResult(local_path="", source="none", query_usada=query)
