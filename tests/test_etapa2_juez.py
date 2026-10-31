# -*- coding: utf-8 -*-
"""Etapa 2: el juez combina revision matematica (sin modelo) y editorial (modelo local).

Sin Ollama: el modelo se sustituye por un doble que cuenta sus llamadas.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import verificar_narracion_reel as v  # noqa: E402

CODE = "potencia = [500, 750, 1000]\nvoltaje = 127\nampere = [p / voltaje for p in potencia]\nprint(ampere)"  # e92 real
OUT = "[3.937007874015748, 5.905511811023622, 7.874015748031496]"


@pytest.fixture
def modelo(monkeypatch):
    llamadas = []

    def falso(code, output, narracion, temperatura):
        llamadas.append(temperatura)
        return {"veredicto": "OK", "motivo": "el modelo dice que todo cuadra"}
    monkeypatch.setattr(v, "_juez", falso)
    return llamadas


def test_divergencia_numerica_es_falla_aunque_el_modelo_diga_ok(modelo):
    r = v.verificar(CODE, OUT, "Salen unos cuatro, seis y doce amperes.")
    assert r["veredicto"] == "FALLA"
    assert r["motivo"].startswith("numeros:")
    assert modelo == []  # ni se pregunta al modelo


def test_narracion_correcta_pasa_a_revision_y_conserva_llaves(modelo):
    r = v.verificar(CODE, OUT, "Con quinientos, setecientos cincuenta y mil watts salen unos cuatro, seis y ocho.")
    assert r["veredicto"] == "OK" and "motivo" in r          # ciclo_diario lee estas dos llaves
    assert r["matematica"]["veredicto"] == "OK"
    assert r["editorial"]["veredicto"] == "OK" and r["editorial"]["pasadas"] == 2
    assert modelo == [0.0, 0.4]


def test_aviso_numerico_viaja_en_el_motivo(modelo):
    r = v.verificar(CODE, OUT, "Salen unos cuatro, seis y ocho amperes: por debajo de un fusible de quince.")
    assert r["matematica"]["veredicto"] == "AVISO"
    assert "AVISO numeros" in r["motivo"]
    # Revision de el operador (21-oct): un AVISO nunca se presenta como OK, aunque el modelo diga OK.
    assert r["veredicto"] == "REVISAR"
    assert r["matematica_completa"] is False


def test_solo_sin_hallazgos_hay_validacion_matematica_completa(modelo):
    r = v.verificar(CODE, OUT, "Salen unos cuatro, seis y ocho amperes.")
    assert r["veredicto"] == "OK" and r["matematica_completa"] is True


def test_ok_del_juez_declara_que_no_autoriza_publicar(modelo):
    r = v.verificar(CODE, OUT, "Este código divide la potencia entre el voltaje.")
    assert "no aprueba ni autoriza" in r["alcance"]


def test_juez_caido_es_falla_y_se_distingue(monkeypatch):
    def caido(*_a, **_k):
        raise ConnectionError("sin ollama")
    import requests
    monkeypatch.setattr(requests, "post", caido)
    r = v.juez_llm(CODE, OUT, "Este código divide la potencia entre el voltaje.")
    assert r["veredicto"] == "FALLA" and r["juez_caido"] is True


def test_ok_del_juez_no_crea_aprobaciones_ni_publica(tmp_path, modelo):
    """Regresion de la compuerta (Etapa 1): un OK del juez no deja nada aprobado en la cola."""
    from content_factory import db, distribucion as dist
    conn = db.conectar(tmp_path / "c.db")
    db.crear_esquema(conn)
    tid = db.upsert_tema(conn, {"hash_titulo": "h", "titulo": "T", "pilar": "P4", "intent": "educativo",
                                "creado_en": "2026-10-21", "estado": "SELECCIONADA",
                                "metodo_clasificacion": "legado", "evidencia": []})
    pid, _ = db.registrar_pieza(conn, tema_id=tid, formato="corto", angulo="t", angulo_metodo="legado",
                                pilar_decision="P4", estado="PRODUCIDA", creada_en="2026-10-21",
                                detalle_nacimiento="x")
    mp4 = tmp_path / "r.mp4"
    mp4.write_bytes(b"mp4")
    assert v.verificar(CODE, OUT, "Salen unos cuatro, seis y ocho amperes.")["veredicto"] == "OK"
    llamadas = []

    def publicador():
        llamadas.append(1)
        return {"video_id": "1", "url": "/reel/1/"}
    publicador.ruta_mp4, publicador.texto, publicador.programada_para = str(mp4), "t", None
    with pytest.raises(dist.SinAprobacionValida):
        dist.publicar_pieza(conn, pid, publicador=publicador, ledger=dist.Ledger(tmp_path / "l.jsonl"),
                            fecha="2026-10-21")
    assert llamadas == []
    assert conn.execute("SELECT COUNT(*) FROM aprobaciones").fetchone()[0] == 0
    conn.close()
