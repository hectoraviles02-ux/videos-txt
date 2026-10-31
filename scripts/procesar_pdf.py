# -*- coding: utf-8 -*-
"""
Extractor de PDF (texto nativo, por pagina) — 22-oct-2026.

Antes el PDF pasaba por readability y el binario quedaba como texto ("%PDF-1.2") marcado EXTRAIDO.
Ahora:
  - se guarda el PDF crudo en raw/ (documento.pdf, read-only por regla 6);
  - cada pagina con texto es un segmento con su numero de pagina (`pagina`, 1 = primera);
  - una pagina sin texto extraible se avisa como [PENDIENTE] (posible escaneo). NO se hace OCR;
  - un PDF que no abre, o sin texto en ninguna pagina, queda sin segmentos -> BORRADOR, nunca EXTRAIDO.
metodo = extractor_articulo (mismo criterio que la extraccion manual del informe NASA de Mars, que ya
lleva `pagina` por segmento). El origen PDF queda en avisos.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comun import calcular_hash, escribir_salida

MIN_CARACTERES_PAGINA = 20   # menos que esto (numeros de pagina, sellos) no cuenta como texto


def es_pdf(contenido: bytes, content_type: str = "") -> bool:
    return contenido[:1024].lstrip().startswith(b"%PDF") or "application/pdf" in (content_type or "").lower()


def _detectar_idioma(texto: str) -> str:
    from procesar_texto import _detectar_idioma
    return _detectar_idioma(texto)


def extraer_paginas(contenido: bytes) -> tuple[list[str], str | None, str | None]:
    """-> (texto por pagina, titulo de metadatos o None, error o None). Nunca devuelve el binario."""
    import io
    from pypdf import PdfReader
    try:
        lector = PdfReader(io.BytesIO(contenido))
        paginas = []
        for p in lector.pages:
            try:
                paginas.append(p.extract_text() or "")
            except Exception as e:  # noqa: BLE001 - una pagina rota no tumba el resto
                paginas.append("")
                print(f"  aviso: pagina ilegible ({type(e).__name__})")
        titulo = None
        try:
            if lector.metadata and lector.metadata.title:
                titulo = lector.metadata.title.strip() or None
        except Exception:  # noqa: BLE001 - metadatos rotos: se usa el nombre del archivo
            titulo = None
        return paginas, titulo, None
    except Exception as e:  # noqa: BLE001
        return [], None, f"{type(e).__name__}: {e}"


def procesar_contenido(url: str, contenido: bytes, raw_dir: Path, proc_dir: Path) -> dict:
    (raw_dir / "documento.pdf").write_bytes(contenido)
    paginas, titulo_meta, error = extraer_paginas(contenido)
    avisos = ["origen PDF: texto nativo por pagina (pypdf); sin OCR"]
    segmentos = []
    sin_texto = []
    for n, texto in enumerate(paginas, start=1):
        texto = (texto or "").replace("\x00", "").strip()
        if len(texto) < MIN_CARACTERES_PAGINA:
            sin_texto.append(n)
            continue
        segmentos.append({"inicio_seg": None, "fin_seg": None, "texto": texto, "confianza": 1.0, "pagina": n})
    if error:
        avisos.append(f"[PENDIENTE] el PDF no se pudo leer ({error}); no se extrajo nada")
    elif not paginas:
        avisos.append("[PENDIENTE] el PDF no tiene paginas")
    elif not segmentos:
        avisos.append(f"[PENDIENTE] ninguna de las {len(paginas)} paginas tiene texto extraible "
                      "(posible escaneo); requiere OCR o revision humana")
    elif sin_texto:
        avisos.append(f"[PENDIENTE] {len(sin_texto)} de {len(paginas)} paginas sin texto extraible "
                      f"(posible escaneo): {sin_texto[:30]}")
    nombre = url.rstrip("/").rsplit("/", 1)[-1] or "documento"
    titulo = titulo_meta or nombre
    texto_total = " ".join(s["texto"] for s in segmentos)
    reg = {"source_url": url, "url_hash": calcular_hash(url), "tipo": "texto",
           "titulo": titulo, "idioma": _detectar_idioma(texto_total) if texto_total else "und",
           "duracion_seg": 0, "metodo": "extractor_articulo", "verificada": bool(segmentos),
           "segmentos": segmentos, "avisos": avisos, "paginas_total": len(paginas)}
    return escribir_salida(proc_dir, reg)
