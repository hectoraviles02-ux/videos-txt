# -*- coding: utf-8 -*-
"""PDF por pagina (22-oct-2026): valido, escaneado, corrupto, paginas mezcladas y que HTML no cambie."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import comun  # noqa: E402
import procesar_pdf  # noqa: E402
import procesar_texto  # noqa: E402

fitz = pytest.importorskip("fitz")

URL = "https://ejemplo.org/informes/reporte_final.pdf"
TEXTO_P1 = "The Board found that the root cause was the failure to use metric units in the coding of a ground software file."
TEXTO_P3 = "Recommendation one: verify the conversion of units in every interface before launch."


def _pdf(paginas):
    """paginas: lista de str (texto) o None (pagina solo con imagen, como un escaneo)."""
    doc = fitz.open()
    for p in paginas:
        pag = doc.new_page()
        if p is None:
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 60), 0)
            pix.clear_with(200)
            pag.insert_image(fitz.Rect(50, 50, 300, 300), pixmap=pix)
        else:
            pag.insert_textbox(fitz.Rect(50, 50, 550, 750), p, fontsize=11)
    datos = doc.tobytes()
    doc.close()
    return datos


def _correr(tmp_path, contenido):
    raw, proc = tmp_path / "raw", tmp_path / "proc"
    raw.mkdir()
    proc.mkdir()
    res = procesar_pdf.procesar_contenido(URL, contenido, raw, proc)
    return res, json.loads(res["json"].read_text(encoding="utf-8")), raw


def test_pdf_valido_un_segmento_por_pagina_con_numero(tmp_path):
    res, reg, raw = _correr(tmp_path, _pdf([TEXTO_P1, TEXTO_P3]))
    assert res["estado"] == "EXTRAIDO"
    assert [s["pagina"] for s in reg["segmentos"]] == [1, 2]
    assert "metric units" in reg["segmentos"][0]["texto"]
    assert reg["metodo"] == "extractor_articulo" and reg["verificada"] is True
    assert reg["paginas_total"] == 2
    assert (raw / "documento.pdf").read_bytes()[:4] == b"%PDF"     # crudo conservado
    assert not any("%PDF" in s["texto"] for s in reg["segmentos"])


def test_pdf_escaneado_sin_texto_queda_borrador_y_no_inventa(tmp_path):
    res, reg, _ = _correr(tmp_path, _pdf([None, None]))
    assert res["estado"] == "BORRADOR"
    assert reg["segmentos"] == [] and reg["verificada"] is False
    assert any("ninguna de las 2 paginas" in a for a in reg["avisos"])


def test_pdf_corrupto_queda_borrador_sin_binario(tmp_path):
    res, reg, _ = _correr(tmp_path, b"%PDF-1.4\n\x00\x01basura sin estructura\x00" * 20)
    assert res["estado"] == "BORRADOR"
    assert reg["segmentos"] == []
    assert any("no se pudo leer" in a or "ninguna" in a or "no tiene paginas" in a for a in reg["avisos"])


def test_pdf_mixto_avisa_paginas_sin_texto_y_conserva_numeracion(tmp_path):
    res, reg, _ = _correr(tmp_path, _pdf([TEXTO_P1, None, TEXTO_P3]))
    assert res["estado"] == "EXTRAIDO"
    assert [s["pagina"] for s in reg["segmentos"]] == [1, 3]       # la 2 no se rellena ni se corre
    assert any("1 de 3 paginas sin texto" in a and "[2]" in a for a in reg["avisos"])


def test_es_pdf_por_firma_o_cabecera():
    assert procesar_pdf.es_pdf(b"%PDF-1.7 ...")
    assert procesar_pdf.es_pdf(b"\n  %PDF-1.2")
    assert procesar_pdf.es_pdf(b"<html>", "application/pdf; charset=binary")
    assert not procesar_pdf.es_pdf(b"<!doctype html><html>", "text/html")


class _Resp:
    def __init__(self, contenido, ctype):
        self.content = contenido
        self.headers = {"Content-Type": ctype}
        self.text = contenido.decode("latin-1")
        self.apparent_encoding = "utf-8"
        self.encoding = None


def test_procesar_texto_desvia_pdf_al_extractor_pdf(tmp_path, monkeypatch):
    datos = _pdf([TEXTO_P1])
    monkeypatch.setattr(comun, "obtener_seguro", lambda *a, **k: _Resp(datos, "application/octet-stream"))
    raw, proc = tmp_path / "raw", tmp_path / "proc"
    raw.mkdir()
    proc.mkdir()
    res = procesar_texto.procesar(URL, raw, proc)
    reg = json.loads(res["json"].read_text(encoding="utf-8"))
    assert res["estado"] == "EXTRAIDO" and reg["segmentos"][0]["pagina"] == 1
    assert not (raw / "pagina.html").exists()


def test_html_sigue_igual(tmp_path, monkeypatch):
    html = ("<html><head><title>Nota</title></head><body><article><p>" + "Primer parrafo con contenido real. " * 8
            + "</p><p>" + "Segundo parrafo con mas contenido. " * 8 + "</p></article></body></html>").encode()
    monkeypatch.setattr(comun, "obtener_seguro", lambda *a, **k: _Resp(html, "text/html"))
    raw, proc = tmp_path / "raw", tmp_path / "proc"
    raw.mkdir()
    proc.mkdir()
    res = procesar_texto.procesar("https://ejemplo.org/nota", raw, proc)
    reg = json.loads(res["json"].read_text(encoding="utf-8"))
    assert res["estado"] == "EXTRAIDO"
    assert (raw / "pagina.html").exists()
    assert "pagina" not in reg["segmentos"][0]
    assert comun._estado(reg) == "EXTRAIDO"
