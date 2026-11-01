# -*- coding: utf-8 -*-
"""
Extractor de Contenido — ROUTER principal.
Uso: python scripts/procesar_url.py <URL>

Flujo (todo determinista, el modelo solo aprieta este boton):
  1. hash de la URL normalizada  -> ¿ya esta en INDICE.md? -> DUPLICADO, salir.
  2. compuerta: ¿la URL resuelve? -> si no, PENDIENTE, salir.
  3. clasificar video/texto por dominio.
  4. despachar al extractor correspondiente (mismo proceso, import directo).
Nada se inventa aqui: la extraccion la hacen procesar_video / procesar_texto.
"""
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comun import (utf8_stdout, calcular_hash, normalizar_url, url_ya_procesada,
                   verificar_url, append_memoria, slugify)
import procesar_video
import procesar_texto
from comun import crear_directorios

VIDEO_DOMINIOS = ("youtube.com", "youtu.be", "vimeo.com", "dailymotion.com",
                  "twitch.tv", "facebook.com", "fb.watch", "instagram.com",
                  "tiktok.com", "twitter.com", "x.com")


def clasificar(url: str) -> str:
    dom = urlparse(url).netloc.lower().replace("www.", "")
    return "video" if any(d in dom for d in VIDEO_DOMINIOS) else "texto"


def main():
    utf8_stdout()
    if len(sys.argv) < 2:
        print("Uso: python procesar_url.py <URL>")
        sys.exit(1)
    url = sys.argv[1].strip()
    h = calcular_hash(url)

    # 1. duplicado (idempotencia)
    if url_ya_procesada(h):
        print(f"DUPLICADO: ya procesada (hash {h}). NO reprocesar.")
        sys.exit(0)

    # 2. compuerta: ¿resuelve?
    v = verificar_url(url)
    if not v["resuelve"]:
        print(f"PENDIENTE: no resuelve — {v['razon']}")
        append_memoria("error", f"URL no resuelve: {normalizar_url(url)}", v["razon"])
        sys.exit(1)

    # 3. clasificar
    tipo = clasificar(url)
    print(f"URL: {url}")
    print(f"hash={h}  status={v['status']}  tipo={tipo}")

    # 4. despachar
    # Obtener titulo para slug
    titulo = "sin-titulo"
    if tipo == "video":   # 31-oct-2026 (SSRF): yt-dlp solo para dominios de video; antes leía CUALQUIER URL
        try:
            import yt_dlp
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
                info = ydl.extract_info(url, download=False)
            titulo = info.get("title") or "sin-titulo"
        except Exception:
            titulo = "sin-titulo"
    else:   # artículo: el título sale de la página, pedida por el descargador seguro
        try:
            import re as _re
            from comun import obtener_seguro
            m = _re.search(r"<title[^>]*>(.*?)</title>", obtener_seguro(url, headers={"User-Agent": "Mozilla/5.0"},
                                                                       timeout=20).text, _re.I | _re.S)
            titulo = (m.group(1).strip() if m else "") or "sin-titulo"
        except Exception:
            titulo = "sin-titulo"
    if titulo == "sin-titulo" and tipo == "texto":   # p. ej. un PDF: se nombra por su archivo
        titulo = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1] or "sin-titulo"
    
    slug = slugify(titulo)
    print(f"titulo={titulo}  slug={slug}")
    
    raw_dir, proc_dir = crear_directorios(slug, h)
    try:
        if tipo == "video":
            res = procesar_video.procesar(url, raw_dir, proc_dir)
        else:
            res = procesar_texto.procesar(url, raw_dir, proc_dir)
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        append_memoria("error", f"fallo extraccion {tipo}: {url}", f"{type(e).__name__}: {e}")
        sys.exit(2)

    import json
    from comun import append_indice
    reg = json.loads(res["json"].read_text(encoding="utf-8"))
    append_indice(reg["titulo"], tipo, reg["metodo"], res["estado"], reg["url_hash"])
    print(f"OK [{res['estado']}] metodo={reg['metodo']} segmentos={len(reg['segmentos'])}")
    print(f"  MD:   {res['md']}")
    print(f"  JSON: {res['json']}")


if __name__ == "__main__":
    main()
