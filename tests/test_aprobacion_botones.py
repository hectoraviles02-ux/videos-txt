# -*- coding: utf-8 -*-
"""Etapa 4 (22-oct-2026): botón APROBAR de Telegram → cola → distribuidor.

Todo SIMULADO: base temporal, plataformas falsas que cuentan llamadas, Telegram falso. No hay red, no se
toca content.db real y no se publica nada. Recorrido completo: registrar → enviar con botón → apretar →
aprobar → publicar → leer la plataforma → estado.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import sys
import types

import pytest

from content_factory import db, distribucion

el operador = "0"
EN = "2026-10-22T17:00:00-06:00"


@pytest.fixture
def ab(monkeypatch):
    falso = types.ModuleType("telegram.ext")            # el bot real no se carga en pruebas
    falso.CallbackQueryHandler = lambda *a, **k: ("handler", a, k)
    tg = types.ModuleType("telegram")                  # botones falsos: el mensaje de «pendiente» lleva Reintentar
    tg.InlineKeyboardButton = lambda texto, callback_data=None: (texto, callback_data)
    tg.InlineKeyboardMarkup = lambda filas: filas
    monkeypatch.setitem(sys.modules, "telegram", tg)
    monkeypatch.setitem(sys.modules, "telegram.ext", falso)
    sys.modules.pop("telegram_bot.aprobacion_botones", None)
    return importlib.import_module("telegram_bot.aprobacion_botones")


@pytest.fixture
def pieza(tmp_path, ab, monkeypatch):
    carpeta = tmp_path / "2026-10-22_prueba"
    carpeta.mkdir()
    (carpeta / "v_YT.mp4").write_bytes(b"video youtube")
    (carpeta / "v_FB.mp4").write_bytes(b"video facebook")
    (carpeta / "publicacion.json").write_text(json.dumps({
        "tema": "Prueba", "pilar": "SIN_PILAR", "titulo_youtube": "Título", "descripcion": "Descripción",
        "tags_youtube": ["x"], "videos": [
            {"formato": "largo_YT", "archivo": "v_YT.mp4", "canal": "youtube", "etiqueta_ia": False},
            {"formato": "largo_FB", "archivo": "v_FB.mp4", "canal": "facebook", "etiqueta_ia": True}]}),
        encoding="utf-8")
    monkeypatch.setattr(ab, "LEDGER", tmp_path / "ledger.jsonl")
    conn = db.conectar(tmp_path / "cola.db")
    db.crear_esquema(conn)
    ids = {v["formato"]: pid for pid, v in ab.registrar(conn, carpeta, en=EN)}
    yield conn, ids, carpeta
    conn.close()


class Telegram:
    def __init__(self):
        self.envios = []

    def __call__(self, metodo, data, archivo=None):
        self.envios.append((metodo, data, archivo))
        return {"ok": True, "result": {"message_id": len(self.envios)}}

    def boton(self, texto_inicia):
        teclado = json.loads(self.envios[-1][1]["reply_markup"])
        return next(b["callback_data"] for fila in teclado["inline_keyboard"] for b in fila
                    if b["text"].startswith(texto_inicia))


class Plataforma:
    """Publicador falso: cuenta llamadas y declara lo que sube, como los reales."""

    def __init__(self, vid="VID1", falla=None, publica=True):
        self.llamadas, self.vid, self.falla, self.publica = 0, vid, falla, publica

    def construir(self, canal, ruta, texto, pub, video):
        def _publicar():
            self.llamadas += 1
            if self.falla:
                raise self.falla
            return {"video_id": self.vid}
        _publicar.ruta_mp4, _publicar.texto, _publicar.programada_para = str(ruta), texto, None
        return _publicar

    def verificar(self, canal, vid):
        return self.publica, "lectura simulada"


def _apretar(ab, conn, pid, data, plataforma, usuario=el operador, **kw):
    _, p, codigo, t8 = data.split("|")
    comp = {"origen": "telegram", "user_id": usuario, "chat_id": el operador, "message_id": "900"}
    return ab.aprobar_y_publicar(conn, int(p), codigo=codigo, t8=t8, comprobante=comp, en=EN,
                                 construir=plataforma.construir, verificar=plataforma.verificar,
                                 legado=lambda *a: None, **kw)


def _enviar(ab, conn, pid):
    tg = Telegram()
    r = ab.enviar_para_aprobar(conn, pid, destinatario=el operador, en=EN, telegram=tg)
    assert r["ok"]
    return tg


# ── envío ─────────────────────────────────────────────────────────────────────

def test_una_pieza_por_archivo_y_registrar_es_idempotente(ab, pieza):
    conn, ids, carpeta = pieza
    assert len(set(ids.values())) == 2
    assert {pid for pid, _ in ab.registrar(conn, carpeta, en=EN)} == set(ids.values())


def test_se_manda_el_mp4_tal_cual_y_el_texto_completo_con_el_boton(ab, pieza):
    conn, ids, carpeta = pieza
    tg = _enviar(ab, conn, ids["largo_YT"])
    (m1, d1, _), (m2, d2, archivo) = tg.envios
    assert m1 == "sendMessage" and "Título\n\nDescripción" in d1["text"]
    assert m2 == "sendVideo" and archivo == carpeta / "v_YT.mp4"
    assert d2["reply_to_message_id"] == 1 and d2["supports_streaming"] is True
    assert tg.boton("✅").startswith("ap|%d|" % ids["largo_YT"])
    assert len(tg.boton("✅").encode()) <= 64                           # límite de Telegram
    assert db.aprobacion_vigente(conn, ids["largo_YT"]) is None          # mandar NO aprueba


# ── recorrido completo ────────────────────────────────────────────────────────

def test_apretar_aprueba_publica_confirma_y_no_deja_aprobacion_reutilizable(ab, pieza):
    conn, ids, _ = pieza
    pid = ids["largo_YT"]
    tg = _enviar(ab, conn, pid)
    plat = Plataforma()
    r = _apretar(ab, conn, pid, tg.boton("✅"), plat)
    assert plat.llamadas == 1
    assert r["linea"].startswith("✅ PUBLICADO Y CONFIRMADO en YouTube")
    assert db.obtener_publicacion(conn, pid, "youtube")["estado"] == db.PUB_CONFIRMADA
    assert db.aprobacion_vigente(conn, pid) is None                      # ya cumplió: no sirve para otra subida
    ap = conn.execute("SELECT * FROM aprobaciones WHERE pieza_id = ?", (pid,)).fetchone()
    assert json.loads(ap["canales_json"]) == ["youtube"] and json.loads(ap["comprobante_json"])["user_id"] == el operador


def test_el_mismo_boton_dos_veces_no_publica_dos_veces(ab, pieza):
    conn, ids, _ = pieza
    pid = ids["largo_FB"]
    tg = _enviar(ab, conn, pid)
    plat = Plataforma()
    _apretar(ab, conn, pid, tg.boton("✅"), plat)
    r = _apretar(ab, conn, pid, tg.boton("✅"), plat)
    assert plat.llamadas == 1 and "ya se uso" in r["linea"]


def test_subido_pero_sin_confirmar_lo_dice_y_ofrece_verificar(ab, pieza):
    conn, ids, _ = pieza
    tg = _enviar(ab, conn, ids["largo_FB"])
    r = _apretar(ab, conn, ids["largo_FB"], tg.boton("✅"), Plataforma(publica=False))
    assert r["linea"].startswith("⚠️ SUBIDO a Facebook") and r["botones"] == "verificar"


# ── rechazos: 0 llamadas a la plataforma ─────────────────────────────────────

def test_otro_usuario_no_aprueba(ab, pieza):
    conn, ids, _ = pieza
    tg = _enviar(ab, conn, ids["largo_YT"])
    plat = Plataforma()
    r = _apretar(ab, conn, ids["largo_YT"], tg.boton("✅"), plat, usuario="555")
    assert plat.llamadas == 0 and r["linea"].startswith("🔒 NO APROBADO")


def test_texto_cambiado_despues_de_mandarlo_no_se_aprueba(ab, pieza):
    conn, ids, carpeta = pieza
    tg = _enviar(ab, conn, ids["largo_YT"])
    pub = json.loads((carpeta / "publicacion.json").read_text(encoding="utf-8"))
    pub["descripcion"] = "Otra cosa"
    (carpeta / "publicacion.json").write_text(json.dumps(pub), encoding="utf-8")
    plat = Plataforma()
    r = _apretar(ab, conn, ids["largo_YT"], tg.boton("✅"), plat)
    assert plat.llamadas == 0 and "el texto cambió" in r["linea"]
    assert db.aprobacion_vigente(conn, ids["largo_YT"]) is None


def test_video_cambiado_despues_de_mandarlo_no_se_aprueba(ab, pieza):
    conn, ids, carpeta = pieza
    tg = _enviar(ab, conn, ids["largo_YT"])
    (carpeta / "v_YT.mp4").write_bytes(b"video youtube EDITADO")
    plat = Plataforma()
    r = _apretar(ab, conn, ids["largo_YT"], tg.boton("✅"), plat)
    assert plat.llamadas == 0 and "MP4 cambio" in r["linea"]


def test_el_boton_de_un_video_no_aprueba_otro(ab, pieza):
    conn, ids, _ = pieza
    tg = _enviar(ab, conn, ids["largo_YT"])
    _, _, codigo, t8 = tg.boton("✅").split("|")
    plat = Plataforma()
    r = _apretar(ab, conn, ids["largo_FB"], "ap|%d|%s|%s" % (ids["largo_FB"], codigo, t8), plat)
    assert plat.llamadas == 0 and r["linea"].startswith("🔒 NO APROBADO")


def test_plataforma_desconectada_no_sube_y_permite_reintentar_sin_otra_aprobacion(ab, pieza):
    conn, ids, _ = pieza
    pid = ids["largo_YT"]
    tg = _enviar(ab, conn, pid)

    def caida(*a):
        raise ab.Desconectado("YouTube desconectado")
    r = ab.aprobar_y_publicar(conn, pid, codigo=tg.boton("✅").split("|")[2], t8=tg.boton("✅").split("|")[3],
                              comprobante={"origen": "telegram", "user_id": el operador, "chat_id": el operador,
                                           "message_id": "1"}, en=EN, construir=caida, legado=lambda *a: None)
    assert r["linea"].startswith("⛔ NO SE PUBLICÓ") and r["botones"] == "reintentar"
    assert db.obtener_publicacion(conn, pid, "youtube") is None
    plat = Plataforma()
    r2 = ab.publicar(conn, pid, en=EN, construir=plat.construir, verificar=plat.verificar, legado=lambda *a: None)
    assert plat.llamadas == 1 and r2["linea"].startswith("✅ PUBLICADO Y CONFIRMADO")


def test_error_dudoso_queda_incierto_y_no_se_reintenta(ab, pieza):
    conn, ids, _ = pieza
    pid = ids["largo_FB"]
    tg = _enviar(ab, conn, pid)
    plat = Plataforma(falla=ConnectionError("se cortó"))
    r = _apretar(ab, conn, pid, tg.boton("✅"), plat)
    assert r["linea"].startswith("⚠️ RESULTADO INCIERTO")
    r2 = ab.publicar(conn, pid, en=EN, construir=plat.construir, verificar=plat.verificar, legado=lambda *a: None)
    assert plat.llamadas == 1 and r2["linea"].startswith("⚠️ RESULTADO INCIERTO")


def test_rechazo_seguro_permite_reintentar(ab, pieza):
    conn, ids, _ = pieza
    pid = ids["largo_YT"]
    tg = _enviar(ab, conn, pid)
    r = _apretar(ab, conn, pid, tg.boton("✅"), Plataforma(falla=distribucion.RechazoDelCanal("tope diario")))
    assert r["linea"].startswith("❌ YouTube lo rechazó SIN publicar") and r["botones"] == "reintentar"
    plat = Plataforma()
    r2 = ab.publicar(conn, pid, en=EN, construir=plat.construir, verificar=plat.verificar, legado=lambda *a: None)
    assert plat.llamadas == 1 and r2["linea"].startswith("✅ PUBLICADO")


# ── el manejador del bot ─────────────────────────────────────────────────────

class _Q:
    def __init__(self, data, usuario):
        self.data, self.ediciones, self.respuestas = data, [], []
        self.message = types.SimpleNamespace(text="Texto\n\nEstado: PENDIENTE DE TU DECISIÓN", message_id=7)
        self.usuario = usuario

    async def answer(self, *a, **k):
        self.respuestas.append((a, k))

    async def edit_message_text(self, texto, reply_markup=None):
        self.ediciones.append(texto)


def _update(data, usuario, chat):
    q = _Q(data, usuario)
    up = types.SimpleNamespace(callback_query=q, effective_chat=types.SimpleNamespace(id=chat),
                               effective_user=types.SimpleNamespace(id=usuario))
    ctx = types.SimpleNamespace(bot_data={"config": types.SimpleNamespace(allowed_chat_ids=[int(el operador)])})
    return up, ctx, q


def test_manejador_rechaza_chat_no_autorizado_sin_tocar_nada(ab, monkeypatch):
    llamado = []
    monkeypatch.setattr(ab, "_en_hilo", lambda *a, **k: llamado.append(1))
    up, ctx, q = _update("ap|1|%s|abcdef12" % ("0" * 32), 999, 999)
    asyncio.run(ab.on_callback(up, ctx))
    assert llamado == [] and q.ediciones == [] and q.respuestas[0][1].get("show_alert")


def test_manejador_quita_los_botones_antes_de_publicar_y_muestra_el_resultado(ab, monkeypatch):
    monkeypatch.setenv("VIDEOS_TXT_TELEGRAM_CHAT_ID", el operador)
    monkeypatch.setattr(ab, "_en_hilo", lambda f, *a, **k: None if f is ab.aprobar else
                        {"linea": "✅ PUBLICADO Y CONFIRMADO en YouTube: x", "botones": None})
    up, ctx, q = _update("ap|1|%s|abcdef12" % ("0" * 32), int(el operador), int(el operador))
    asyncio.run(ab.on_callback(up, ctx))
    assert "APROBADO por ti (guardado)" in q.ediciones[0] and "Pendiente de publicación" in q.ediciones[0]
    assert "PUBLICADO Y CONFIRMADO" in q.ediciones[-1]


def test_no_publicar_descarta(ab, monkeypatch):
    monkeypatch.setenv("VIDEOS_TXT_TELEGRAM_CHAT_ID", el operador)
    up, ctx, q = _update("an|1", int(el operador), int(el operador))
    asyncio.run(ab.on_callback(up, ctx))
    assert "DESCARTADO" in q.ediciones[-1]


def test_callback_en_video_edita_caption_sin_publicar_al_rechazar(ab, monkeypatch):
    monkeypatch.setenv("VIDEOS_TXT_TELEGRAM_CHAT_ID", el operador)
    up, ctx, q = _update("an|1", int(el operador), int(el operador))
    q.message.text = None
    q.message.video = True
    q.message.caption = "Video pendiente"
    async def caption(**kw):
        q.ediciones.append(kw["caption"])
    q.edit_message_caption = caption
    monkeypatch.setattr(ab, "_en_hilo", lambda *a, **k: pytest.fail("no debe publicar"))
    asyncio.run(ab.on_callback(up, ctx))
    assert "Video pendiente" in q.ediciones[-1] and "DESCARTADO" in q.ediciones[-1]


def test_texto_largo_no_se_trunca_ni_envia_boton(ab, pieza):
    conn, ids, carpeta = pieza
    p = carpeta / "publicacion.json"
    pub = json.loads(p.read_text(encoding="utf-8"))
    pub["descripcion"] = "x" * 4100
    p.write_text(json.dumps(pub), encoding="utf-8")
    tg = Telegram()
    r = ab.enviar_para_aprobar(conn, ids["largo_YT"], destinatario=el operador, en=EN, telegram=tg)
    assert not r["ok"] and tg.envios == []


def test_entrada_del_ciclo_y_asistente_entrega_variantes_con_compuerta(ab, tmp_path, monkeypatch):
    from telegram_bot import reels_review as rr
    monkeypatch.setattr(rr, "REPO", tmp_path)
    monkeypatch.setattr(rr, "POC", tmp_path)
    monkeypatch.setattr(rr, "_FBPUB", tmp_path / "fb")
    monkeypatch.setattr(rr, "REELS_V2", tmp_path / "fb")
    monkeypatch.setattr(rr, "REELS_V2_YT", tmp_path / "yt")
    monkeypatch.setattr(db, "RUTA_PRODUCCION", tmp_path / "cola.db")
    monkeypatch.setenv("VIDEOS_TXT_TELEGRAM_CHAT_ID", el operador)
    monkeypatch.setenv("VIDEOS_TXT_TELEGRAM_BOT_TOKEN", "simulado")
    (tmp_path / "_banco.json").write_text(json.dumps([
        {"id": "e1", "titulo": "Prueba", "narracion": "Explicación"}]), encoding="utf-8")
    for canal in ("fb", "yt"):
        (tmp_path / canal).mkdir()
        (tmp_path / canal / "e1_prueba.mp4").write_bytes(canal.encode())
    tg = Telegram()
    envio = ab.enviar_para_aprobar
    monkeypatch.setattr(ab, "enviar_para_aprobar", lambda *a, **k: envio(*a, telegram=tg, **k))
    assert rr.enviar_reel_con_botones("e1")["ok"]
    assert [x[0] for x in tg.envios] == ["sendMessage", "sendVideo"] * 2
    assert {x[2].parent.name for x in tg.envios if x[0] == "sendVideo"} == {"fb", "yt"}
    conn = db.conectar(db.RUTA_PRODUCCION, produccion=True)
    assert conn.execute("SELECT count(*) FROM aprobaciones").fetchone()[0] == 0
    conn.close()
    # La variante histórica no vuelve a ofrecerse; la otra conserva su identidad.
    (tmp_path / "fb/_publicados.json").write_text('{"e1": {"video_id": "previo"}}')
    tg.envios.clear()
    assert rr.enviar_reel_con_botones("e1")["ok"]
    assert [x[2].parent.name for x in tg.envios if x[0] == "sendVideo"] == ["yt"]


# ── TikTok (22-oct-2026) ──────────────────────────────────────────────────
class _TTFalso:
    def __init__(self, token_ok=True, init_error=None, opciones=("PUBLIC_TO_EVERYONE", "SELF_ONLY")):
        self.token_ok, self.init_error, self.opciones, self.posts = token_ok, init_error, opciones, []

    def env(self):
        return "k", "s"

    def token(self):
        if not self.token_ok:
            raise SystemExit("No se pudo renovar el token")
        return "t"

    def info(self):
        return {"privacy_level_options": list(self.opciones)}

    def _post(self, path, body):
        self.posts.append((path, body))
        if self.init_error:
            raise RuntimeError(self.init_error)
        return {"publish_id": "p_123", "upload_url": "http://subida.invalida/"}


def test_tiktok_sin_token_es_desconectado_y_no_sube(tmp_path):
    import pytest
    from telegram_bot import aprobacion_botones as ab
    mp4 = tmp_path / "v.mp4"
    mp4.write_bytes(b"x")
    with pytest.raises(ab.Desconectado):
        ab.publicador_tiktok(mp4, "texto", etiqueta_ia=True, tt=_TTFalso(token_ok=False))


def test_tiktok_error_en_init_es_rechazo_sin_publicar(tmp_path, monkeypatch):
    import pytest
    from content_factory import distribucion, guardia_publicacion
    from telegram_bot import aprobacion_botones as ab
    monkeypatch.setattr(guardia_publicacion, "exigir", lambda *a, **k: 1)
    mp4 = tmp_path / "v.mp4"
    mp4.write_bytes(b"x")
    tt = _TTFalso(init_error="unaudited_client_can_only_post_to_private_accounts: x")
    pub = ab.publicador_tiktok(mp4, "texto", etiqueta_ia=True, tt=tt)
    assert pub.ruta_mp4 == str(mp4) and pub.texto == "texto" and pub.programada_para is None
    with pytest.raises(distribucion.RechazoDelCanal):
        pub()
    assert tt.posts[0][0] == "/post/publish/inbox/video/init/"     # borrador a la bandeja, no publicación directa


def test_tiktok_sin_compuerta_no_sube(tmp_path, monkeypatch):
    import pytest
    from content_factory import guardia_publicacion
    from telegram_bot import aprobacion_botones as ab

    def bloquea(*a, **k):
        raise guardia_publicacion.PublicacionBloqueada("sin aprobación")
    monkeypatch.setattr(guardia_publicacion, "exigir", bloquea)
    mp4 = tmp_path / "v.mp4"
    mp4.write_bytes(b"x")
    tt = _TTFalso()
    with pytest.raises(guardia_publicacion.PublicacionBloqueada):
        ab.publicador_tiktok(mp4, "texto", etiqueta_ia=True, tt=tt)()
    assert [p for p, _ in tt.posts] == []          # ni siquiera el init


# ── 23-oct-2026: botones apretados mientras otra pieza publica ────────────────

def test_respuesta_vencida_no_pierde_la_aprobacion(ab, monkeypatch):
    """Caso real 08:01: Telegram contestó «Query is too old» y la aprobación se perdía."""
    monkeypatch.setenv("VIDEOS_TXT_TELEGRAM_CHAT_ID", el operador)
    hechas = []
    monkeypatch.setattr(ab, "_en_hilo", lambda f, *a, **k: None if f is ab.aprobar else
                        hechas.append(a) or {"linea": "✅ PUBLICADO", "botones": None})
    up, ctx, q = _update("ap|188|%s|abcdef12" % ("0" * 32), int(el operador), int(el operador))

    async def vencida(*a, **k):
        raise RuntimeError("Query is too old and response timeout expired or query id is invalid")
    q.answer = vencida
    asyncio.run(ab.on_callback(up, ctx))
    assert hechas == [(188,)] and "PUBLICADO" in q.ediciones[-1]


def test_doble_toque_simultaneo_procesa_una_sola_vez(ab, monkeypatch):
    monkeypatch.setenv("VIDEOS_TXT_TELEGRAM_CHAT_ID", el operador)
    hechas = []

    def lento(f, *a, **k):
        if f is ab.aprobar:
            return None
        import time as _t
        _t.sleep(0.3)
        hechas.append(a)
        return {"linea": "✅ PUBLICADO", "botones": None}
    monkeypatch.setattr(ab, "_en_hilo", lento)
    up1, ctx, q1 = _update("ap|5|%s|abcdef12" % ("0" * 32), int(el operador), int(el operador))
    up2, _, q2 = _update("ap|5|%s|abcdef12" % ("0" * 32), int(el operador), int(el operador))

    async def ambos():
        await asyncio.gather(ab.on_callback(up1, ctx), ab.on_callback(up2, ctx))
    asyncio.run(ambos())
    assert len(hechas) == 1 and q2.ediciones == [] and "ya se está procesando" in q2.respuestas[0][0][0]


def test_botones_de_piezas_distintas_contestan_al_instante_y_publican_de_una_en_una(ab, monkeypatch):
    monkeypatch.setenv("VIDEOS_TXT_TELEGRAM_CHAT_ID", el operador)
    import threading
    import time as _t
    activos, maximo, contestado = [0], [0], {}
    candado = threading.Lock()

    def publica(f, pid, **k):
        if f is ab.aprobar:
            return None
        with candado:
            activos[0] += 1
            maximo[0] = max(maximo[0], activos[0])
        _t.sleep(0.2)
        with candado:
            activos[0] -= 1
        return {"linea": "✅ PUBLICADO", "botones": None}
    monkeypatch.setattr(ab, "_en_hilo", publica)
    ups = [_update("ap|%d|%s|abcdef12" % (p, "0" * 32), int(el operador), int(el operador)) for p in (11, 12, 13)]
    for p, (_, _, q) in zip((11, 12, 13), ups):
        async def ans(*a, _p=p, **k):
            contestado[_p] = _t.perf_counter()
        q.answer = ans

    async def todos():
        t0 = _t.perf_counter()
        await asyncio.gather(*(ab.on_callback(up, ctx) for up, ctx, _ in ups))
        return t0
    t0 = asyncio.run(todos())
    assert max(contestado.values()) - t0 < 0.1          # nadie espera a que otra pieza termine de publicar
    assert maximo[0] == 1                               # las publicaciones siguen saliendo de una en una


def test_el_manejador_de_botones_no_bloquea_al_bot(ab):
    assert ab.APROBACION_HANDLERS[0][2].get("block") is False


def test_el_mismo_codigo_en_dos_hilos_a_la_vez_publica_una_sola_vez(ab, pieza, tmp_path):
    """Doble toque real: dos hilos con su propia conexión, mismo código, al mismo tiempo."""
    import threading
    conn, ids, _ = pieza
    pid = ids["largo_FB"]
    data = _enviar(ab, conn, pid).boton("✅")
    plat = Plataforma()
    salida, barrera = [], threading.Barrier(2)

    def apretar():
        c = db.conectar(tmp_path / "cola.db")
        try:
            barrera.wait()
            salida.append(_apretar(ab, c, pid, data, plat)["linea"])
        finally:
            c.close()
    hilos = [threading.Thread(target=apretar) for _ in range(2)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert plat.llamadas == 1 and sum(x.startswith("🔒") for x in salida) == 1


# ── 23-oct-2026 (2.ª revisión de Codex): la decisión se guarda ANTES de tocar el mensaje ──

def test_la_decision_se_guarda_aunque_falle_editar_el_mensaje(ab, monkeypatch):
    monkeypatch.setenv("VIDEOS_TXT_TELEGRAM_CHAT_ID", el operador)
    orden = []
    monkeypatch.setattr(ab, "_en_hilo", lambda f, *a, **k: orden.append(f.__name__) or (
        None if f is ab.aprobar else {"linea": "✅ PUBLICADO", "botones": None}))
    up, ctx, q = _update("ap|9|%s|abcdef12" % ("0" * 32), int(el operador), int(el operador))

    async def falla(*a, **k):
        raise RuntimeError("Message is not modified / red caída")
    q.edit_message_text = falla
    asyncio.run(ab.on_callback(up, ctx))
    assert orden == ["aprobar", "publicar"]          # la falla al editar no detuvo ni la decisión ni la publicación


def test_aprobado_se_muestra_solo_despues_de_guardar(ab, monkeypatch):
    monkeypatch.setenv("VIDEOS_TXT_TELEGRAM_CHAT_ID", el operador)
    up, ctx, q = _update("ap|9|%s|abcdef12" % ("0" * 32), int(el operador), int(el operador))
    visto = []

    def en_hilo(f, *a, **k):
        if f is ab.aprobar:
            visto.append(list(q.ediciones))               # lo que el mensaje decía cuando se guardó
            return {"linea": "🔒 NO APROBADO: el codigo ya se uso", "botones": None}
        pytest.fail("rechazada: no debe publicar")
    monkeypatch.setattr(ab, "_en_hilo", en_hilo)
    asyncio.run(ab.on_callback(up, ctx))
    assert visto == [[]] and not any("APROBADO por ti" in e for e in q.ediciones)
    assert "NO APROBADO" in q.ediciones[-1]


def test_reinicio_esperando_turno_deja_la_aprobacion_guardada_y_el_boton_de_reintento(ab, pieza):
    """Se aprueba (se guarda) y el bot muere antes de publicar: la aprobación sigue VIGENTE, nada se
    publica solo, y «Reintentar» publica con ESA aprobación una sola vez."""
    conn, ids, _ = pieza
    pid = ids["largo_YT"]
    data = _enviar(ab, conn, pid).boton("✅")
    _, p, codigo, t8 = data.split("|")
    comp = {"origen": "telegram", "user_id": el operador, "chat_id": el operador, "message_id": "900"}
    assert ab.aprobar(conn, pid, codigo=codigo, t8=t8, comprobante=comp, en=EN) is None
    # aquí «se reinicia» el bot: no hubo publicación
    assert db.aprobacion_vigente(conn, pid) is not None and db.obtener_publicacion(conn, pid, "youtube") is None
    assert ab._botones(pid, "reintentar")["inline_keyboard"][0][0]["callback_data"] == "ar|%d" % pid
    plat = Plataforma()
    kw = dict(construir=plat.construir, verificar=plat.verificar, legado=lambda *a: None)
    assert ab.publicar(conn, pid, en=EN, **kw)["linea"].startswith("✅ PUBLICADO")
    ab.publicar(conn, pid, en=EN, **kw)                               # segundo «Reintentar»
    assert plat.llamadas == 1
    assert ab.aprobar(conn, pid, codigo=codigo, t8=t8, comprobante=comp, en=EN)["linea"].startswith("🔒")
