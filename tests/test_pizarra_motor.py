# -*- coding: utf-8 -*-
"""Motor pizarra (scripts/pizarra): compuertas, cobertura, revisiones con falla cerrada, selector y un cuadro de humo.
Sin GPU ni voz: la pieza de prueba se fabrica aquí (tiempos sintéticos + voz en silencio)."""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

RAIZ = Path(__file__).resolve().parents[1]
PIZ = RAIZ / "scripts" / "pizarra"
sys.path.insert(0, str(PIZ))
sys.path.insert(0, str(RAIZ / "scripts"))
import componentes as C  # noqa: E402
import motor  # noqa: E402
import producir_pizarra as P  # noqa: E402
import revisiones  # noqa: E402
import producir_pieza  # noqa: E402

GUION = {
    "motor": "pizarra", "id": "prueba_contrato", "titulo": "¿PRUEBA?", "leyenda": "EJEMPLO ILUSTRATIVO · prueba",
    "voz": {"motor": "chatterbox", "referencia": "x.wav", "pausa_s": 0.4, "semilla": 7},
    "tramos": [{"id": "t1", "texto": "Uno más uno da dos."}, {"id": "t2", "texto": "Y dos más dos da cuatro."}],
    "verificaciones": [{"desc": "1+1", "expr": "1+1", "esperado": 2}, {"desc": "2+2", "expr": "2+2", "esperado": 4}],
    "pistas": {"x": [["t1/uno", 0], ["t2/cuatro", 4]]},
    "contadores": {"desde": "t1", "filas": [{"texto": "x = {x:4.1f}", "color": "cian"}]},
    "escena": {"tipo": "precio", "desde": "t1", "texto": "{x:.0f}"},
    "centro": {"tipo": "barras", "max": 4, "formato": "{valor:.0f}", "barras": [{"etiqueta": "A", "valor": 2, "color": "cian", "en": "t1/dos"}]},
    "pulsos": [{"objetivo": "barra:0", "en": "t2/cuatro"}],
}


@pytest.fixture
def pieza(tmp_path):
    (tmp_path / "guion.json").write_text(json.dumps(GUION, ensure_ascii=False), encoding="utf-8")
    pal1 = [{"w": w, "ini": 0.5 + 0.3 * i, "fin": 0.75 + 0.3 * i} for i, w in enumerate("Uno más uno da dos.".split())]
    pal2 = [{"w": w, "ini": 2.5 + 0.3 * i, "fin": 2.75 + 0.3 * i} for i, w in enumerate("Y dos más dos da cuatro.".split())]
    ti = {"duracion": 5.0, "tramos": [{"id": "t1", "texto": GUION["tramos"][0]["texto"], "ini": 0.5, "fin": 2.0, "palabras": pal1},
                                      {"id": "t2", "texto": GUION["tramos"][1]["texto"], "ini": 2.5, "fin": 4.4, "palabras": pal2}],
          "cobertura": ["t1: guiado, 5/5 palabras medidas", "t2: guiado, 6/6 palabras medidas"]}
    (tmp_path / "tiempos.json").write_text(json.dumps(ti, ensure_ascii=False), encoding="utf-8")
    sf.write(str(tmp_path / "voz.wav"), np.zeros(int(5.0 * 48000)), 48000)
    return tmp_path


def g(**cambios):
    x = copy.deepcopy(GUION)
    x.update(cambios)
    return x


# ---------- compuertas del guion ----------
def test_guion_valido_pasa():
    assert len(P.compuertas(GUION)) == 2


@pytest.mark.parametrize("danado", [
    g(tramos=[{"id": "t1", "texto": "Uno más 1 da 2."}]),                       # cifras en la voz
    g(verificaciones=[{"desc": "falsa", "expr": "1+1", "esperado": 3}]),           # aritmética falsa
    g(motor="documental"),                                                        # otro motor
    g(musica={"archivo": "no_existe.mp3"}),                                       # música inexistente
    {k: v for k, v in GUION.items() if k != "leyenda"},                           # sin leyenda de ilustrativo
])
def test_compuerta_rechaza(danado):
    with pytest.raises(SystemExit):
        P.compuertas(danado)


def test_texto_ilegible_y_color_inexistente():
    with pytest.raises(SystemExit):
        C.texto_img("x", 20, (255, 255, 255))
    with pytest.raises(SystemExit):
        C.col("morado")


# ---------- anclas y expresiones ----------
def test_anclas_y_expresiones(pieza):
    tema = motor.Tema(pieza)
    assert abs(tema.ancla("t1/dos") - 1.7) < 1e-6
    for mala in ("t1/hipopotamo", "t99", "t1/uno#9"):
        with pytest.raises(SystemExit):
            tema.ancla(mala)
    with pytest.raises(SystemExit):
        motor.Tema.eval("no_existe * 2", {})


def test_cuadro_de_humo(pieza):
    m = motor.Motor(pieza)
    im = m.cuadro(int(3.2 * C.FPS))
    assert im.size == (C.W, C.H)


# ---------- cobertura de voz ----------
def test_cobertura_valida(pieza):
    ok, det = P.revisar_cobertura(pieza, GUION)
    assert ok, det


@pytest.mark.parametrize("dano", [
    lambda ti: ti["tramos"][0].__setitem__("texto", "otro texto"),
    lambda ti: ti.__setitem__("cobertura", ["t1: libre+alineado, 2/5 palabras medidas", ti["cobertura"][1]]),
    lambda ti: ti.__setitem__("duracion", ti["duracion"] + 3),
])
def test_cobertura_rechaza(pieza, dano):
    ti = json.loads((pieza / "tiempos.json").read_text(encoding="utf-8"))
    dano(ti)
    (pieza / "tiempos.json").write_text(json.dumps(ti, ensure_ascii=False), encoding="utf-8")
    assert not P.revisar_cobertura(pieza, GUION)[0]


# ---------- revisiones: falla cerrada ----------
@pytest.mark.parametrize("fn", [revisiones.c_movimiento_pizarra, revisiones.c_parpadeo, revisiones.c_audio])
def test_revision_sin_video_no_pasa(tmp_path, fn):
    with pytest.raises((FileNotFoundError, ValueError)):
        fn(tmp_path / "no_existe.mp4")
    basura = tmp_path / "basura.mp4"
    basura.write_bytes(b"esto no es un video")
    with pytest.raises((FileNotFoundError, ValueError)):
        fn(basura)


# ---------- selector de motor ----------
def test_selector_resuelve_pizarra(pieza, monkeypatch, tmp_path):
    cfg = tmp_path / "motores.json"
    cfg.write_text(json.dumps({"motores": {"pizarra": {"activo": True, "comando": ["{python}", "scripts/pizarra/producir_pizarra.py", "{pieza}"]},
                                           "apagado": {"activo": False, "comando": ["x"]}}}), encoding="utf-8")
    monkeypatch.setattr(producir_pieza, "CONFIG", cfg)
    cmd = producir_pieza.resolver(pieza)
    assert cmd[1].endswith("producir_pizarra.py") and cmd[2] == str(pieza)
    with pytest.raises(SystemExit):
        producir_pieza.resolver(pieza, "apagado")      # desactivado: no se ejecuta
    with pytest.raises(SystemExit):
        producir_pieza.resolver(pieza, "inventado")    # no registrado


def test_config_real_valida():
    reg = json.loads((RAIZ / "config" / "motores_video.json").read_text(encoding="utf-8"))["motores"]
    assert reg["pizarra"]["activo"] is True
    for nombre, m in reg.items():
        if m.get("activo"):
            assert (RAIZ / m["comando"][1]).is_file(), nombre


# ---------- el motor no contiene nada de un tema ----------
def test_motor_sin_palabras_de_temas():
    claves = set()
    for gj in list((RAIZ / "guiones" / "pizarra").glob("*/guion.json")):
        gg = json.loads(gj.read_text(encoding="utf-8"))
        claves |= {gg["id"], gj.parent.name.split("_", 1)[-1]}
        claves |= set(re.findall(r"[a-záéíóúñ]{6,}", gg["titulo"].lower()))
    codigo = "\n".join(p.read_text(encoding="utf-8").lower() for p in PIZ.glob("*.py") if p.name != "cifras.py")
    assert not sorted(c for c in claves if c in codigo)
