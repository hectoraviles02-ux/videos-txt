# -*- coding: utf-8 -*-
"""Fase A de la fabrica editorial (v5): aprobacion individual atada al SHA-256.

Todo con publicador SIMULADO: no se publica nada real. Lo que se demuestra:
- sin aprobacion vigente NO se llama al canal (contador = 0);
- si el MP4 cambia despues de aprobarlo, la aprobacion se INVALIDA sola;
- APROBADO, PROGRAMADO y PUBLICADO son estados distintos;
- un error incierto del canal bloquea el reintento hasta liberarlo con evidencia.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from content_factory import adaptador_fb, db, distribucion as dist
from tests.ayuda_aprobacion import aprobar_como_telegram

PROG = "2026-10-25T08:00:00-06:00"


@pytest.fixture
def conn(tmp_path):
    c = db.conectar(tmp_path / "cola.db")
    db.crear_esquema(c)
    yield c
    c.close()


def _pieza(conn, estado="PRODUCIDA"):
    tid = db.upsert_tema(conn, {
        "hash_titulo": "ht-%s" % estado, "titulo": "T", "pilar": "P4", "intent": "educativo",
        "creado_en": "2026-10-21", "estado": "SELECCIONADA", "metodo_clasificacion": "prueba",
        "evidencia": []})
    pid, _ = db.registrar_pieza(
        conn, tema_id=tid, formato="corto", angulo="t", angulo_metodo="prueba",
        pilar_decision="P4", estado=estado, creada_en="2026-10-21", detalle_nacimiento="prueba")
    return pid


@pytest.fixture
def mp4(tmp_path):
    p = tmp_path / "video.mp4"
    p.write_bytes(b"contenido original del video")
    return p


class _Canal:
    """Publicador simulado que cuenta llamadas y declara lo que sube."""

    def __init__(self, ruta, texto="texto aprobado", programada_para=None, resp=None, error=None):
        self.ruta_mp4, self.texto, self.programada_para = str(ruta), texto, programada_para
        self.resp, self.error, self.n = resp or {"video_id": "900"}, error, 0

    def __call__(self):
        self.n += 1
        if self.error is not None:
            raise self.error
        return self.resp


def _aprobar(conn, pid, mp4, **kw):
    # v6: la aprobacion solo nace del boton de Telegram (codigo de un solo uso)
    datos = dict(ruta_mp4=mp4, texto="texto aprobado", canales=["facebook", "youtube"])
    datos.update(kw)
    return aprobar_como_telegram(conn, pid, **datos)


def _publicar(conn, pid, canal_obj, tmp_path, canal="facebook", fecha="2026-10-21"):
    return dist.publicar_pieza(conn, pid, publicador=canal_obj, canal=canal,
                               ledger=dist.Ledger(tmp_path / "ledger.jsonl"), fecha=fecha)


# ── Sin aprobacion no se publica ──────────────────────────────────────────────

def test_sin_aprobacion_se_rechaza_y_no_llama_al_canal(conn, mp4, tmp_path):
    pid = _pieza(conn)
    canal = _Canal(mp4)
    with pytest.raises(dist.SinAprobacionValida, match="no tiene aprobacion vigente"):
        _publicar(conn, pid, canal, tmp_path)
    assert canal.n == 0
    assert conn.execute("SELECT COUNT(*) FROM publicaciones").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM eventos WHERE detalle LIKE 'publicacion RECHAZADA%'"
                        ).fetchone()[0] == 1


def test_publicador_que_no_declara_que_sube_se_rechaza(conn, mp4, tmp_path):
    pid = _pieza(conn)
    _aprobar(conn, pid, mp4)
    llamadas = []
    with pytest.raises(dist.SinAprobacionValida, match="no declara"):
        _publicar(conn, pid, lambda: llamadas.append(1) or {"video_id": "1"}, tmp_path)
    assert llamadas == []


def test_aprobar_exige_el_comprobante_de_telegram(conn, mp4):
    # v6: antes bastaban unas palabras; ahora sin comprobante del boton no hay aprobacion
    pid = _pieza(conn)
    with pytest.raises(db.AprobacionInvalida):
        db.aprobar_pieza(conn, pid, ruta_mp4=mp4, texto="t", canales=["facebook"],
                         otorgada_en="2026-10-21T10:00:00-06:00", comprobante={"palabras": "SI"})
    assert db.aprobacion_vigente(conn, pid) is None


def test_no_se_aprueba_una_pieza_sin_producir(conn, mp4):
    pid = _pieza(conn, estado="GUIONADA")
    with pytest.raises(db.AprobacionInvalida, match="GUIONADA"):
        _aprobar(conn, pid, mp4)


# ── Cambiar el MP4 invalida la aprobacion ─────────────────────────────────────

def test_mp4_modificado_invalida_la_aprobacion_y_no_publica(conn, mp4, tmp_path):
    pid = _pieza(conn)
    ap_id = _aprobar(conn, pid, mp4)
    mp4.write_bytes(b"contenido original del video + un cuadro distinto")   # cambia 1 cosa
    canal = _Canal(mp4)
    with pytest.raises(dist.SinAprobacionValida, match="cambio"):
        _publicar(conn, pid, canal, tmp_path)
    assert canal.n == 0
    fila = conn.execute("SELECT estado, motivo_invalidacion FROM aprobaciones WHERE id=?",
                        (ap_id,)).fetchone()
    assert fila["estado"] == "INVALIDADA"
    assert "aprobado sha256=" in fila["motivo_invalidacion"]
    assert db.aprobacion_vigente(conn, pid) is None
    # restaurar el contenido NO revive la aprobacion: hace falta aprobar otra vez
    mp4.write_bytes(b"contenido original del video")
    with pytest.raises(dist.SinAprobacionValida):
        _publicar(conn, pid, canal, tmp_path)
    assert canal.n == 0


def test_mp4_borrado_invalida_la_aprobacion(conn, mp4, tmp_path):
    pid = _pieza(conn)
    _aprobar(conn, pid, mp4)
    mp4.unlink()
    with pytest.raises(dist.SinAprobacionValida, match="no existe"):
        _publicar(conn, pid, _Canal(mp4), tmp_path)
    assert db.aprobacion_vigente(conn, pid) is None


def test_subir_otro_archivo_se_rechaza(conn, mp4, tmp_path):
    pid = _pieza(conn)
    _aprobar(conn, pid, mp4)
    otro = tmp_path / "otro.mp4"
    otro.write_bytes(b"otro video")
    canal = _Canal(otro)
    with pytest.raises(dist.SinAprobacionValida, match="no es el aprobado"):
        _publicar(conn, pid, canal, tmp_path)
    assert canal.n == 0


def test_una_copia_identica_del_aprobado_si_pasa(conn, mp4, tmp_path):
    pid = _pieza(conn)
    _aprobar(conn, pid, mp4)
    copia = tmp_path / "copia.mp4"
    copia.write_bytes(mp4.read_bytes())
    assert _publicar(conn, pid, _Canal(copia), tmp_path)["resultado"] == "publicada"


@pytest.mark.parametrize("cambio, motivo", [
    ({"texto": "texto cambiado"}, "texto"),
    ({"programada_para": "2026-10-26T08:00:00-06:00"}, "horario"),
])
def test_texto_u_horario_distintos_se_rechazan(conn, mp4, tmp_path, cambio, motivo):
    pid = _pieza(conn)
    _aprobar(conn, pid, mp4, programada_para=PROG)
    datos = {"texto": "texto aprobado", "programada_para": PROG}
    datos.update(cambio)
    canal = _Canal(mp4, **datos)
    with pytest.raises(dist.SinAprobacionValida, match=motivo):
        _publicar(conn, pid, canal, tmp_path)
    assert canal.n == 0
    assert db.aprobacion_vigente(conn, pid) is not None   # no se invalida: se rechaza el envio


def test_canal_no_aprobado_se_rechaza(conn, mp4, tmp_path):
    pid = _pieza(conn)
    _aprobar(conn, pid, mp4, canales=["facebook"])
    canal = _Canal(mp4)
    with pytest.raises(dist.SinAprobacionValida, match="tiktok"):
        _publicar(conn, pid, canal, tmp_path, canal="tiktok")
    assert canal.n == 0


def test_programada_para_sin_zona_horaria_se_rechaza(conn, mp4):
    pid = _pieza(conn)
    with pytest.raises(db.AprobacionInvalida, match="zona"):
        _aprobar(conn, pid, mp4, programada_para="2026-10-25T08:00:00")


def test_aprobar_otra_vez_revoca_la_anterior(conn, mp4):
    pid = _pieza(conn)
    a1 = _aprobar(conn, pid, mp4)
    a2 = _aprobar(conn, pid, mp4, texto="texto nuevo")
    estados = dict(conn.execute("SELECT id, estado FROM aprobaciones").fetchall())
    assert estados == {a1: "REVOCADA", a2: "VIGENTE"}
    with pytest.raises(sqlite3.IntegrityError):   # el indice impide dos VIGENTES
        conn.execute("UPDATE aprobaciones SET estado='VIGENTE' WHERE id=?", (a1,))


# ── APROBADO / PROGRAMADO / PUBLICADO separados ──────────────────────────────

def test_programar_deja_programada_no_publicada_y_no_duplica(conn, mp4, tmp_path):
    pid = _pieza(conn)
    _aprobar(conn, pid, mp4, programada_para=PROG)
    assert db.estado_editorial(conn, pid)["aprobado"] is True
    canal = _Canal(mp4, programada_para=PROG, resp={"video_id": "777", "url": "/reel/777/"})

    r = _publicar(conn, pid, canal, tmp_path)
    assert r["resultado"] == "programada"
    ed = db.estado_editorial(conn, pid)
    assert ed["estado_pieza"] == "LISTA"                      # NO PUBLICADA
    assert ed["canales"]["facebook"] == {
        "estado": "PROGRAMADO", "programada_para": PROG,
        "identificador_remoto": "777", "url": "/reel/777/"}

    assert _publicar(conn, pid, canal, tmp_path)["resultado"] == "ya_programada"
    assert canal.n == 1                                       # el canal se llamo UNA vez

    with pytest.raises(dist.PublicacionError, match="evidencia"):
        dist.confirmar_programada(conn, pid, canal="facebook", fecha="2026-10-25", evidencia="")
    dist.confirmar_programada(conn, pid, canal="facebook", fecha="2026-10-25T08:05:00-06:00",
                              evidencia="lectura API: published=true")
    ed = db.estado_editorial(conn, pid)
    assert ed["estado_pieza"] == "PUBLICADA"
    assert ed["canales"]["facebook"]["estado"] == "PUBLICADO"


def test_segundo_canal_despues_de_publicada(conn, mp4, tmp_path):
    pid = _pieza(conn)
    _aprobar(conn, pid, mp4)
    assert _publicar(conn, pid, _Canal(mp4), tmp_path)["resultado"] == "publicada"
    r = _publicar(conn, pid, _Canal(mp4, resp={"video_id": "yt1"}), tmp_path, canal="youtube")
    assert r["resultado"] == "publicada"
    assert set(db.estado_editorial(conn, pid)["canales"]) == {"facebook", "youtube"}


def test_programada_con_bd_caida_se_reconcilia_como_programada(conn, mp4, tmp_path, monkeypatch):
    pid = _pieza(conn)
    _aprobar(conn, pid, mp4, programada_para=PROG)
    canal = _Canal(mp4, programada_para=PROG, resp={"video_id": "555"})
    real = db.registrar_publicacion
    monkeypatch.setattr(db, "registrar_publicacion",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disco lleno")))
    assert _publicar(conn, pid, canal, tmp_path)["resultado"] == "publicado_pendiente_bd"
    monkeypatch.setattr(db, "registrar_publicacion", real)
    r = _publicar(conn, pid, canal, tmp_path)
    assert r["resultado"] == "programada_reconciliada"
    assert canal.n == 1
    fila = db.obtener_publicacion(conn, pid, "facebook")
    assert fila["estado"] == db.PUB_PROGRAMADA and fila["programada_para"] == PROG
    assert db.estado_editorial(conn, pid)["estado_pieza"] == "LISTA"


# ── Reintentos inseguros ─────────────────────────────────────────────────────

def test_error_incierto_bloquea_reintento_hasta_liberarlo_con_evidencia(conn, mp4, tmp_path):
    pid = _pieza(conn)
    _aprobar(conn, pid, mp4)
    canal = _Canal(mp4, error=TimeoutError("se corto la conexion al terminar la subida"))
    assert _publicar(conn, pid, canal, tmp_path)["resultado"] == "resultado_incierto"
    assert db.obtener_publicacion(conn, pid, "facebook")["estado"] == db.PUB_PENDIENTE

    canal.error = None
    assert _publicar(conn, pid, canal, tmp_path)["resultado"] == "intento_sin_confirmar"
    assert canal.n == 1                                       # NO se reintento a ciegas

    with pytest.raises(dist.PublicacionError, match="evidencia"):
        dist.liberar_intento(conn, pid, canal="facebook", fecha="2026-10-21", evidencia=" ")
    dist.liberar_intento(conn, pid, canal="facebook", fecha="2026-10-21",
                         evidencia="revise la pagina de FB: el video no existe")
    assert _publicar(conn, pid, canal, tmp_path)["resultado"] == "publicada"
    assert canal.n == 2


def test_sin_video_id_tambien_es_incierto(conn, mp4, tmp_path):
    pid = _pieza(conn)
    _aprobar(conn, pid, mp4)
    canal = _Canal(mp4, resp={"status": "ok"})
    assert _publicar(conn, pid, canal, tmp_path)["resultado"] == "resultado_incierto"
    assert _publicar(conn, pid, canal, tmp_path)["resultado"] == "intento_sin_confirmar"
    assert canal.n == 1


# ── Adaptador real de FB (cliente simulado) y migracion ──────────────────────

def test_adaptador_declara_lo_que_sube_y_pasa_la_programacion(conn, mp4, tmp_path):
    pid = _pieza(conn)
    _aprobar(conn, pid, mp4, programada_para=PROG)
    visto = {}

    def publicar_uno(page, tok, item, **kw):
        visto.update(kw, file=item["file"], caption=item["caption"])
        return ("4242", {})

    pub = adaptador_fb.construir_publicador(
        mp4_path=str(mp4), caption="texto aprobado", page_id="P", token="T",
        publicar_uno=publicar_uno, programada_para=PROG, ia=True)
    assert (pub.ruta_mp4, pub.texto, pub.programada_para) == (str(mp4), "texto aprobado", PROG)
    assert _publicar(conn, pid, pub, tmp_path)["resultado"] == "programada"
    assert visto["ia"] is True
    assert visto["programar_unix"] == datetime(2026, 10, 25, 14, 0, tzinfo=timezone.utc).timestamp()
    assert visto["file"] == str(mp4) and visto["caption"] == "texto aprobado"


def test_migracion_v4_a_v5_preserva_publicaciones_y_no_aprueba_nada(tmp_path):
    conn = db.conectar(tmp_path / "v4.db")
    db.crear_esquema(conn)
    pid = _pieza(conn, estado="PUBLICADA")
    db.registrar_publicacion(conn, pieza_id=pid, canal="facebook", publicada_en="2026-10-05",
                             identificador_remoto="123", url="/reel/123/")
    # degradar a v4: sin `aprobaciones` ni `programada_para`
    conn.execute("DROP TABLE aprobaciones")
    conn.execute("ALTER TABLE publicaciones DROP COLUMN programada_para")
    conn.execute("DELETE FROM esquema_version")
    conn.execute("INSERT INTO esquema_version (version, aplicada_en) VALUES (4, datetime('now'))")
    conn.commit()

    db.crear_esquema(conn)
    fila = db.obtener_publicacion(conn, pid, "facebook")
    assert (fila["identificador_remoto"], fila["url"], fila["estado"]) == ("123", "/reel/123/", "CONFIRMADA")
    assert fila["programada_para"] is None                    # desconocido, no inventado
    assert conn.execute("SELECT COUNT(*) FROM aprobaciones").fetchone()[0] == 0
    assert conn.execute("SELECT MAX(version) FROM esquema_version").fetchone()[0] == 6
    conn.close()
