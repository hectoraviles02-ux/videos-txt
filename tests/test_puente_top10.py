# -*- coding: utf-8 -*-
"""Puente TOP10 -> generar_creativo. Pruebas discriminantes A-E.

La entrada de A/E es la salida REAL del motor del 2026-09-21, versionada como
evidencia en `docs/operations/evidencia/validacion_vertical_20260922/`. No es
un fixture fabricado: la saco `content_factory informe` de la corrida real, y
su procedencia contra los crudos esta demostrada 10/10 en esa misma carpeta.
"""

from __future__ import annotations

import copy
import inspect
import json
import random
from pathlib import Path

import pytest

from synthesis import puente_top10
from synthesis.puente_top10 import PuenteInvalido, entrada_desde_top10

TOP_REAL = (Path(__file__).resolve().parent.parent / "docs" / "operations" / "evidencia"
            / "validacion_vertical_20260922" / "top10_json_2026-09-21.json")


@pytest.fixture
def top():
    return json.loads(TOP_REAL.read_text(encoding="utf-8"))


# ── A · TOP real valido -> entrada valida, y del contrato que espera el generador ──

def test_a_el_top_real_produce_la_entrada_con_lo_que_trae_el_top(top):
    e = entrada_desde_top10(top, 1)
    fuente = top["temas"][0]
    assert fuente["posicion"] == 1
    assert e.titulo == fuente["titulo"] == "Blank Space that cannot be removed by TRIM ()"
    assert e.angulo == fuente["angulo_propuesto"]
    assert e.hash_titulo == fuente["hash_titulo"]
    assert e.fecha_corte == "2026-09-21"
    assert e.hash_entradas == top["hash_entradas"]
    assert [(x.fuente, x.url) for x in e.evidencia] == [
        (x["fuente"], x["url"]) for x in fuente["evidencia"]]
    # La procedencia llega al contexto del guion, con la identidad de la corrida.
    ctx = e.contexto_procedencia()
    assert fuente["evidencia"][0]["url"] in ctx
    assert top["hash_entradas"] in ctx


def test_a_los_argumentos_son_exactamente_los_de_generar_creativo(top):
    """Si `generar_creativo` cambia de firma, esto cae antes que la corrida."""
    from synthesis.script_generator import ScriptGenerator

    args = entrada_desde_top10(top, 1).argumentos_generar_creativo()
    firma = inspect.signature(ScriptGenerator.generar_creativo)
    firma.bind(None, **args)                      # revienta si sobra o falta algo
    assert set(args) == set(firma.parameters) - {"self"}


def test_a_formato_no_se_convierte_a_minutos(top):
    """"corto" no tiene duracion declarada: minutos va en 0 y el formato viaja aparte."""
    e = entrada_desde_top10(top, 1)
    assert e.formato == top["temas"][0]["formato"] == "corto"
    assert e.argumentos_generar_creativo()["minutos"] == 0.0


# ── B · cambia la evidencia de entrada -> cambia la procedencia de salida ─────────

def test_b_otra_url_en_la_entrada_es_otra_url_en_la_salida(top):
    original = entrada_desde_top10(top, 1)
    mutado = copy.deepcopy(top)
    otra = "https://www.reddit.com/r/excel/comments/zzzzzzz/otra/"
    mutado["temas"][0]["evidencia"][0]["url"] = otra
    e = entrada_desde_top10(mutado, 1)
    assert e.evidencia[0].url == otra
    ctx = e.argumentos_generar_creativo()["contexto"]
    assert otra in ctx
    assert original.evidencia[0].url not in ctx


def test_b_el_contexto_adicional_no_desplaza_a_la_procedencia(top):
    e = entrada_desde_top10(top, 1)
    ctx = e.argumentos_generar_creativo("- hecho X [fuente Y]")["contexto"]
    assert ctx.startswith("- hecho X [fuente Y]")
    assert e.evidencia[0].url in ctx


# ── C · tema sin evidencia -> rechazo explicito ──────────────────────────────────

@pytest.mark.parametrize("romper", [
    lambda t: t.__setitem__("evidencia", []),
    lambda t: t.pop("evidencia"),
    lambda t: t["evidencia"][0].__setitem__("url", ""),
    lambda t: t["evidencia"][0].pop("fuente"),
], ids=["evidencia-vacia", "sin-evidencia", "url-vacia", "sin-fuente"])
def test_c_sin_procedencia_no_pasa(top, romper):
    romper(top["temas"][0])
    with pytest.raises(PuenteInvalido):
        entrada_desde_top10(top, 1)


def test_c_sin_angulo_no_se_inventa_uno(top):
    top["temas"][0]["angulo_propuesto"] = ""
    with pytest.raises(PuenteInvalido, match="angulo_propuesto"):
        entrada_desde_top10(top, 1)


# ── D · sin fallback silencioso a fixtures ni temas escritos a mano ─────────────

@pytest.mark.parametrize("top10", [
    {"fecha_corte": "2026-09-21", "hash_entradas": "h", "temas": []},
    {"fecha_corte": "2026-09-21", "hash_entradas": "h"},
    "no es json",
    [],
], ids=["temas-vacio", "sin-temas", "json-invalido", "no-objeto"])
def test_d_un_top_inservible_detiene_la_cadena(top10):
    with pytest.raises(PuenteInvalido):
        entrada_desde_top10(top10, 1)


def test_d_una_posicion_ausente_no_cae_en_otra(top):
    with pytest.raises(PuenteInvalido, match="posicion 11"):
        entrada_desde_top10(top, 11)


def test_d_el_modulo_no_conoce_fixtures_ni_temas_concretos():
    fuente = inspect.getsource(puente_top10)
    for prohibido in ("fixtures", "corrida_esperada", "SYSTRAN", "faster-whisper",
                      "Blank Space", "smoke_top10"):
        assert prohibido not in fuente, prohibido


# ── E · el elemento N es el N del TOP, aunque la lista venga en otro orden ──────

def test_e_se_elige_por_posicion_y_no_por_lugar_en_la_lista(top):
    por_posicion = {t["posicion"]: t for t in top["temas"]}
    barajado = copy.deepcopy(top)
    random.Random(20260922).shuffle(barajado["temas"])
    assert [t["posicion"] for t in barajado["temas"]] != sorted(por_posicion)
    for n, esperado in por_posicion.items():
        e = entrada_desde_top10(barajado, n)
        assert (e.posicion, e.titulo, e.hash_titulo) == (
            n, esperado["titulo"], esperado["hash_titulo"])


def test_e_una_posicion_repetida_se_rechaza(top):
    top["temas"][1]["posicion"] = 1
    with pytest.raises(PuenteInvalido, match="repite"):
        entrada_desde_top10(top, 1)
