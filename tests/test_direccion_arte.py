# -*- coding: utf-8 -*-
"""Pruebas de la direccion de arte.

Lo que se prueba es lo que rompe la coherencia en silencio: que el estilo
llegue a TODAS las escenas y no solo a la primera, que no se pierda la
descripcion original, y que la eleccion no falle por un plural.
"""
from __future__ import annotations

import synthesis.direccion_arte as da


# ── eleccion del estilo ──────────────────────────────────────────────────

def test_da_01a_elige_por_el_tema():
    assert da.estilo_para("el robo al banco de Bangladesh").nombre == "noir"
    assert da.estilo_para("los agujeros negros").nombre == "cosmico"
    assert da.estilo_para("como llenar un formato").nombre == "institucional"


def test_da_01b_la_coincidencia_es_por_raiz_no_por_palabra():
    """Regresion: 'agujeros' en plural elegia el estilo equivocado porque la
    pista decia 'agujero' y la comparacion era por palabra exacta."""
    assert da.estilo_para("los agujeros negros").nombre == "cosmico"
    assert da.estilo_para("las galaxias lejanas").nombre == "cosmico"
    assert da.estilo_para("fallas del sistema").nombre == "noir"


def test_da_01c_tema_vacio_no_revienta():
    assert da.estilo_para("").nombre == da.ESTILO_POR_DEFECTO
    assert da.estilo_para(None).nombre == da.ESTILO_POR_DEFECTO


# ── aplicacion ───────────────────────────────────────────────────────────

def _sb(n=3):
    return {"scenes": [{"id": "s%d" % i,
                        "visual": {"description": "escena numero %d" % i}}
                       for i in range(n)],
            "metadata": {"title": "T"}}


def test_da_02a_el_estilo_llega_a_todas_las_escenas():
    """Una escena sin estilo rompe la coherencia de todo el video."""
    r = da.aplicar(_sb(4), da.ESTILOS["noir"])
    assert len(r["scenes"]) == 4
    for e in r["scenes"]:
        d = e["visual"]["description"]
        # La escena va PRIMERO y el estilo detras: con el estilo delante el
        # modelo ignoraba la escena. Ver _componer.
        assert d.startswith("escena numero")
        # Se comprueba que el estilo LLEGO, sin atarse a su redaccion
        # exacta: los prefijos se acortaron para caber en los 77 tokens
        # de CLIP y volveran a cambiar.
        assert da.ESTILOS["noir"].prefijo.split(",")[0] in d
        assert e["visual"]["estilo"] == "noir"
        assert e["visual"]["negative_prompt"]


def test_da_02b_conserva_la_descripcion_original_detras_del_prefijo():
    """El estilo dice COMO se ve; la descripcion, QUE se ve. Hacen falta las
    dos: sin la descripcion el modelo pinta el estilo sobre nada."""
    r = da.aplicar(_sb(1), da.ESTILOS["cosmico"])
    assert "escena numero 0" in r["scenes"][0]["visual"]["description"]


def test_da_02c_no_muta_el_storyboard_recibido():
    sb = _sb(2)
    da.aplicar(sb, da.ESTILOS["noir"])
    assert sb["scenes"][0]["visual"]["description"] == "escena numero 0"


def test_da_02d_marca_el_estilo_en_los_metadatos():
    r = da.aplicar(_sb(1), da.ESTILOS["noir"])
    assert r["metadata"]["estilo"] == "noir"
    assert r["metadata"]["banda_negra"] is True
    assert r["metadata"]["title"] == "T"


def test_da_02e_funciona_con_escenas_planas_sin_visual():
    sb = {"scenes": [{"visual_description": "una sala vacia"}]}
    r = da.aplicar(sb, da.ESTILOS["institucional"])
    e = r["scenes"][0]
    assert e["visual_description"].startswith("una sala vacia")
    assert (da.ESTILOS["institucional"].prefijo.split(",")[0]
            in e["visual_description"])


def test_da_02f_storyboard_sin_escenas_no_revienta():
    r = da.aplicar({"scenes": []}, da.ESTILOS["noir"])
    assert r["scenes"] == []


# ── el negativo ──────────────────────────────────────────────────────────

def test_da_03a_todos_los_estilos_evitan_caras_deformes():
    """Una cara mal generada destruye la credibilidad de una pieza
    documental mas que cualquier diferencia de color."""
    for e in da.ESTILOS.values():
        assert "deformed face" in e.evitar
        assert "watermark" in e.evitar


def test_da_03b_lo_que_se_evita_va_en_el_negativo_nunca_en_el_positivo():
    """Regresion de un error clasico: escribir "no visible faces" en el
    prompt positivo no quita caras, las ATRAE — estos modelos no procesan la
    negacion, leen "faces" y pintan caras. Verificado: se pidio una pantalla
    con codigo y salio un rostro."""
    for e in da.ESTILOS.values():
        assert "face" in e.evitar
        assert "no visible faces" not in e.prefijo
        assert " no " not in e.prefijo


# ── banda negra ──────────────────────────────────────────────────────────

def test_da_04a_la_banda_deja_negro_arriba_y_abajo():
    f = da.filtro_banda_negra(1080, 1920)
    assert "pad=1080:1920" in f
    assert "black" in f
    # 1080 de ancho en 16:9 da 607.5 -> 606, par para que h264 no se queje
    assert "1080:606" in f


def test_da_04b_el_alto_de_la_banda_siempre_es_par():
    """h264 rechaza dimensiones impares."""
    for ancho in (1080, 720, 1000, 1234):
        f = da.filtro_banda_negra(ancho, 1920)
        alto = int(f.split("crop=%d:" % ancho)[1].split(",")[0])
        assert alto % 2 == 0
