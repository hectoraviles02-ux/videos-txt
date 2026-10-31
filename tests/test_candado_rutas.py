# -*- coding: utf-8 -*-
"""Rutas de producción bajo el candado de la fábrica (19-oct-2026): documental del bot, documental EDL y el render de la
publicación diaria (la subida queda fuera). Con la fábrica ocupada ninguna inicia producción; el candado se libera al
terminar y al fallar. No renderiza ni publica nada: las etapas pesadas se sustituyen por espías."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import psutil
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
from telegram_bot import candado  # noqa: E402


@pytest.fixture
def lock(tmp_path, monkeypatch):
    ruta = tmp_path / "_fabrica.lock"
    monkeypatch.setattr(candado, "LOCK", ruta)
    return ruta


def ocupar(ruta):
    """Otro productor VIVO tiene la fábrica (este mismo proceso de pruebas sirve: existe y su inicio coincide)."""
    ruta.write_text(json.dumps({"quien": "otro-productor", "pid": os.getpid(), "desde": time.time(),
                                "inicio_proceso": round(psutil.Process(os.getpid()).create_time(), 3)}), encoding="utf-8")


def cargar(nombre, ruta):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------- documental del bot (handlers._run_pipeline_in_thread) ----------
@pytest.fixture(scope="module")
def handlers():
    import telegram_bot.handlers as h
    return h


def _llamar_bot(h):
    return h._run_pipeline_in_thread(storyboard=None, provider=None, adapter=None, config=None)


def test_bot_ocupado_no_produce(handlers, lock, monkeypatch):
    llamado = []
    monkeypatch.setattr(handlers, "_gate_fidelidad", lambda *a, **k: llamado.append("gate"))
    monkeypatch.setattr(handlers, "run_pipeline", lambda **k: llamado.append("pipeline"))
    ocupar(lock)
    r = _llamar_bot(handlers)
    assert r.status == "OCUPADO" and "ocupada" in r.result.error["message"]
    assert llamado == []                       # ni gate (modelo local) ni pipeline (GPU)
    assert json.loads(lock.read_text(encoding="utf-8"))["quien"] == "otro-productor"


def test_bot_libre_produce_y_libera(handlers, lock, monkeypatch):
    llamado = []
    monkeypatch.setattr(handlers, "_gate_fidelidad", lambda *a, **k: None)
    monkeypatch.setattr(handlers, "run_pipeline", lambda **k: llamado.append(candado.quien()) or "OK")
    assert _llamar_bot(handlers) == "OK"
    assert llamado and llamado[0].startswith("bot-documental")   # el pipeline corrió CON el candado tomado
    assert not lock.exists()                                       # y se liberó al terminar


def test_bot_libera_si_falla(handlers, lock, monkeypatch):
    monkeypatch.setattr(handlers, "_gate_fidelidad", lambda *a, **k: None)
    def revienta(**k):
        raise RuntimeError("falla simulada del pipeline")
    monkeypatch.setattr(handlers, "run_pipeline", revienta)
    with pytest.raises(RuntimeError):
        _llamar_bot(handlers)
    assert not lock.exists()


# ---------- documental EDL (scripts/producir_documental_edl.py main) ----------
@pytest.fixture
def edl(monkeypatch):
    m = cargar("producir_documental_edl_prueba", RAIZ / "scripts" / "producir_documental_edl.py")
    monkeypatch.setattr(sys, "argv", ["producir_documental_edl.py", "edl_x", "--out", "x.mp4"])
    return m


def test_edl_ocupado_no_renderiza(edl, lock, monkeypatch):
    llamado = []
    monkeypatch.setattr(edl, "producir", lambda *a, **k: llamado.append(1) or 0)
    ocupar(lock)
    assert edl.main() == 8
    assert llamado == []


def test_edl_libre_renderiza_y_libera(edl, lock, monkeypatch):
    monkeypatch.setattr(edl, "producir", lambda *a, **k: 0 if candado.quien().startswith("documental-edl") else 99)
    assert edl.main() == 0
    assert not lock.exists()


def test_edl_libera_si_falla(edl, lock, monkeypatch):
    def revienta(*a, **k):
        raise RuntimeError("falla simulada de Remotion")
    monkeypatch.setattr(edl, "producir", revienta)
    with pytest.raises(RuntimeError):
        edl.main()
    assert not lock.exists()


# ---------- publicación diaria: render bajo candado, subida fuera ----------
@pytest.fixture
def pub(monkeypatch):
    m = cargar("publicar_diario_prueba", RAIZ / "docs" / "operations" / "evidencia" / "poc_terminal_shorts_20261005" / "publicar_diario.py")
    pasos = []
    monkeypatch.setattr(m, "log", lambda msg: pasos.append(("log", msg)))
    monkeypatch.setattr(m, "corre", lambda desc, args, cwd: pasos.append(("corre", desc, candado.quien())) or (0, ""))
    monkeypatch.setattr(m, "yt_pendientes", lambda: ["e1"])
    m._pasos = pasos
    return m


def test_publicacion_ocupada_no_renderiza_pero_sube(pub, lock):
    ocupar(lock)
    pub.main()
    descs = [p[1] for p in pub._pasos if p[0] == "corre"]
    assert not any(d.startswith("RENDER") for d in descs)                       # no inicia producción
    assert any(d.startswith("PUBLICAR FACEBOOK") for d in descs)                # la subida sigue (sin GPU)
    assert any(d.startswith("PUBLICAR YOUTUBE") for d in descs)


def test_publicacion_libre_renderiza_con_candado_y_sube_sin_el(pub, lock):
    pub.main()
    corridas = [p for p in pub._pasos if p[0] == "corre"]
    render = [p for p in corridas if p[1].startswith("RENDER")]
    subida = [p for p in corridas if p[1].startswith("PUBLICAR")]
    assert len(render) == 2 and all(p[2].startswith("publicar_diario-render") for p in render)
    assert subida and all(p[2] == "nadie" for p in subida)                       # candado ya liberado al subir
    assert not lock.exists()


def test_publicacion_libera_si_falla_el_render(pub, lock, monkeypatch):
    def revienta(desc, args, cwd):
        raise RuntimeError("falla simulada del render")
    monkeypatch.setattr(pub, "corre", revienta)
    with pytest.raises(RuntimeError):
        pub.main()
    assert not lock.exists()
