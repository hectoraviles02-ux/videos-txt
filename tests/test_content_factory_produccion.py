# -*- coding: utf-8 -*-
"""E1 · Importacion del stock legado como piezas trazables.

Cambio SDD: content-factory-produccion-y-distribucion.
Cubre las pruebas 1-4 del operador: importacion inicial, cero duplicados en la
segunda, estados derivados del artefacto, y reconstruccion SQL
legacy_id -> pieza -> tema -> estado -> evidencia. Ademas: evidencia por EVENTOS
(no por claims) e identidad estable con conflicto visible.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from content_factory import db, importar_stock as imp


@pytest.fixture
def conn(tmp_path):
    c = db.conectar(tmp_path / "prueba.db")
    db.crear_esquema(c)
    yield c
    c.close()


STOCK = [
    {"id": "e01", "titulo": "CONTAR VOCALES", "code": "print('aei')", "output": "3",
     "estado_ejec": "OK", "fuente_banco": "_ejercicios.json"},
    {"id": "e11", "titulo": "F-STRINGS", "code": "print('hola')", "output": "hola",
     "estado_ejec": "OK", "fuente_banco": "_banco.json"},
    {"id": "e12", "titulo": "INTERCAMBIAR", "code": "print(1)", "output": "1",
     "estado_ejec": "OK", "fuente_banco": "_banco.json"},
    {"id": "e99", "titulo": "SIN RENDER", "code": "print('x')", "output": "x",
     "estado_ejec": "OK", "fuente_banco": "_banco.json"},
]

# e11 publicado por API (comprobable), e01 subido "manual" (NO comprobable), e12 sin publicar.
PUBLICADOS = {
    "e01": {"video_id": "manual"},
    "e11": {"video_id": "1868869514546504"},
}


@pytest.fixture
def stub_artefactos(monkeypatch):
    """e01/e11/e12 tienen MP4; e99 no. Hash estable y controlado por id."""
    def fake_mp4(lid):
        return Path("%s_demo.mp4" % lid) if lid != "e99" else None

    def fake_hash(p):
        return "hash_%s" % Path(p).stem.split("_")[0]

    monkeypatch.setattr(imp, "_mp4_de", fake_mp4)
    monkeypatch.setattr(imp, "_hash_archivo", fake_hash)


def test_importacion_inicial_correcta(conn, stub_artefactos):
    res = imp.importar(conn, fecha="2026-10-05", stock=STOCK, publicados=PUBLICADOS)
    assert res["total"] == 4
    assert res["creados"] == 4
    assert res["saltados"] == 0
    # una pieza por item, un tema por item
    assert conn.execute("SELECT COUNT(*) FROM piezas").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM temas").fetchone()[0] == 4
    # todos pilar P4
    assert conn.execute("SELECT COUNT(*) FROM piezas WHERE pilar_decision='P4'").fetchone()[0] == 4


def test_segunda_importacion_cero_duplicados(conn, stub_artefactos):
    imp.importar(conn, fecha="2026-10-05", stock=STOCK, publicados=PUBLICADOS)
    res2 = imp.importar(conn, fecha="2026-10-06", stock=STOCK, publicados=PUBLICADOS)
    assert res2["creados"] == 0
    assert res2["saltados"] == 4
    assert conn.execute("SELECT COUNT(*) FROM piezas").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM temas").fetchone()[0] == 4


def test_estados_coinciden_con_artefactos(conn, stub_artefactos):
    imp.importar(conn, fecha="2026-10-05", stock=STOCK, publicados=PUBLICADOS)
    estados = dict(conn.execute(
        "SELECT t.titulo, p.estado FROM piezas p JOIN temas t ON t.id=p.tema_id").fetchall())
    # e11: MP4 + video_id API -> PUBLICADA
    assert estados["F-STRINGS"] == "PUBLICADA"
    # e01: MP4 pero 'manual' (NO comprobable) -> PRODUCIDA, no inflado
    assert estados["CONTAR VOCALES"] == "PRODUCIDA"
    # e12: MP4 sin publicar -> PRODUCIDA
    assert estados["INTERCAMBIAR"] == "PRODUCIDA"
    # e99: sin MP4, codigo ejecutado OK -> VERIFICADA
    assert estados["SIN RENDER"] == "VERIFICADA"
    # solo e11 deja fila de publicacion comprobable
    assert conn.execute("SELECT COUNT(*) FROM publicaciones").fetchone()[0] == 1
    pub = conn.execute("SELECT canal FROM publicaciones").fetchone()
    assert pub["canal"] == "facebook"


def test_evidencia_en_eventos_no_en_claims(conn, stub_artefactos):
    imp.importar(conn, fecha="2026-10-05", stock=STOCK, publicados=PUBLICADOS)
    # claims NO se usa para evidencia de ejecucion
    assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0
    # cada pieza tiene un evento de NACIMIENTO (de_estado NULL, a_estado = su estado)
    nac = conn.execute(
        "SELECT COUNT(*) FROM eventos WHERE pieza_id IS NOT NULL AND de_estado IS NULL "
        "AND a_estado IS NOT NULL").fetchone()[0]
    assert nac == 4
    # la nota de legado y la salida real quedan en el evento
    fila = conn.execute(
        "SELECT detalle FROM eventos WHERE a_estado='PUBLICADA' AND pieza_id IS NOT NULL").fetchone()
    assert "stock legado previo al pipeline completo" in fila["detalle"]
    assert "salida_real=" in fila["detalle"]


def test_conflicto_identidad_falla_visible(conn, monkeypatch):
    """Mismo legacy_id con artefacto distinto -> ConflictoImportacion (no fusiona)."""
    monkeypatch.setattr(imp, "_mp4_de", lambda lid: Path("%s_demo.mp4" % lid))
    monkeypatch.setattr(imp, "_hash_archivo", lambda p: "hash_A")
    imp.importar(conn, fecha="2026-10-05", stock=STOCK[:1], publicados={})
    # ahora el MISMO e01 "cambia" de artefacto
    monkeypatch.setattr(imp, "_hash_archivo", lambda p: "hash_B")
    with pytest.raises(imp.ConflictoImportacion) as err:
        imp.importar(conn, fecha="2026-10-06", stock=STOCK[:1], publicados={})
    assert "e01" in str(err.value)


def test_reconstruccion_sql_legacy_id_a_evidencia(conn, stub_artefactos):
    """Prueba 4: una consulta reconstruye legacy_id -> pieza -> tema -> estado -> evidencia."""
    imp.importar(conn, fecha="2026-10-05", stock=STOCK, publicados=PUBLICADOS)
    filas = conn.execute(
        """
        SELECT
          json_extract(t.evidencia_json, '$[0].legacy_id') AS legacy_id,
          p.id        AS pieza_id,
          t.id        AS tema_id,
          t.pilar     AS pilar,
          p.estado    AS estado,
          (SELECT e.detalle FROM eventos e
             WHERE e.pieza_id = p.id AND e.de_estado IS NULL AND e.a_estado IS NOT NULL
             ORDER BY e.id LIMIT 1) AS evidencia_nacimiento
        FROM piezas p
        JOIN temas t ON t.id = p.tema_id
        WHERE json_extract(t.evidencia_json, '$[0].origen') = 'legacy_python_bank'
        ORDER BY legacy_id
        """
    ).fetchall()
    recon = {f["legacy_id"]: f for f in filas}
    assert set(recon) == {"e01", "e11", "e12", "e99"}
    assert recon["e11"]["estado"] == "PUBLICADA"
    assert recon["e11"]["pilar"] == "P4"
    assert recon["e11"]["pieza_id"] is not None
    assert "stock legado" in recon["e11"]["evidencia_nacimiento"]
