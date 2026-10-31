# -*- coding: utf-8 -*-
"""Motor «diagrama» (23-oct-2026): planificador, reglas de texto en pantalla y conexión con el flujo de enlaces del
bot. Sin Claude real, sin voz, sin render: se sustituyen por espías. El candado de la fábrica se aísla en tmp."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts" / "diagrama"))
import plan as PL  # noqa: E402
import producir as P  # noqa: E402
import render as RD  # noqa: E402
from telegram_bot import candado  # noqa: E402

SB = {"scenes": [
    {"narration": "Tercero, Node.js, el motor que ejecuta JavaScript. Corrigieron 10 fallas."},
    {"narration": "Un archivo comprimido manipulado podía escribir fuera de la memoria y abrir la puerta a ejecutar "
                  "código. ¿Qué hacer? Actualiza a la última versión de tu rama."}]}
LEY = "ESQUEMA ILUSTRATIVO · FUENTE: REEL FB 1EA7jN1Hn3"
BUENO = {"id": "node", "escenas": [
    {"titulo": "Tercero, Node.js", "subtitulo": "el motor que ejecuta JavaScript",
     "arriba": {"tipo": "numero", "valor": "10", "texto": "Corrigieron 10 fallas", "frase": 2}, "centro": None, "abajo": None},
    {"titulo": "¿Qué hacer?", "subtitulo": None, "arriba": None,
     "centro": {"tipo": "flujo", "encabezado": "EL MECANISMO", "pasos": [
         {"texto": "archivo comprimido manipulado", "frase": 3},
         {"texto": "escribir fuera de la memoria", "frase": 3, "palabra": "escribir"},
         {"texto": "ejecutar código", "frase": 3, "palabra": "ejecutar"}]},
     "abajo": {"tipo": "lista", "encabezado": "¿Qué hacer?", "items": [
         {"texto": "Actualiza a la última versión de tu rama", "frase": 5}]}}],
    "dice": {"1": "Tercero, Nóud yei es, el motor que ejecuta Yava Script.", "2": "Corrigieron diez fallas."}}


def _claude(salidas):
    llamadas = []

    def ejecutar(cmd, **kw):
        llamadas.append(kw["input"])
        return SimpleNamespace(returncode=0, stdout=salidas[min(len(llamadas), len(salidas)) - 1], stderr="")
    return ejecutar, llamadas


def test_frases_por_escena_en_orden():
    fr = PL.frases_de(SB)
    assert [f["escena"] for f in fr] == [1, 1, 2, 2, 2] and fr[3]["texto"] == "¿Qué hacer?"


def test_plan_de_claude_valido_se_usa_y_guarda_como_suena():
    ejecutar, llamadas = _claude([json.dumps(BUENO)])
    p = PL.planificar(PL.frases_de(SB), LEY, ejecutar=ejecutar, claude="claude")
    assert p["origen_plan"] == "claude (intento 1)" and len(llamadas) == 1
    assert p["frases"][0]["dice"].startswith("Tercero, Nóud") and p["frases"][1]["dice"] == "Corrigieron diez fallas."
    assert p["frases"][4]["dice"] == "Actualiza a la última versión de tu rama."   # sin «dice»: la frase tal cual


def test_texto_inventado_se_rechaza_se_pide_otra_vez_y_si_no_queda_se_usa_el_respaldo():
    malo = json.loads(json.dumps(BUENO))
    malo["escenas"][1]["centro"]["pasos"][2]["texto"] = "los atacantes tomaron el control"   # no está en la narración
    ejecutar, llamadas = _claude([json.dumps(malo), json.dumps(malo)])
    p = PL.planificar(PL.frases_de(SB), LEY, ejecutar=ejecutar, claude="claude")
    assert len(llamadas) == 2 and "RECHAZADO" in llamadas[1] and "atacantes tomaron" in llamadas[1]
    assert p["origen_plan"] == "respaldo" and p["motivo_respaldo"]


def test_sin_claude_el_respaldo_es_literal_y_cabe():
    fr = PL.frases_de(SB)
    p = PL.planificar(fr, LEY, claude="")
    assert p["origen_plan"] == "respaldo"
    assert PL.validar({k: v for k, v in p.items() if k != "frases"}, fr, LEY) == []


def test_textos_que_no_caben_se_detectan_antes_de_la_voz():
    largo = json.loads(json.dumps(BUENO))
    largo["escenas"][1]["titulo"] = "Un archivo comprimido manipulado podía escribir fuera de la memoria"   # no cabe
    assert any("no cabe" in e for e in PL.validar(largo, PL.frases_de(SB), LEY))


def test_ajustar_parte_en_dos_lineas_o_dice_que_no():
    assert RD.ajustar("archivo comprimido manipulado", 300)[1] == ["archivo", "comprimido"] or \
        len(RD.ajustar("archivo comprimido manipulado", 300)[1]) == 2
    assert RD.ajustar("palabra " * 30, 300) is None


def test_leyenda_con_fuente():
    assert P.leyenda_de({"source_url": "https://www.facebook.com/share/r/1MbDmfb8Ch/"}).endswith("REEL FB 1MbDmfb8Ch")
    assert P.leyenda_de(None) == "ESQUEMA ILUSTRATIVO"


# ── conexión con el bot ─────────────────────────────────────────────────────
@pytest.fixture
def h(tmp_path, monkeypatch):
    import telegram_bot.handlers as handlers
    monkeypatch.setattr(candado, "LOCK", tmp_path / "_fabrica.lock")
    monkeypatch.setattr(handlers, "_gate_fidelidad", lambda *a, **k: None)
    monkeypatch.setattr(handlers, "run_pipeline", lambda **k: pytest.fail("los enlaces ya no usan tomas de banco"))
    return handlers


def _cfg(tmp_path):
    return SimpleNamespace(temp_dir=str(tmp_path))


def test_enlace_produce_con_diagramas_bajo_el_candado(h, tmp_path, monkeypatch):
    visto = []
    monkeypatch.setattr(P, "producir", lambda sb, fu, carpeta, registro=None, **k: visto.append(candado.quien())
                        or Path(carpeta) / "x_9x16.mp4")
    r = h._run_pipeline_in_thread(storyboard=SB, provider=None, adapter=None, config=_cfg(tmp_path), fuente={},
                                  tratamiento="diagramas")
    assert r.status == "TECHNICAL_PASS" and r.result.video_path.endswith("x_9x16.mp4")
    assert visto and visto[0].startswith("bot-documental")


def test_si_el_motor_falla_avisa_y_no_cae_a_tomas_de_banco(h, tmp_path, monkeypatch):
    def falla(*a, **k):
        raise P.FallaDiagrama("la voz no quedó: FALLA frase 3")
    monkeypatch.setattr(P, "producir", falla)
    r = h._run_pipeline_in_thread(storyboard=SB, provider=None, adapter=None, config=_cfg(tmp_path), fuente={},
                                  tratamiento="diagramas")
    assert r.status == "FALLA_DIAGRAMA" and "frase 3" in r.result.error["message"] and r.result.video_path is None


def test_otras_rutas_siguen_con_el_pipeline_de_siempre(h, tmp_path, monkeypatch):
    monkeypatch.setattr(h, "run_pipeline", lambda **k: "PIPELINE")
    assert h._run_pipeline_in_thread(storyboard=SB, provider=None, adapter=None, config=_cfg(tmp_path)) == "PIPELINE"
