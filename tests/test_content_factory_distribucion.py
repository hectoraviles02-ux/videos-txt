# -*- coding: utf-8 -*-
"""E2 · Distribucion trazable con estados de publicacion y reconciliador.

Cambio SDD: content-factory-produccion-y-distribucion.
El publicador se SIMULA (mock): no se publica nada en Facebook real.

PROPIEDAD CENTRAL: un fallo local POSTERIOR al exito remoto NO produce una segunda
llamada al canal (anti-doble-publicacion remoto).
"""
from __future__ import annotations

import pytest

from content_factory import db, distribucion as dist
from tests.ayuda_aprobacion import aprobar_como_telegram


@pytest.fixture
def conn(tmp_path):
    c = db.conectar(tmp_path / "prueba.db")
    db.crear_esquema(c)
    yield c
    c.close()


def _pieza_producida(conn, fecha="2026-10-05"):
    tema_id = db.upsert_tema(conn, {
        "hash_titulo": "ht-demo", "titulo": "DEMO", "pilar": "P4",
        "intent": "educativo", "creado_en": fecha, "estado": "SELECCIONADA",
        "metodo_clasificacion": "legado", "evidencia": [{"legacy_id": "e12"}]})
    pieza_id, _ = db.registrar_pieza(
        conn, tema_id=tema_id, formato="corto", angulo="tutorial", angulo_metodo="legado",
        pilar_decision="P4", estado="PRODUCIDA", creada_en=fecha,
        detalle_nacimiento="stock legado; evidencia = ejecucion local")
    return pieza_id


def _pub(conn, pieza_id, tmp_path, fn, texto="caption aprobado"):
    """v5: aprueba la pieza (una vez) y devuelve un publicador que DECLARA archivo y texto.

    Antes de v5 estas pruebas publicaban sin aprobacion; ahora el distribuidor lo
    rechaza, asi que cada prueba aprueba primero, como se hara en real.
    """
    mp4 = tmp_path / ("pieza_%d.mp4" % pieza_id)
    if not mp4.is_file():
        mp4.write_bytes(b"mp4 simulado %d" % pieza_id)
    if db.aprobacion_vigente(conn, pieza_id) is None:
        aprobar_como_telegram(conn, pieza_id, ruta_mp4=mp4, texto=texto, canales=["facebook"])

    def publicador():
        return fn()

    publicador.ruta_mp4 = str(mp4)
    publicador.texto = texto
    publicador.programada_para = None
    return publicador


class _Contador:
    def __init__(self, resp):
        self.n = 0
        self.resp = resp

    def __call__(self):
        self.n += 1
        return self.resp


def test_publicacion_simulada_exitosa_guarda_identificador_remoto(conn, tmp_path):
    pieza_id = _pieza_producida(conn)
    ledger = dist.Ledger(tmp_path / "ledger.jsonl")
    res = dist.publicar_pieza(
        conn, pieza_id, publicador=_pub(conn, pieza_id, tmp_path, lambda: {"video_id": "123456"}),
        ledger=ledger, fecha="2026-10-05")
    assert res["resultado"] == "publicada"
    # exactamente 1 fila en publicaciones, CONFIRMADA, con id remoto
    fila = db.obtener_publicacion(conn, pieza_id, "facebook")
    assert fila is not None
    assert fila["estado"] == db.PUB_CONFIRMADA
    assert fila["identificador_remoto"] == "123456"
    assert fila["url"] == "/reel/123456/"
    assert conn.execute("SELECT COUNT(*) FROM publicaciones").fetchone()[0] == 1
    # pieza PUBLICADA con transicion LISTA->PUBLICADA
    assert conn.execute("SELECT estado FROM piezas WHERE id=?", (pieza_id,)).fetchone()["estado"] == "PUBLICADA"
    assert conn.execute(
        "SELECT COUNT(*) FROM eventos WHERE pieza_id=? AND de_estado='LISTA' AND a_estado='PUBLICADA'",
        (pieza_id,)).fetchone()[0] == 1


def test_url_ausente_no_impide_registrar_identificador_remoto(conn, tmp_path):
    pieza_id = _pieza_producida(conn)
    ledger = dist.Ledger(tmp_path / "ledger.jsonl")
    # el publicador NO devuelve url; distribucion deriva /reel/<id>/ pero aun sin ella
    # el id remoto debe guardarse
    res = dist.publicar_pieza(
        conn, pieza_id, publicador=_pub(conn, pieza_id, tmp_path, lambda: {"video_id": "321"}), ledger=ledger, fecha="2026-10-05")
    assert res["resultado"] == "publicada"
    fila = db.obtener_publicacion(conn, pieza_id, "facebook")
    assert fila["identificador_remoto"] == "321"


def test_error_api_se_registra_sin_transicion_de_pieza(conn, tmp_path):
    pieza_id = _pieza_producida(conn)
    ledger = dist.Ledger(tmp_path / "ledger.jsonl")

    def publicador_que_falla():
        # v5: un rechazo SEGURO del canal se declara con RechazoDelCanal (antes RuntimeError;
        # un error generico ahora cuenta como INCIERTO, ver test_aprobacion)
        raise dist.RechazoDelCanal("OAuthException 190: token expired")

    res = dist.publicar_pieza(conn, pieza_id, publicador=_pub(conn, pieza_id, tmp_path, publicador_que_falla),
                              ledger=ledger, fecha="2026-10-05")
    assert res["resultado"] == "error_api"
    # SIN transicion: sigue PRODUCIDA; SIN fila de publicacion
    assert conn.execute("SELECT estado FROM piezas WHERE id=?", (pieza_id,)).fetchone()["estado"] == "PRODUCIDA"
    assert conn.execute("SELECT COUNT(*) FROM publicaciones").fetchone()[0] == 0
    # el motivo quedo registrado en un evento
    assert conn.execute(
        "SELECT COUNT(*) FROM eventos WHERE detalle LIKE '%fallo al publicar%'").fetchone()[0] == 1


def test_fb_ok_falla_bd_queda_en_reconciliacion_y_reintento_sin_segunda_llamada(conn, tmp_path, monkeypatch):
    """Escenario critico: FB confirma, la BD falla -> reconciliable; el reintento
    reconcilia SIN volver a llamar al canal (anti-doble-publicacion remoto)."""
    pieza_id = _pieza_producida(conn)
    ledger = dist.Ledger(tmp_path / "ledger.jsonl")
    pub = _Contador({"video_id": "999"})

    # la API publica OK pero escribir la fila de publicacion falla
    monkeypatch.setattr(db, "registrar_publicacion",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disco lleno")))
    res = dist.publicar_pieza(conn, pieza_id, publicador=_pub(conn, pieza_id, tmp_path, pub), ledger=ledger, fecha="2026-10-05")
    assert res["resultado"] == "publicado_pendiente_bd"
    assert res["identificador_remoto"] == "999"
    # NO quedo PUBLICADA; el ledger tiene la evidencia remota durable
    assert conn.execute("SELECT estado FROM piezas WHERE id=?", (pieza_id,)).fetchone()["estado"] != "PUBLICADA"
    assert ledger.obtener(pieza_id, "facebook")["identificador_remoto"] == "999"

    # reintento (BD sana): reconcilia desde el ledger, SIN llamar al canal otra vez
    monkeypatch.undo()
    res2 = dist.publicar_pieza(conn, pieza_id, publicador=_pub(conn, pieza_id, tmp_path, pub), ledger=ledger, fecha="2026-10-06")
    assert res2["resultado"] == "reconciliada"
    assert pub.n == 1  # <-- el canal se llamo UNA sola vez: no hubo doble publicacion
    fila = db.obtener_publicacion(conn, pieza_id, "facebook")
    assert fila["estado"] == db.PUB_CONFIRMADA and fila["identificador_remoto"] == "999"
    assert conn.execute("SELECT estado FROM piezas WHERE id=?", (pieza_id,)).fetchone()["estado"] == "PUBLICADA"


def test_fila_pendiente_en_bd_se_reconcilia_sin_llamar_al_canal(conn, tmp_path, monkeypatch):
    """Si la fila quedo RECONCILIACION_PENDIENTE (transicion fallo), el reintento
    la completa sin llamar al canal."""
    pieza_id = _pieza_producida(conn)
    ledger = dist.Ledger(tmp_path / "ledger.jsonl")
    pub = _Contador({"video_id": "888"})

    # la fila se escribe, pero la transicion LISTA->PUBLICADA falla
    real_trans = db.transicionar

    def trans_que_falla_en_publicada(c, pid, de, a, en, det):
        if a == "PUBLICADA":
            raise RuntimeError("fallo al transicionar")
        return real_trans(c, pid, de, a, en, det)

    monkeypatch.setattr(db, "transicionar", trans_que_falla_en_publicada)
    r1 = dist.publicar_pieza(conn, pieza_id, publicador=_pub(conn, pieza_id, tmp_path, pub), ledger=ledger, fecha="2026-10-05")
    assert r1["resultado"] == "publicado_pendiente_bd"
    fila = db.obtener_publicacion(conn, pieza_id, "facebook")
    assert fila is not None and fila["estado"] == db.PUB_RECONCILIACION_PENDIENTE

    monkeypatch.undo()
    r2 = dist.publicar_pieza(conn, pieza_id, publicador=_pub(conn, pieza_id, tmp_path, pub), ledger=ledger, fecha="2026-10-06")
    assert r2["resultado"] == "reconciliada"
    assert pub.n == 1
    assert db.obtener_publicacion(conn, pieza_id, "facebook")["estado"] == db.PUB_CONFIRMADA


def test_reconciliacion_repetida_es_idempotente(conn, tmp_path):
    pieza_id = _pieza_producida(conn)
    ledger = dist.Ledger(tmp_path / "ledger.jsonl")
    dist.publicar_pieza(conn, pieza_id, publicador=_pub(conn, pieza_id, tmp_path, lambda: {"video_id": "111"}),
                        ledger=ledger, fecha="2026-10-05")
    # ya CONFIRMADA; reconciliar de nuevo no rompe ni duplica
    r = dist.reconciliar(conn, pieza_id, fecha="2026-10-07")
    assert r["resultado"] == "ya_reconciliada"
    r2 = dist.reconciliar(conn, pieza_id, fecha="2026-10-08")
    assert r2["resultado"] == "ya_reconciliada"
    assert conn.execute("SELECT COUNT(*) FROM publicaciones").fetchone()[0] == 1


def test_idempotente_no_republica(conn, tmp_path):
    pieza_id = _pieza_producida(conn)
    ledger = dist.Ledger(tmp_path / "ledger.jsonl")
    pub = _Contador({"video_id": "777"})
    r1 = dist.publicar_pieza(conn, pieza_id, publicador=_pub(conn, pieza_id, tmp_path, pub), ledger=ledger, fecha="2026-10-05")
    r2 = dist.publicar_pieza(conn, pieza_id, publicador=_pub(conn, pieza_id, tmp_path, pub), ledger=ledger, fecha="2026-10-05")
    assert r1["resultado"] == "publicada"
    assert r2["resultado"] == "ya_publicada"
    assert pub.n == 1
    assert conn.execute("SELECT COUNT(*) FROM publicaciones").fetchone()[0] == 1
