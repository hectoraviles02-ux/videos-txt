# -*- coding: utf-8 -*-
"""Candado de la fábrica (telegram_bot/candado.py): pruebas con procesos reales a la vez.
Antes había carrera («revisar y luego escribir»): dos procesos entraban juntos (hallazgo de Codex, 19-oct-2026)."""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

RAIZ = Path(os.environ.get("CANDADO_RAIZ_PRUEBA", Path(__file__).resolve().parents[1]))  # permite probar la versión vieja
sys.path.insert(0, str(RAIZ))
from telegram_bot import candado  # noqa: E402

N = 6


def _trabajador(ruta, barrera, salida):
    sys.path.insert(0, str(RAIZ))
    from telegram_bot import candado as c
    c.LOCK = Path(ruta)
    barrera.wait()  # todos intentan en el mismo instante
    with c.tomar("prueba-%d" % os.getpid()) as ok:
        salida.put(ok)
        if ok:
            time.sleep(1.5)  # retiene el candado mientras los demás ya intentaron


def _competir(ruta):
    ctx = mp.get_context("spawn")
    barrera, salida = ctx.Barrier(N), ctx.Queue()
    ps = [ctx.Process(target=_trabajador, args=(str(ruta), barrera, salida)) for _ in range(N)]
    for p in ps:
        p.start()
    for p in ps:
        p.join(60)
    return [salida.get(timeout=5) for _ in range(N)]


def test_libre_solo_uno_entra(tmp_path):
    r = _competir(tmp_path / "_fabrica.lock")
    assert sum(r) == 1, r


def test_abandonado_por_proceso_muerto_solo_uno_lo_recupera(tmp_path):
    lock = tmp_path / "_fabrica.lock"
    lock.write_text(json.dumps({"quien": "muerto", "pid": 2 ** 22 + 7, "desde": time.time()}), encoding="utf-8")
    r = _competir(lock)
    assert sum(r) == 1, r


def test_viejo_con_dueno_vivo_NO_se_recupera(tmp_path, monkeypatch):
    """Un render largo (> 6 h) sigue siendo dueño: la edad sola no abandona el candado (hallazgo de Codex, 19-oct)."""
    import psutil
    lock = tmp_path / "_fabrica.lock"
    vivo = {"quien": "render-largo", "pid": os.getpid(), "desde": time.time() - 7 * 3600,
            "inicio_proceso": round(psutil.Process(os.getpid()).create_time(), 3)}
    lock.write_text(json.dumps(vivo), encoding="utf-8")
    monkeypatch.setattr(candado, "LOCK", lock)
    with candado.tomar("intruso") as ok:
        assert not ok
    assert json.loads(lock.read_text(encoding="utf-8")) == vivo


def test_numero_de_proceso_reusado_se_recupera(tmp_path, monkeypatch):
    """Mismo número de proceso pero otro programa (distinto inicio): el dueño original ya no existe."""
    lock = tmp_path / "_fabrica.lock"
    lock.write_text(json.dumps({"quien": "muerto", "pid": os.getpid(), "desde": time.time(), "inicio_proceso": 1.0}), encoding="utf-8")
    monkeypatch.setattr(candado, "LOCK", lock)
    with candado.tomar("nuevo") as ok:
        assert ok
        assert json.loads(lock.read_text(encoding="utf-8"))["quien"] == "nuevo"
    assert not lock.exists()  # el dueño lo libera al salir


def test_solo_el_dueno_libera(tmp_path, monkeypatch):
    lock = tmp_path / "_fabrica.lock"
    ajeno = {"quien": "otro", "pid": os.getppid() or 4, "desde": time.time()}  # proceso vivo distinto
    lock.write_text(json.dumps(ajeno), encoding="utf-8")
    monkeypatch.setattr(candado, "LOCK", lock)
    with candado.tomar("yo") as ok:
        assert not ok
    assert json.loads(lock.read_text(encoding="utf-8")) == ajeno  # sigue intacto


def test_candado_a_medio_escribir_cuenta_como_ocupado(tmp_path, monkeypatch):
    lock = tmp_path / "_fabrica.lock"
    lock.write_text("", encoding="utf-8")  # recién creado, aún sin contenido
    monkeypatch.setattr(candado, "LOCK", lock)
    with candado.tomar("yo") as ok:
        assert not ok
    assert lock.exists()


def test_sin_poder_verificar_al_dueno_queda_ocupado(tmp_path, monkeypatch):
    """Sin psutil no se puede saber si el dueño vive: falla cerrada, aunque el candado sea viejo (Codex, 19-oct)."""
    lock = tmp_path / "_fabrica.lock"
    lock.write_text(json.dumps({"quien": "desconocido", "pid": 4242, "desde": time.time() - 9 * 3600}), encoding="utf-8")
    monkeypatch.setattr(candado, "LOCK", lock)
    monkeypatch.setattr(candado, "_vivo", lambda d: None)  # simula que psutil no está disponible
    with candado.tomar("yo") as ok:
        assert not ok
    assert lock.exists()


def test_inicio_ilegible_con_proceso_vivo_queda_ocupado(tmp_path, monkeypatch):
    """El proceso existe pero su hora de inicio no se puede leer: estado desconocido → ocupado (Codex, 19-oct).
    Solo una hora LEÍDA y distinta significa que el dueño anterior desapareció."""
    lock = tmp_path / "_fabrica.lock"
    lock.write_text(json.dumps({"quien": "x", "pid": os.getpid(), "desde": time.time(), "inicio_proceso": 123.0}), encoding="utf-8")
    monkeypatch.setattr(candado, "LOCK", lock)
    monkeypatch.setattr(candado, "_inicio", lambda pid: None)  # simula que no se puede leer el inicio
    with candado.tomar("yo") as ok:
        assert not ok
    assert json.loads(lock.read_text(encoding="utf-8"))["quien"] == "x"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
