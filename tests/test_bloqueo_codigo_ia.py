"""31-oct-2026: bloqueo temporal de la ejecución de código generado por IA (P0 abierto).

Sin el archivo logs/PERMITIR_CODIGO_IA no se le pide código a Ollama ni se ejecuta nada.
Todo simulado: sin Ollama, sin render, sin publicar.
"""
from __future__ import annotations

import subprocess

from telegram_bot import producir_reel as pr


def test_sin_permiso_no_llama_a_ollama_ni_ejecuta(monkeypatch, tmp_path):
    monkeypatch.setattr(pr, "PERMISO_CODIGO_IA", tmp_path / "no_existe")
    monkeypatch.setattr(pr, "_ollama", lambda t: (_ for _ in ()).throw(AssertionError("llamó a Ollama")))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ejecutó")))
    r = pr.producir("listas en python")
    assert "bloqueada" in r["error"]


def test_sin_permiso_ejecutar_directo_tambien_se_niega(monkeypatch, tmp_path):
    monkeypatch.setattr(pr, "PERMISO_CODIGO_IA", tmp_path / "no_existe")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ejecutó")))
    ok, msg = pr._ejecutar("print(1)")
    assert ok is False and "bloqueada" in msg


def test_con_permiso_vuelve_el_comportamiento_anterior(monkeypatch, tmp_path):
    permiso = tmp_path / "PERMITIR_CODIGO_IA"
    permiso.write_text("ok")
    monkeypatch.setattr(pr, "PERMISO_CODIGO_IA", permiso)
    ok, out = pr._ejecutar("print(2 + 3)")
    assert ok is True and out == "5"
