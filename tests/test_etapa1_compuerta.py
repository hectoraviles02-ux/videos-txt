# -*- coding: utf-8 -*-
"""Etapa 1 (21-oct-2026): ninguna ruta aprueba ni publica sin la aprobacion individual de el operador.

Todo con plataformas SIMULADAS: no hay red, no se publica nada y no se toca content.db real.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from content_factory import automatico as auto, db, distribucion as dist, guardia_publicacion as guardia
from tests.ayuda_aprobacion import CHAT, USUARIO, aprobar_como_telegram

RAIZ = Path(__file__).resolve().parent.parent
EN = "2026-10-21T10:00:00-06:00"


@pytest.fixture
def caso(tmp_path):
    ruta_bd = tmp_path / "cola.db"
    conn = db.conectar(ruta_bd)
    db.crear_esquema(conn)
    tid = db.upsert_tema(conn, {"hash_titulo": "t", "titulo": "T", "pilar": "P4", "intent": "educativo",
                                "creado_en": "2026-10-21", "estado": "SELECCIONADA",
                                "metodo_clasificacion": "prueba", "evidencia": []})
    pid, _ = db.registrar_pieza(conn, tema_id=tid, formato="corto", angulo="t", angulo_metodo="prueba",
                                pilar_decision="P4", estado="PRODUCIDA", creada_en="2026-10-21",
                                detalle_nacimiento="prueba")
    mp4 = tmp_path / "video.mp4"
    mp4.write_bytes(b"video original")
    yield conn, pid, mp4, ruta_bd
    conn.close()


def _comprobante(codigo, **cambios):
    c = {"origen": "telegram", "codigo": codigo, "user_id": USUARIO, "chat_id": CHAT, "message_id": "77"}
    c.update(cambios)
    return c


def _aprobar(conn, pid, mp4, comprobante, **kw):
    datos = dict(ruta_mp4=mp4, texto="texto", canales=["facebook"], otorgada_en=EN, comprobante=comprobante)
    datos.update(kw)
    return db.aprobar_pieza(conn, pid, **datos)


def _n_aprobaciones(conn):
    return conn.execute("SELECT COUNT(*) FROM aprobaciones").fetchone()[0]


# ── Una frase no aprueba ─────────────────────────────────────────────────────

@pytest.mark.parametrize("comprobante", [
    {"origen": "consola", "palabras": "go ya tenemos todo conectado"},
    {"palabras": "SI APRUEBO"},
    {},
    None,
])
def test_una_frase_generica_no_aprueba(caso, comprobante):
    conn, pid, mp4, _ = caso
    with pytest.raises(db.AprobacionInvalida):
        _aprobar(conn, pid, mp4, comprobante)
    assert _n_aprobaciones(conn) == 0


def test_la_firma_vieja_con_palabras_ya_no_existe(caso):
    conn, pid, mp4, _ = caso
    with pytest.raises(TypeError):
        db.aprobar_pieza(conn, pid, ruta_mp4=mp4, texto="t", canales=["facebook"],
                         palabras="go", otorgada_en=EN)
    assert _n_aprobaciones(conn) == 0


def test_codigo_inventado_no_aprueba(caso):
    conn, pid, mp4, _ = caso
    with pytest.raises(db.AprobacionInvalida, match="no existe"):
        _aprobar(conn, pid, mp4, _comprobante("0" * 32))
    assert _n_aprobaciones(conn) == 0


# ── El codigo de Telegram: un solo uso, una pieza, un usuario, una version ───

def test_codigo_valido_aprueba_y_queda_gastado(caso):
    conn, pid, mp4, _ = caso
    codigo = db.emitir_codigo(conn, pid, ruta_mp4=mp4, destinatario_user_id=USUARIO, emitido_en=EN)
    ap = _aprobar(conn, pid, mp4, _comprobante(codigo))
    fila = conn.execute("SELECT origen, comprobante_json, palabras FROM aprobaciones WHERE id=?", (ap,)).fetchone()
    recibo = json.loads(fila["comprobante_json"])
    assert fila["origen"] == "telegram"
    assert (recibo["user_id"], recibo["message_id"]) == (USUARIO, "77")
    assert codigo not in fila["comprobante_json"]                 # el codigo en claro no se guarda
    with pytest.raises(db.AprobacionInvalida, match="ya se uso"):
        _aprobar(conn, pid, mp4, _comprobante(codigo))
    assert _n_aprobaciones(conn) == 1


def test_codigo_de_otra_pieza_no_sirve(caso):
    conn, pid, mp4, _ = caso
    tid = conn.execute("SELECT tema_id FROM piezas WHERE id=?", (pid,)).fetchone()[0]
    otra, _ = db.registrar_pieza(conn, tema_id=tid, formato="largo", angulo="t", angulo_metodo="prueba",
                                 pilar_decision="P4", estado="PRODUCIDA", creada_en="2026-10-21",
                                 detalle_nacimiento="otra")
    codigo = db.emitir_codigo(conn, otra, ruta_mp4=mp4, destinatario_user_id=USUARIO, emitido_en=EN)
    with pytest.raises(db.AprobacionInvalida, match="otra pieza"):
        _aprobar(conn, pid, mp4, _comprobante(codigo))


def test_codigo_de_otro_usuario_no_sirve(caso):
    conn, pid, mp4, _ = caso
    codigo = db.emitir_codigo(conn, pid, ruta_mp4=mp4, destinatario_user_id=USUARIO, emitido_en=EN)
    with pytest.raises(db.AprobacionInvalida, match="destinatario"):
        _aprobar(conn, pid, mp4, _comprobante(codigo, user_id="9999"))
    assert conn.execute("SELECT usado_en FROM codigos_aprobacion").fetchone()[0] is None


def test_codigo_caducado_no_sirve(caso):
    conn, pid, mp4, _ = caso
    codigo = db.emitir_codigo(conn, pid, ruta_mp4=mp4, destinatario_user_id=USUARIO, emitido_en=EN)
    with pytest.raises(db.AprobacionInvalida, match="caduco"):
        _aprobar(conn, pid, mp4, _comprobante(codigo), otorgada_en="2026-10-24T10:00:01-06:00")


def test_mp4_cambiado_despues_de_enviarlo_a_revision_no_se_aprueba(caso):
    conn, pid, mp4, _ = caso
    codigo = db.emitir_codigo(conn, pid, ruta_mp4=mp4, destinatario_user_id=USUARIO, emitido_en=EN)
    mp4.write_bytes(b"video cambiado despues de enviarlo")
    with pytest.raises(db.AprobacionInvalida, match="cambio"):
        _aprobar(conn, pid, mp4, _comprobante(codigo))
    assert _n_aprobaciones(conn) == 0


@pytest.mark.parametrize("falta", ["message_id", "user_id", "chat_id", "codigo"])
def test_respuesta_incompleta_de_telegram_no_aprueba_ni_gasta_el_codigo(caso, falta):
    conn, pid, mp4, _ = caso
    codigo = db.emitir_codigo(conn, pid, ruta_mp4=mp4, destinatario_user_id=USUARIO, emitido_en=EN)
    with pytest.raises(db.AprobacionInvalida):
        comp = _comprobante(codigo)
        comp[falta] = ""
        _aprobar(conn, pid, mp4, comp)
    assert _n_aprobaciones(conn) == 0
    assert conn.execute("SELECT usado_en FROM codigos_aprobacion").fetchone()[0] is None


# ── El juez y la politica automatica no autorizan ────────────────────────────

def _politica(tmp_path, activa=True):
    cfg = tmp_path / "politica.json"
    cfg.write_text(json.dumps({"activa": activa, "version": "t", "palabras_operador": "go ya tenemos todo",
                               "apis_pagadas": False, "pilares": ["P4"], "canales": ["facebook"]}))
    return cfg


def test_juez_ok_y_politica_activa_no_autorizan_publicar(caso, tmp_path):
    conn, pid, mp4, _ = caso
    llamadas = []

    def pub():
        llamadas.append(1)
        return {"video_id": "1"}

    pub.ruta_mp4, pub.texto, pub.programada_para = str(mp4), "texto", None
    ev = {"juez": {"veredicto": "OK"}, "tecnica": {"audio": True}, "sha256": db.sha256_archivo(mp4)}
    with pytest.raises(dist.SinAprobacionValida):
        auto.publicar(conn, pid, pub, canal="facebook", fecha=EN, evidencia=ev,
                      ledger=dist.Ledger(tmp_path / "l.jsonl"), config=_politica(tmp_path))
    assert llamadas == []
    assert _n_aprobaciones(conn) == 0


def test_la_politica_guardada_sigue_apagada():
    p = json.loads((RAIZ / "config/publicacion_automatica.json").read_text(encoding="utf-8"))
    assert p["activa"] is False


# ── La compuerta de los publicadores que no pasan por el distribuidor ────────

def test_guardia_bloquea_sin_aprobacion_y_deja_evento(caso):
    conn, pid, mp4, ruta_bd = caso
    with pytest.raises(guardia.PublicacionBloqueada, match="BLOQUEADO"):
        guardia.exigir(mp4, "texto", "facebook", en=EN, ruta_bd=ruta_bd)
    assert conn.execute("SELECT COUNT(*) FROM eventos WHERE detalle LIKE 'subida BLOQUEADA%'").fetchone()[0] == 1


def test_guardia_deja_pasar_solo_lo_aprobado(caso):
    conn, pid, mp4, ruta_bd = caso
    aprobar_como_telegram(conn, pid, ruta_mp4=mp4, texto="texto", canales=["facebook"])
    assert guardia.exigir(mp4, "texto", "facebook", en=EN, ruta_bd=ruta_bd) == pid
    with pytest.raises(guardia.PublicacionBloqueada):
        guardia.exigir(mp4, "otro texto", "facebook", en=EN, ruta_bd=ruta_bd)
    with pytest.raises(guardia.PublicacionBloqueada):
        guardia.exigir(mp4, "texto", "youtube", en=EN, ruta_bd=ruta_bd)
    mp4.write_bytes(b"video modificado")
    with pytest.raises(guardia.PublicacionBloqueada):
        guardia.exigir(mp4, "texto", "facebook", en=EN, ruta_bd=ruta_bd)


def _cargar(ruta, nombre):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_publicador_fb_real_bloquea_antes_de_tocar_la_red(caso, monkeypatch):
    conn, pid, mp4, ruta_bd = caso
    fb = _cargar(RAIZ / "docs/operations/evidencia/publicacion_fb_20261005/publicar_reels_fb.py", "fb_prueba")
    red = []
    import requests
    for metodo in ("post", "get", "put"):
        monkeypatch.setattr(requests, metodo, lambda *a, **k: red.append(a) or (_ for _ in ()).throw(AssertionError("red")))
    monkeypatch.setattr(fb, "_ruta_bd_compuerta", lambda: ruta_bd)
    with pytest.raises(guardia.PublicacionBloqueada):
        fb.publicar_uno("PAGINA", "TOKEN", {"id": "x", "file": str(mp4), "caption": "texto"})
    assert red == []


def test_botones_viejos_de_telegram_ya_no_publican(monkeypatch):
    import sys
    import types
    falso = types.ModuleType("telegram.ext")            # el bot real no se carga en pruebas
    falso.CallbackQueryHandler = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "telegram", types.ModuleType("telegram"))
    monkeypatch.setitem(sys.modules, "telegram.ext", falso)
    rr = _cargar(RAIZ / "telegram_bot/reels_review.py", "rr_prueba")
    botones = [b["callback_data"] for fila in rr.teclado_dict("e99")["inline_keyboard"] for b in fila]
    assert botones == ["rv|no|e99"]
    for funcion in (rr._fb_publicar, rr._yt_publicar):
        assert funcion("e99")["estado"] == "BLOQUEADO"


# ── Inventario: nadie mas aprueba, y todo publicador pasa por la compuerta ───

_EXCLUIR = re.compile(r"(_backup_|respaldo_|\.bak$|[\\/]\.git[\\/]|[\\/]runs[\\/]|site-packages|node_modules|"
                      r"[\\/]\.venv|[\\/]_respaldo_drive[\\/]|[\\/]tests[\\/]|codex_autopublicacion_20261021|"
                      r"fase_a_cola_aprobacion_20261021[\\/]prueba_integral_simulada\.py)")


def _codigo_del_repo():
    for p in RAIZ.rglob("*.py"):
        if not _EXCLUIR.search(str(p)):
            yield p, p.read_text(encoding="utf-8", errors="replace")


# Etapa 4 (22-oct-2026): el manejador del botón APROBAR de Telegram es el único otro lugar que emite y gasta
# códigos; db.aprobar_pieza sigue exigiendo el comprobante del botón. Ningún otro archivo puede sumarse.
_PUEDEN_APROBAR = {RAIZ / "content_factory/db.py", RAIZ / "telegram_bot/aprobacion_botones.py"}


def test_nadie_fuera_de_db_crea_aprobaciones():
    culpables = [str(p.relative_to(RAIZ)) for p, s in _codigo_del_repo()
                 if re.search(r"\b(aprobar_pieza|emitir_codigo)\s*\(", s)
                 and p not in _PUEDEN_APROBAR]
    assert culpables == []


_LLAMADAS_DE_SUBIDA = re.compile(r"videos\(\)\.insert\(|\"upload_phase\": \"start\"|/post/publish/video/init/|"
                                 r"/post/publish/inbox/video/init/")


def test_todo_publicador_pasa_por_la_compuerta():
    sin_compuerta = []
    for p, s in _codigo_del_repo():
        for m in _LLAMADAS_DE_SUBIDA.finditer(s):
            antes = s[:m.start()]
            if "guardia_publicacion" not in s or not re.search(r"(?<!def )_compuerta\(|guardia\.exigir\(", antes):
                sin_compuerta.append("%s:%d" % (p.relative_to(RAIZ), s.count("\n", 0, m.start()) + 1))
    assert sin_compuerta == []
