# -*- coding: utf-8 -*-
"""La compuerta de relevancia: que el material ajeno no pase por ser mucho.

El 2026-08-29 se pidio un documental de la salsa Valentina. La busqueda fue a
arXiv, arXiv solo tiene fisica, y volvieron 71 hechos impecablemente concretos
sobre desintegracion de mesones. La compuerta de entonces contaba hechos, y
71 > 8, asi que dejo pasar. El guion salio titulado "La Salsa de Valentina y el
Misterio del B^0_s a Muones" sin mencionar la salsa ni una vez.

Aqui no se prueba el modelo -eso es lento y variable-, se prueba la LOGICA:
documentos que no aportan hechos cuentan como fuera de tema, y a partir de
cierta proporcion el usuario tiene que enterarse.
"""
import synthesis.investigacion as I


class _Doc:
    """Lo minimo que `investigar` le pide a un documento."""

    def __init__(self, titulo, del_tema):
        self.titulo = titulo
        self.url = "https://ejemplo.org/" + titulo[:8]
        self.institucion = "Fuente"
        self.fecha = "2024"
        self.texto = titulo
        self.del_tema = del_tema


def _monta(monkeypatch, docs):
    """Una fuente falsa que devuelve `docs`, y un extractor que respeta
    la marca `del_tema` de cada uno."""
    monkeypatch.setattr(I, "FUENTES", {"falsa": lambda tema, limite=5: docs},
                        raising=False)
    monkeypatch.setattr(I, "a_ingles", lambda tema: tema)
    monkeypatch.setattr(
        I, "hechos_de",
        lambda d, tema: ([I.Hecho("dato concreto numero %d del tema" % i,
                                  d.institucion, d.url, d.fecha)
                          for i in range(9)] if d.del_tema else []))


def test_material_ajeno_dispara_aviso(monkeypatch):
    """8 de 10 documentos fuera de tema: el usuario tiene que saberlo."""
    docs = [_Doc("doc %d" % i, del_tema=(i < 2)) for i in range(10)]
    _monta(monkeypatch, docs)
    inv = I.investigar("la salsa Valentina", fuentes=["falsa"])
    assert inv.avisos, "8 de 10 documentos ajenos y NINGUN aviso"
    texto = " ".join(inv.avisos)
    assert "8 de 10" in texto
    assert "salsa Valentina" in texto


def test_material_del_tema_no_molesta(monkeypatch):
    """Si el material SI es del tema, no se inventa una alarma."""
    docs = [_Doc("doc %d" % i, del_tema=True) for i in range(10)]
    _monta(monkeypatch, docs)
    inv = I.investigar("la salsa Valentina", fuentes=["falsa"])
    fuera = [a for a in inv.avisos if "no hablaban" in a]
    assert not fuera, "alarma de relevancia con material que SI era del tema"


def test_todo_ajeno_se_detiene(monkeypatch):
    """Cero hechos utiles: no se escribe un documental de otro campo.

    `SinFuentes` ya lo maneja bien en el handler: avisa por Telegram y para.
    Lo que se prueba es que la compuerta LLEGUE hasta ahi.
    """
    import pytest
    docs = [_Doc("doc %d" % i, del_tema=False) for i in range(10)]
    _monta(monkeypatch, docs)
    with pytest.raises(I.SinFuentes):
        I.investigar("la salsa Valentina", fuentes=["falsa"])
