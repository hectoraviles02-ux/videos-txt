# -*- coding: utf-8 -*-
"""Guion de URL por Claude con respaldo local (22-oct-2026). Sin red ni Claude real."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from synthesis import guion_claude as GC  # noqa: E402
from synthesis.script_generator import ScriptGenerator  # noqa: E402

TEXTO = " ".join(["En 1998 se borraron los archivos de la película y la copia de seguridad no servía."] * 40)
CONTENIDO = {"titulo": "Prueba", "tipo": "video", "duracion_seg": 180,
             "segmentos": [{"inicio_seg": 0.0, "fin_seg": 180.0, "texto": TEXTO, "confianza": 0.9}]}
LOCAL = {"title": "LOCAL", "scenes": [{"narration": "local", "visual_description": "", "search_keywords": ""}]}


def _gen(monkeypatch, salida=None, codigo=0, claude="claude", excepcion=None):
    llamadas = []

    def ejecutar(cmd, **kw):
        llamadas.append((cmd, kw.get("input", "")))
        if excepcion:
            raise excepcion
        return SimpleNamespace(returncode=codigo, stdout=salida or "", stderr="")
    monkeypatch.setattr(ScriptGenerator, "generate", lambda self, c: LOCAL)
    return GC.GuionClaude(ejecutar=ejecutar, claude=claude), llamadas


def _guion(n):
    return json.dumps({"title": "T", "scenes": [{"narration": "En 1998 se borraron los archivos."}] * n})


def test_usa_claude_y_manda_la_fuente_con_las_reglas(monkeypatch):
    n = ScriptGenerator()._plan_por_fuente(CONTENIDO, len(TEXTO.split()))[0]
    g, llamadas = _gen(monkeypatch, salida="```json\n%s\n```" % _guion(n))
    d = g.generate(CONTENIDO)
    assert g.ultimo_motor == "claude" and d["title"] == "T" and len(d["scenes"]) == n
    assert d["scenes"][0]["search_keywords"] == ""                 # llaves que el pipeline espera
    assert "FIDELIDAD" in llamadas[0][1] and "1998" in llamadas[0][1]
    assert llamadas[0][0][1] == "-p"


def test_sin_claude_instalado_usa_local(monkeypatch):
    g, llamadas = _gen(monkeypatch, claude="")
    assert g.generate(CONTENIDO) is LOCAL and g.ultimo_motor == "local" and llamadas == []


def test_claude_falla_o_tarda_usa_local(monkeypatch):
    g, _ = _gen(monkeypatch, codigo=1)
    assert g.generate(CONTENIDO) is LOCAL
    import subprocess
    g2, _ = _gen(monkeypatch, excepcion=subprocess.TimeoutExpired("claude", 1))
    assert g2.generate(CONTENIDO) is LOCAL


def test_respuesta_que_no_es_guion_usa_local(monkeypatch):
    g, _ = _gen(monkeypatch, salida="No puedo ayudar con eso.")
    assert g.generate(CONTENIDO) is LOCAL
    g2, _ = _gen(monkeypatch, salida=_guion(1))                     # muy pocas escenas
    assert g2.generate(CONTENIDO) is LOCAL


def test_corregir_frases_usa_claude_y_sin_claude_las_quita():
    def ejecutar(cmd, **kw):
        assert "EVIDENCIA: Reconstruye tus imágenes." in kw["input"]
        return SimpleNamespace(returncode=0, stdout='{"1": "Reconstruye tus imágenes."}', stderr="")
    malas = [("Reconstruye tus imágenes antes de volver a ponerlas en marcha.", "Reconstruye tus imágenes.")]
    assert GC.corregir_frases(malas, ejecutar=ejecutar, claude="claude") == {malas[0][0]: "Reconstruye tus imágenes."}
    assert GC.corregir_frases(malas, claude="") == {malas[0][0]: ""}
