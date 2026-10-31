# -*- coding: utf-8 -*-
"""Pruebas de la investigacion en fuentes oficiales.

Se prueba lo que puede fallar en silencio: que la cita viaje pegada al dato,
que una fuente caida no tumbe las demas, y que la compuerta de material
insuficiente se dispare en vez de entregar un documental de relleno.

Las llamadas a la red y a Ollama estan sustituidas: aqui se prueba la
logica, no la disponibilidad de arXiv.
"""
from __future__ import annotations

import json

import pytest

import synthesis.investigacion as inv


# ── parseo de cada fuente ────────────────────────────────────────────────

ARXIV_XML = """<feed>
<entry>
  <id>http://arxiv.org/abs/2608.19675v1</id>
  <published>2026-08-20T00:00:00Z</published>
  <title>Towards independent event horizon
  imaging</title>
  <summary>The Event Horizon Telescope Collaboration's images of the
  supermassive black holes have provided the first views.</summary>
</entry>
<entry>
  <id>http://arxiv.org/abs/1607.03034v1</id>
  <published>2016-07-11T00:00:00Z</published>
  <title>Observing Active Galactic Nuclei</title>
  <summary>Un resumen cualquiera con suficiente texto para pasar.</summary>
</entry>
</feed>"""


def test_in_01a_arxiv_extrae_cita_completa(monkeypatch):
    monkeypatch.setattr(inv, "_get", lambda url: ARXIV_XML)
    docs = inv.fuente_arxiv("event horizon telescope")
    assert len(docs) == 2
    d = docs[0]
    assert d.institucion == "arXiv"
    assert d.url == "http://arxiv.org/abs/2608.19675v1"
    assert d.fecha == "2026-08-20"
    # el titulo viene partido en varias lineas en el XML real
    assert "\n" not in d.titulo
    assert d.titulo == "Towards independent event horizon imaging"


def test_in_01b_arxiv_sin_red_devuelve_vacio(monkeypatch):
    monkeypatch.setattr(inv, "_get", lambda url: None)
    assert inv.fuente_arxiv("lo que sea") == []


def test_in_01c_arxiv_ignora_entradas_incompletas(monkeypatch):
    monkeypatch.setattr(inv, "_get",
                        lambda url: "<feed><entry><id>x</id></entry></feed>")
    assert inv.fuente_arxiv("x") == []


NASA_JSON = json.dumps({"collection": {"items": [
    {"href": "https://images-assets.nasa.gov/uno",
     "data": [{"title": "Agujero negro", "date_created": "2016-04-06T00:00:00Z",
               "description": " ".join(["palabra"] * 40)}]},
    {"href": "https://images-assets.nasa.gov/dos",
     "data": [{"title": "Muy corto", "date_created": "2020-01-01T00:00:00Z",
               "description": "dos palabras"}]},
]}})


def test_in_02a_nasa_descarta_descripciones_pobres(monkeypatch):
    """Una descripcion de dos palabras no da un hecho."""
    monkeypatch.setattr(inv, "_get", lambda url: NASA_JSON)
    docs = inv.fuente_nasa("black hole")
    assert len(docs) == 1
    assert docs[0].institucion == "NASA"
    assert docs[0].fecha == "2016-04-06"


def test_in_02b_nasa_json_invalido_no_revienta(monkeypatch):
    monkeypatch.setattr(inv, "_get", lambda url: "esto no es json")
    assert inv.fuente_nasa("x") == []


# ── la cita viaja pegada al dato ─────────────────────────────────────────

def test_in_03a_el_hecho_conserva_su_procedencia(monkeypatch):
    doc = inv.Documento(titulo="T", texto="x" * 200, url="http://arxiv.org/abs/1",
                        institucion="arXiv", fecha="2026-08-20")
    monkeypatch.setattr(inv, "_pedir",
                        lambda p, **k: {"hechos": ["Un dato concreto con cifras 225"]})
    hechos = inv.hechos_de(doc, "tema")
    assert len(hechos) == 1
    h = hechos[0]
    assert h.institucion == "arXiv"
    assert h.url == "http://arxiv.org/abs/1"
    assert h.fecha == "2026-08-20"
    assert h.cita() == "arXiv, 2026"


def test_in_03b_descarta_hechos_demasiado_cortos(monkeypatch):
    """Tres palabras no son un hecho, son una etiqueta."""
    doc = inv.Documento("T", "x" * 200, "u", "arXiv")
    monkeypatch.setattr(inv, "_pedir",
                        lambda p, **k: {"hechos": ["muy corto", "este si tiene suficientes palabras"]})
    assert len(inv.hechos_de(doc, "tema")) == 1


def test_in_03c_si_ollama_cae_no_tumba_la_investigacion(monkeypatch):
    doc = inv.Documento("T", "x" * 200, "u", "arXiv")

    def cae(p, **k):
        raise ValueError("ollama caido")

    monkeypatch.setattr(inv, "_pedir", cae)
    assert inv.hechos_de(doc, "tema") == []


def test_in_03d_el_texto_del_documento_llega_al_modelo(monkeypatch):
    vistos = []
    doc = inv.Documento("Titulo", "dato irrepetible del resumen", "u",
                        "arXiv", "2026-08-20")

    def espia(p, **k):
        vistos.append(p)
        return {"hechos": []}

    monkeypatch.setattr(inv, "_pedir", espia)
    inv.hechos_de(doc, "agujeros negros")
    assert "dato irrepetible del resumen" in vistos[0]
    assert "arXiv" in vistos[0]
    assert "agujeros negros" in vistos[0]


# ── deduplicacion ────────────────────────────────────────────────────────

def test_in_04a_quita_el_mismo_dato_repetido_entre_fuentes():
    a = inv.Hecho("La masa es de 6500 millones de veces la del Sol", "arXiv", "u1")
    b = inv.Hecho("La masa es de 6500 millones de veces la del Sol", "NASA", "u2")
    c = inv.Hecho("Otro dato completamente distinto aqui", "NASA", "u2")
    assert len(inv._sin_repetidos([a, b, c])) == 2


# ── orquestacion y compuerta ─────────────────────────────────────────────

def _doc(i):
    return inv.Documento("T%d" % i, "x" * 200, "http://u/%d" % i, "arXiv", "2026")


def test_in_05a_una_fuente_caida_no_tumba_las_demas(monkeypatch):
    monkeypatch.setitem(inv.FUENTES, "rota", lambda t, **k: [])
    monkeypatch.setitem(inv.FUENTES, "buena", lambda t, **k: [_doc(i) for i in range(3)])
    monkeypatch.setattr(inv, "hechos_de", lambda d, t: [
        inv.Hecho("hecho concreto %s numero %d con cifras" % (d.titulo, i),
                  d.institucion, d.url)
        for i in range(4)])
    r = inv.investigar("tema", fuentes=["rota", "buena"])
    assert any("rota" in a for a in r.avisos)
    assert len(r.documentos) == 3


def test_in_05b_material_escaso_avisa_pero_NO_bloquea(monkeypatch):
    """Bloquear aqui impedia justo los temas concretos, que son los que
    tienen historia.

    MEDIDO el 2026-08-22: "el robo al Banco de Bangladesh" dio 4 hechos y el
    sistema se nego a producir; "ciberataques al sistema financiero" dio 18,
    de paises distintos, y produjo una lista sin historia. La compuerta
    bloqueaba el tema que SI tenia historia y dejaba pasar el que no.
    """
    monkeypatch.setitem(inv.FUENTES, "pobre", lambda t, **k: [_doc(0)])
    monkeypatch.setattr(inv, "hechos_de",
                        lambda d, t: [inv.Hecho("un solo hecho concreto aqui",
                                                "arXiv", "u")])
    r = inv.investigar("tema estrecho", fuentes=["pobre"])
    assert len(r.hechos) == 1
    assert any("solo 1 hechos" in a for a in r.avisos)


def test_in_05b2_sin_ningun_hecho_si_se_detiene(monkeypatch):
    """Con cero material no hay nada que citar y se dice."""
    monkeypatch.setitem(inv.FUENTES, "vacia", lambda t, **k: [])
    with pytest.raises(inv.SinFuentes) as e:
        inv.investigar("tema inexistente", fuentes=["vacia"])
    assert "creacion propia" in str(e.value)


def test_in_05c_fuente_desconocida_se_avisa(monkeypatch):
    monkeypatch.setitem(inv.FUENTES, "buena", lambda t, **k: [_doc(i) for i in range(3)])
    monkeypatch.setattr(inv, "hechos_de", lambda d, t: [
        inv.Hecho("hecho concreto %s numero %d con cifras" % (d.titulo, i),
                  "arXiv", d.url)
        for i in range(4)])
    r = inv.investigar("tema", fuentes=["inventada", "buena"])
    assert any("inventada" in a for a in r.avisos)


def test_in_05d_las_fuentes_quedan_listadas_para_citar(monkeypatch):
    monkeypatch.setitem(inv.FUENTES, "buena", lambda t, **k: [_doc(i) for i in range(3)])
    monkeypatch.setattr(inv, "hechos_de", lambda d, t: [
        inv.Hecho("hecho concreto %s numero %d con cifras" % (d.titulo, i),
                  d.institucion, d.url)
        for i in range(4)])
    r = inv.investigar("tema", fuentes=["buena"])
    assert len(r.fuentes) == 3
    assert all("arXiv" in f and "http" in f for f in r.fuentes)
