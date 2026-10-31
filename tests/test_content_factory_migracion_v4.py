# -*- coding: utf-8 -*-
"""Migracion v3 -> v4 de `publicaciones` (distribucion trazable).

Cambio SDD: content-factory-produccion-y-distribucion.
Demuestra: base nueva nace v4; y una base v3 real migra a v4 PRESERVANDO datos.
"""
from __future__ import annotations



from content_factory import db


def _cols(conn, tabla):
    return {r[1] for r in conn.execute("PRAGMA table_info(%s)" % tabla)}


def test_base_nueva_nace_v4_con_columnas_de_publicaciones(tmp_path):
    conn = db.conectar(tmp_path / "nueva.db")
    db.crear_esquema(conn)
    assert db.ESQUEMA_VERSION == 6   # v6 (Etapa 1): la base nueva nace en la version vigente
    assert conn.execute("SELECT MAX(version) FROM esquema_version").fetchone()[0] == 6
    cols = _cols(conn, "publicaciones")
    assert {"identificador_remoto", "estado", "detalle_error"} <= cols
    assert {"pieza_id", "canal", "url", "publicada_en"} <= cols  # se conservan las de v3
    conn.close()


def test_migracion_real_v3_a_v4_preserva_datos(tmp_path):
    ruta = tmp_path / "v3.db"
    conn = db.conectar(ruta)
    db.crear_esquema(conn)   # v4
    # una pieza real para satisfacer la FK de publicaciones
    tid = db.upsert_tema(conn, {
        "hash_titulo": "ht", "titulo": "T", "pilar": "P4", "intent": "educativo",
        "creado_en": "2026-01-01", "estado": "SELECCIONADA", "metodo_clasificacion": "legado",
        "evidencia": []})
    pid, _ = db.registrar_pieza(
        conn, tema_id=tid, formato="corto", angulo="t", angulo_metodo="legado",
        pilar_decision="P4", estado="PRODUCIDA", creada_en="2026-01-01",
        detalle_nacimiento="x")

    # DEGRADAR `publicaciones` a su forma v3 (solo las 4 columnas viejas) con un dato
    conn.execute("DROP TABLE publicaciones")
    conn.execute(
        "CREATE TABLE publicaciones ("
        " pieza_id INTEGER NOT NULL REFERENCES piezas(id) ON DELETE CASCADE,"
        " canal TEXT NOT NULL, url TEXT, publicada_en TEXT,"
        " PRIMARY KEY (pieza_id, canal))")
    conn.execute(
        "INSERT INTO publicaciones (pieza_id, canal, url, publicada_en) VALUES (?,?,?,?)",
        (pid, "facebook", "/reel/legado/", "2026-01-01"))
    conn.execute("DELETE FROM esquema_version")
    conn.execute("INSERT INTO esquema_version (version, aplicada_en) VALUES (3, datetime('now'))")
    conn.commit()
    # confirmar que ES v3 (sin las columnas nuevas)
    assert "identificador_remoto" not in _cols(conn, "publicaciones")
    assert conn.execute("SELECT MAX(version) FROM esquema_version").fetchone()[0] == 3

    # MIGRAR (re-ejecutar crear_esquema corre el ALTER real)
    db.crear_esquema(conn)

    cols = _cols(conn, "publicaciones")
    assert {"identificador_remoto", "estado", "detalle_error"} <= cols   # columnas nuevas
    # DATO PRESERVADO
    fila = conn.execute("SELECT * FROM publicaciones WHERE pieza_id=?", (pid,)).fetchone()
    assert fila["url"] == "/reel/legado/"
    assert fila["publicada_en"] == "2026-01-01"
    assert fila["identificador_remoto"] is None          # desconocido = honesto, no inventado
    assert fila["estado"] == "CONFIRMADA"                 # default de la migracion
    # version registrada ahora incluye la 4
    assert conn.execute("SELECT MAX(version) FROM esquema_version").fetchone()[0] == 6   # v3 migra hasta la vigente (v6)
    conn.close()


def test_migracion_idempotente(tmp_path):
    conn = db.conectar(tmp_path / "idem.db")
    db.crear_esquema(conn)
    antes = _cols(conn, "publicaciones")
    db.crear_esquema(conn)   # segunda vez: no debe romper ni duplicar columnas
    assert _cols(conn, "publicaciones") == antes
    assert conn.execute("SELECT MAX(version) FROM esquema_version").fetchone()[0] == 6
    conn.close()
