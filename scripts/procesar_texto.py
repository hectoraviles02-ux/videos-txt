# -*- coding: utf-8 -*-
"""
Extractor de TEXTO / articulo (readability-lxml).
Baja el HTML, guarda el crudo en raw/, extrae el contenido principal
(descarta menu/publicidad/boilerplate) y lo parte en parrafos.
El texto NO tiene tiempos: inicio_seg/fin_seg = null (no se inventan).
metodo = extractor_articulo.
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comun import (utf8_stdout, crear_directorios, escribir_salida,
                   append_indice, append_memoria, calcular_hash, slugify)

UA = {"User-Agent": "Mozilla/5.0"}


def _detectar_idioma(texto: str) -> str:
    """Heuristica minima ES/EN por stopwords (sin dependencias)."""
    t = f" {texto.lower()} "
    es = sum(t.count(f" {w} ") for w in ("que", "de", "la", "el", "los", "para", "con", "una"))
    en = sum(t.count(f" {w} ") for w in ("the", "of", "and", "to", "for", "with", "that", "is"))
    if es == en == 0:
        return "und"
    return "es" if es >= en else "en"


def _codificacion(resp) -> str:
    """UTF-8 si el contenido es UTF-8 válido (casi todas las páginas); si no, la que adivina requests.
    23-oct-2026: apparent_encoding adivinó MacRoman en notas de Expansión/La Razón («Tom√°s P√©rez») y ninguna cita
    con acentos coincidía con la página: la investigación descartó 24 de 28 afirmaciones."""
    try:
        resp.content.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return resp.apparent_encoding or "utf-8"


def procesar(url: str, raw_dir: Path, proc_dir: Path) -> dict:
    from readability import Document
    from lxml import html as lxml_html

    resp = requests.get(url, headers=UA, timeout=30)
    import procesar_pdf
    if procesar_pdf.es_pdf(resp.content, resp.headers.get("Content-Type", "")):
        return procesar_pdf.procesar_contenido(url, resp.content, raw_dir, proc_dir)   # nunca el binario como texto
    resp.encoding = _codificacion(resp)
    (raw_dir / "pagina.html").write_text(resp.text, encoding="utf-8")

    doc = Document(resp.text)
    titulo = (doc.short_title() or "(sin titulo)").strip()
    tree = lxml_html.fromstring(doc.summary())
    texto = tree.text_content()

    parrafos = [p.strip() for p in texto.split("\n") if p.strip()]
    segmentos = [{"inicio_seg": None, "fin_seg": None, "texto": p, "confianza": 1.0}
                 for p in parrafos]

    reg = {"source_url": url, "url_hash": calcular_hash(url), "tipo": "texto",
           "titulo": titulo, "idioma": _detectar_idioma(texto), "duracion_seg": 0,
           "metodo": "extractor_articulo", "verificada": True,
           "segmentos": segmentos, "avisos": []}
    if not segmentos:
        reg["avisos"].append("[PENDIENTE] readability no extrajo contenido principal")
    return escribir_salida(proc_dir, reg)


def main():
    utf8_stdout()
    if len(sys.argv) < 2:
        print("Uso: python procesar_texto.py <URL>")
        sys.exit(1)
    url = sys.argv[1]
    url_hash = calcular_hash(url)
    
    # Obtener titulo para slug
    from readability import Document
    import requests
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    import procesar_pdf
    if procesar_pdf.es_pdf(resp.content, resp.headers.get("Content-Type", "")):
        titulo = url.rstrip("/").rsplit("/", 1)[-1] or "sin-titulo"
    else:
        resp.encoding = _codificacion(resp)
        doc = Document(resp.text)
        titulo = (doc.short_title() or "sin-titulo").strip()
    slug = slugify(titulo)
    
    print(f"Titulo: {titulo}")
    print(f"Slug: {slug}")
    print(f"Hash: {url_hash}")
    
    raw_dir, proc_dir = crear_directorios(slug, url_hash)
    try:
        res = procesar(url, raw_dir, proc_dir)
    except Exception as e:
        print(f"ERROR texto: {type(e).__name__}: {e}")
        append_memoria("error", f"texto fallo {url}", f"{type(e).__name__}: {e}")
        sys.exit(2)
    import json
    reg = json.loads(res["json"].read_text(encoding="utf-8"))
    append_indice(reg["titulo"], "texto", reg["metodo"], res["estado"], reg["url_hash"])
    print(f"OK [{res['estado']}] metodo={reg['metodo']} parrafos={len(reg['segmentos'])}")
    print(f"  {res['md']}")
    print(f"  {res['json']}")


if __name__ == "__main__":
    main()
