"""31-oct-2026: un borrador en la bandeja de TikTok NO es una publicación.

Antes, SEND_TO_USER_INBOX contaba como «publicado» y la fila quedaba CONFIRMADA.
Ahora queda EN_BANDEJA, no se vuelve a subir y solo pasa a CONFIRMADA con evidencia leída del canal.
"""
from __future__ import annotations

import pytest

from content_factory import db, distribucion as dist
from telegram_bot import aprobacion_botones as ab
from tests.ayuda_aprobacion import aprobar_como_telegram


@pytest.fixture
def conn(tmp_path):
    c = db.conectar(tmp_path / "prueba.db")
    db.crear_esquema(c)
    yield c
    c.close()


def _pieza(conn):
    tema_id = db.upsert_tema(conn, {
        "hash_titulo": "ht-bandeja", "titulo": "BANDEJA", "pilar": "P4", "intent": "educativo",
        "creado_en": "2026-10-31", "estado": "SELECCIONADA", "metodo_clasificacion": "legado",
        "evidencia": [{"legacy_id": "e999"}]})
    pieza_id, _ = db.registrar_pieza(
        conn, tema_id=tema_id, formato="corto", angulo="tutorial", angulo_metodo="legado",
        pilar_decision="P4", estado="PRODUCIDA", creada_en="2026-10-31", detalle_nacimiento="prueba bandeja")
    return pieza_id


class _Publicador:
    """Publicador simulado que, como publicador_tiktok, declara destino «bandeja»."""

    def __init__(self, conn, pieza_id, tmp_path):
        mp4 = tmp_path / "pieza.mp4"
        mp4.write_bytes(b"mp4 simulado")
        aprobar_como_telegram(conn, pieza_id, ruta_mp4=mp4, texto="texto aprobado", canales=["tiktok"])
        self.ruta_mp4, self.texto, self.programada_para, self.destino = str(mp4), "texto aprobado", None, "bandeja"
        self.llamadas = 0

    def __call__(self):
        self.llamadas += 1
        return {"video_id": "v_inbox_1", "url": "tiktok bandeja v_inbox_1"}


def _estado_pieza(conn, pieza_id):
    return conn.execute("SELECT estado FROM piezas WHERE id=?", (pieza_id,)).fetchone()["estado"]


def test_borrador_en_bandeja_no_queda_como_publicado(conn, tmp_path):
    pieza_id = _pieza(conn)
    pub = _Publicador(conn, pieza_id, tmp_path)
    res = dist.publicar_pieza(conn, pieza_id, publicador=pub, ledger=dist.Ledger(tmp_path / "l.jsonl"),
                              canal="tiktok", fecha="2026-10-31")
    assert res["resultado"] == "en_bandeja"
    fila = db.obtener_publicacion(conn, pieza_id, "tiktok")
    assert fila["estado"] == db.PUB_EN_BANDEJA
    assert fila["identificador_remoto"] == "v_inbox_1"
    assert _estado_pieza(conn, pieza_id) == "LISTA"      # NO PUBLICADA


def test_borrador_en_bandeja_no_se_vuelve_a_subir(conn, tmp_path):
    pieza_id = _pieza(conn)
    pub = _Publicador(conn, pieza_id, tmp_path)
    ledger = dist.Ledger(tmp_path / "l.jsonl")
    dist.publicar_pieza(conn, pieza_id, publicador=pub, ledger=ledger, canal="tiktok", fecha="2026-10-31")
    res = dist.publicar_pieza(conn, pieza_id, publicador=pub, ledger=ledger, canal="tiktok", fecha="2026-10-31")
    assert res["resultado"] == "ya_en_bandeja"
    assert pub.llamadas == 1


def test_bandeja_pasa_a_confirmada_solo_con_evidencia(conn, tmp_path):
    pieza_id = _pieza(conn)
    pub = _Publicador(conn, pieza_id, tmp_path)
    dist.publicar_pieza(conn, pieza_id, publicador=pub, ledger=dist.Ledger(tmp_path / "l.jsonl"),
                        canal="tiktok", fecha="2026-10-31")
    with pytest.raises(dist.PublicacionError):
        dist.confirmar_programada(conn, pieza_id, canal="tiktok", fecha="2026-11-01", evidencia="  ")
    r = dist.confirmar_programada(conn, pieza_id, canal="tiktok", fecha="2026-11-01",
                                  evidencia="visto público en el perfil", url="https://www.tiktok.com/@x/video/1")
    assert r["resultado"] == "confirmada"
    assert db.obtener_publicacion(conn, pieza_id, "tiktok")["estado"] == db.PUB_CONFIRMADA
    assert _estado_pieza(conn, pieza_id) == "PUBLICADA"


class _TTFalso:
    def __init__(self, estado):
        self.estado = estado

    def env(self):
        pass

    def _post(self, ruta, datos):
        return {"status": self.estado}


@pytest.mark.parametrize("estado,publico", [("SEND_TO_USER_INBOX", False), ("PUBLISH_COMPLETE", True),
                                            ("FAILED", False)])
def test_verificar_tiktok_no_confunde_bandeja_con_publico(monkeypatch, estado, publico):
    monkeypatch.setattr(ab, "_cargar_tt", lambda: _TTFalso(estado))
    ok, detalle = ab.verificar_en_plataforma("tiktok", "v_inbox_1")
    assert ok is publico
    if estado == "SEND_TO_USER_INBOX":
        assert "no es público" in detalle
