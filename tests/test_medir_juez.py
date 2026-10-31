# -*- coding: utf-8 -*-
"""Etapa 2: las respuestas de operador se leen aunque Excel haya guardado la plantilla a su modo."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import medir_juez as m  # noqa: E402

CLAVES = {"01": "B-e74", "02": "D-e92", "03": "B-e85"}


def test_csv_como_lo_guarda_excel_en_espanol(tmp_path):
    """Excel en es-MX: cp1252, separador ';', numero sin cero, encabezado tocado."""
    f = tmp_path / "r.csv"
    f.write_bytes("numero;titulo;respuesta;comentario\r\n1;HERENCIA;bien;\r\n"
                  "2;FÓRMULA AMPERES;MAL;el 15 no sale\r\n3;F-STRINGS;No sé;\r\n".encode("cp1252"))
    r = m.leer_respuestas(f, CLAVES)
    assert r["etiquetas"] == {"B-e74": "BIEN", "D-e92": "MAL"}
    assert r["no_se"] == 1 and r["no_reconocidas"] == []


def test_csv_original_utf8_con_comas(tmp_path):
    f = tmp_path / "r.csv"
    f.write_text("numero,titulo,tu_respuesta (BIEN / MAL / NO SE),comentario\n01,HERENCIA,MAL,\n02,X,,\n",
                 encoding="utf-8-sig")
    r = m.leer_respuestas(f, CLAVES)
    assert r["etiquetas"] == {"B-e74": "MAL"} and r["vacias"] == 1


def test_lo_que_no_se_entiende_se_reporta_no_se_pierde(tmp_path):
    f = tmp_path / "r.csv"
    f.write_text("numero,titulo,r,c\n01,A,quizas,\n99,B,MAL,\n", encoding="utf-8")
    r = m.leer_respuestas(f, CLAVES)
    assert r["etiquetas"] == {}
    assert r["no_reconocidas"] == ["01=QUIZAS", "99"]


def test_regla_desplegada_un_aviso_nunca_pasa_como_ok():
    assert m._desplegado("OK", "AVISO") == "REVISAR"
    assert m._desplegado("OK", "BLOQUEO") == "FALLA"
    assert m._desplegado("FALLA", "OK") == "FALLA"
    assert m._desplegado("OK", "OK") == "OK"
