# -*- coding: utf-8 -*-
"""Pruebas del gate visual. Reglas GENERALES (no codificadas para V2/9:17):
los inputs V2/9:17/sintetico solo EJERCITAN las reglas. Sin %-prueba de calidad."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))
import gate_visual as GV  # noqa: E402


def _plan(planos):
    return {"pista_visual": planos}


def _rec(plano, tratamiento, rid="r1", clase="EVIDENCIA-REAL-DEL-CASO", dur=6, **kw):
    d = {"plano": plano, "tratamiento": tratamiento, "recurso_id": rid, "clase": clase,
         "in": (plano - 1) * dur, "out": plano * dur}
    d.update(kw)
    return d


def _txt(plano, dur=6):
    return {"plano": plano, "tratamiento": "rotulo_pleno", "clase": "PROPIO",
            "in": (plano - 1) * dur, "out": plano * dur, "texto": "frase"}


def _guion(factuales):
    return {"beats": [{"id": f"b{i+1}", "factual": f, "claim_ids": (["c"] if f else []),
                       "texto": ("Una afirmacion de contenido con varias palabras suficientes" if f else "pausa")}
                      for i, f in enumerate(factuales)]}


# ── reglas generales ──
def test_contenido_con_texto_es_deficit():
    res = GV.evaluar(_plan([_txt(1)]), _guion([True]))
    assert not res["apto"] and any("DEFICIT" in b for b in res["bloqueos"])


def test_transicion_breve_con_texto_no_es_deficit():
    res = GV.evaluar(_plan([_txt(1)]), _guion([False]))
    # una transicion (no factual, corta) con texto NO bloquea
    assert res["apto"], res["bloqueos"]


def test_contenido_con_tratamiento_real_apto():
    res = GV.evaluar(_plan([_rec(1, "paneo"), _rec(2, "clip"), _rec(3, "ficha")]), _guion([True, True, True]))
    assert res["apto"], res["bloqueos"]


def test_recreacion_sin_rotulo_bloquea():
    res = GV.evaluar(_plan([_rec(1, "ficha", clase="RECREACION")]), _guion([True]))
    assert not res["apto"] and any("rotulo" in b for b in res["bloqueos"])


def test_sd_recreacion_anunciada_como_video_bloquea():
    res = GV.evaluar(_plan([_rec(1, "clip", clase="RECREACION", rotulo="RECREACION")]), _guion([True]))
    assert not res["apto"] and any("VIDEO" in b for b in res["bloqueos"])


def test_inmovilidad_prolongada_bloquea():
    # un plano estatico (tratamiento sin movimiento) sostenido mucho tiempo
    p = {"plano": 1, "tratamiento": "estatico", "recurso_id": "r", "clase": "ILUSTRATIVO", "in": 0, "out": 30}
    res = GV.evaluar(_plan([p]), _guion([True]))
    assert not res["apto"]  # tratamiento inexistente o inmovilidad -> bloqueo


def test_recurso_sin_derechos_bloquea():
    man = {"r1": {"estado_verificacion": "PENDIENTE", "derechos": "REQUIERE-AUTORIZACION"}}
    res = GV.evaluar(_plan([_rec(1, "ficha")]), _guion([True]), manifiesto=man)
    assert not res["apto"] and any("derechos" in b for b in res["bloqueos"])


def test_repeticion_avisa_no_bloquea():
    planos = [_rec(i, "ficha", rid="mismo") for i in range(1, 5)]
    res = GV.evaluar(_plan(planos), _guion([True] * 4))
    assert res["apto"]  # repetir no bloquea
    assert any("repetido" in a for a in res["avisos"])  # pero avisa


# ── regresion: patrones V2 vs 9:17 (los inputs ejercitan reglas generales) ──
def test_patron_v2_apto():
    # V2: documentos reales en movimiento + clip real, cero texto-sobre-contenido
    planos = [_rec(1, "paneo"), _rec(2, "clip"), _rec(3, "ficha"), _rec(4, "banda")]
    res = GV.evaluar(_plan(planos), _guion([True, True, True, True]))
    assert res["apto"], res["bloqueos"]


def test_patron_9min_no_apto():
    # 9:17: mayoria de beats de contenido resueltos con texto a pantalla
    planos = [_rec(1, "ficha"), _txt(2), _txt(3), _rec(4, "ficha"), _txt(5), _txt(6)]
    res = GV.evaluar(_plan(planos), _guion([True, True, True, True, True, True]))
    assert not res["apto"]
    assert sum(1 for b in res["bloqueos"] if "DEFICIT" in b) >= 3


def test_caso_sintetico_independiente():
    # otro documental (tema distinto): bien tratado pasa; con deficit falla
    bien = _plan([_rec(1, "paneo"), {"plano": 2, "tratamiento": "infografia", "clase": "INFOGRAFIA", "in": 6, "out": 12}, _rec(3, "clip")])
    assert GV.evaluar(bien, _guion([True, True, True]))["apto"]
    mal = _plan([_txt(1), _txt(2), _txt(3)])
    assert not GV.evaluar(mal, _guion([True, True, True]))["apto"]
