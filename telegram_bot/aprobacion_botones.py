# -*- coding: utf-8 -*-
"""Etapa 4 (22-oct-2026): botón APROBAR de Telegram → cola (content.db) → distribuidor.

Es el ÚNICO módulo, fuera de content_factory/db.py, que emite y gasta códigos de aprobación
(tests/test_etapa1_compuerta.py lo nombra en su lista). La regla R27 sigue igual: una aprobación
solo nace de que el operador apriete el botón; aquí nada aprueba por él.

Recorrido de un video:
  1. `registrar`: cada MP4 de <pieza>/publicacion.json es una pieza de la cola (una por archivo:
     la aprobación queda atada a la huella de UN archivo, y YouTube y Facebook llevan música distinta).
  2. `enviar_para_aprobar`: emite el código de un solo uso, manda el texto completo y después
     el MP4 como video reproducible con botones. El archivo local original se conserva para publicar.
  3. el operador aprieta APROBAR → `on_callback` → `aprobar_y_publicar`:
     texto igual al que vio → `db.aprobar_pieza` (gasta el código) → `distribucion.publicar_pieza`
     (reconciliar-o-saltar, compuerta, ledger) → lectura de la plataforma → mensaje con el estado:
     APROBADO · PUBLICANDO · PUBLICADO Y CONFIRMADO · SUBIDO, PENDIENTE DE VERIFICAR · INCIERTO.
Un resultado incierto NO se reintenta: el distribuidor lo deja bloqueado hasta revisar el canal.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import logging
import time
from datetime import datetime
from pathlib import Path

from content_factory import db, distribucion

logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parent.parent
LEDGER = RAIZ / "content_factory" / "publicaciones.jsonl"   # el mismo que usa content_factory/automatico.py
_YT_DIR = RAIZ / "docs/operations/evidencia/publicacion_youtube_20261005"
_FB_PUBLICADOR = RAIZ / "docs/operations/evidencia/publicacion_fb_20261005/publicar_reels_fb.py"
_TT_PUBLICADOR = RAIZ / "scripts/tiktok/publicar_tiktok.py"   # token y llamadas de la app «AVILES Publicador»
NOMBRE = {"youtube": "YouTube", "facebook": "Facebook", "tiktok": "TikTok"}


class Desconectado(RuntimeError):
    """La plataforma no está disponible ANTES de subir (credencial vencida, red bloqueada). No se subió nada."""


def _ahora() -> str:
    return datetime.now().astimezone().isoformat()


# ── 1. publicación declarada por la pieza ────────────────────────────────────────

def cargar_publicacion(carpeta: Path) -> dict:
    return json.loads((Path(carpeta) / "publicacion.json").read_text(encoding="utf-8"))


def texto_de(pub: dict, video: dict) -> str:
    """Lo que se aprueba y se sube. En YouTube incluye el TÍTULO (primera línea) para que también quede cubierto."""
    if video["canal"] == "youtube":
        return pub["titulo_youtube"] + "\n\n" + pub["descripcion"]
    return pub["descripcion"]


def registrar(conn, carpeta: Path, *, en: str) -> list[tuple[int, dict]]:
    """Da de alta el tema y una pieza PRODUCIDA por cada MP4. Idempotente por (tema, formato)."""
    carpeta = Path(carpeta).resolve()
    pub = cargar_publicacion(carpeta)
    origen = pub.get("origen", "motor_expediente3d")
    tema_id = db.upsert_tema(conn, {
        "hash_titulo": db.sha256_texto("expediente|%s" % carpeta.name), "titulo": pub["tema"],
        "pilar": pub.get("pilar", "SIN_PILAR"), "intent": "educativo", "creado_en": en[:10],
        "estado": "ENCARGO_el operador", "metodo_clasificacion": "encargo_manual",
        "evidencia": [{"origen": pub.get("origen", "motor_expediente3d"), "carpeta": str(carpeta)}]})
    salida = []
    for v in pub["videos"]:
        ruta = carpeta / v["archivo"]
        pid, _ = db.registrar_pieza(
            conn, tema_id=tema_id, formato=v["formato"], angulo=origen, angulo_metodo="revision_telegram",
            pilar_decision=pub.get("pilar", "SIN_PILAR"), estado="PRODUCIDA", creada_en=en, run_id=carpeta.name,
            detalle_nacimiento="Video de %s: %s (sha256=%s); publicar exige el botón APROBAR de Telegram."
                               % (origen, ruta, db.sha256_archivo(ruta)))
        salida.append((pid, v))
    return salida


def localizar(conn, pieza_id: int) -> tuple[Path, dict, dict]:
    """(carpeta, publicacion, video) de una pieza registrada por `registrar`."""
    fila = conn.execute("SELECT p.formato, t.evidencia_json FROM piezas p JOIN temas t ON t.id = p.tema_id "
                        "WHERE p.id = ?", (pieza_id,)).fetchone()
    if fila is None:
        raise ValueError("la pieza %s no existe" % pieza_id)
    carpeta = Path(json.loads(fila["evidencia_json"])[0]["carpeta"])
    pub = cargar_publicacion(carpeta)
    video = next(v for v in pub["videos"] if v["formato"] == fila["formato"])
    return carpeta, pub, video


# ── 2. envío a el operador con los botones ──────────────────────────────────────────

def _telegram(metodo: str, data: dict, archivo: Path | None = None) -> dict:
    import requests
    token = os.environ.get("VIDEOS_TXT_TELEGRAM_BOT_TOKEN", "").strip()
    url = "https://api.telegram.org/bot%s/%s" % (token, metodo)
    if archivo is None:
        return requests.post(url, data=data, timeout=60).json()
    with open(archivo, "rb") as fh:
        campo = "video" if metodo == "sendVideo" else "document"
        return requests.post(url, data=data, files={campo: (archivo.name, fh)}, timeout=600).json()


def teclado(pieza_id: int, codigo: str, texto: str, canal: str) -> dict:
    t8 = db.sha256_texto(texto)[:8]     # el botón también ata el TEXTO que el operador está viendo
    return {"inline_keyboard": [
        [{"text": "✅ APROBAR y publicar en %s" % NOMBRE[canal], "callback_data": "ap|%d|%s|%s" % (pieza_id, codigo, t8)}],
        [{"text": "🗑 No publicar", "callback_data": "an|%d" % pieza_id}]]}


def enviar_para_aprobar(conn, pieza_id: int, *, destinatario: str, en: str, telegram=_telegram) -> dict:
    carpeta, pub, video = localizar(conn, pieza_id)
    ruta = carpeta / video["archivo"]
    texto = texto_de(pub, video)
    codigo = db.emitir_codigo(conn, pieza_id, ruta_mp4=ruta, destinatario_user_id=destinatario, emitido_en=en)
    sha = db.sha256_archivo(ruta)
    cuerpo = ("📝 Así se publicaría en %s (pieza %d, %s):\n\n%s\n\n🏷 Etiqueta de IA: %s\n\nEstado: PENDIENTE DE TU DECISIÓN"
              % (NOMBRE[video["canal"]], pieza_id, video["formato"], texto,
                 "SÍ (voz narradora de IA)" if video.get("etiqueta_ia") else "no"))
    # Nunca aprobar un texto truncado que el usuario no pudo leer.
    if len(cuerpo) > 4096:
        return {"ok": False, "paso": "texto", "error": "texto demasiado largo para revisión en Telegram"}
    msg = telegram("sendMessage", {"chat_id": destinatario, "text": cuerpo})
    if not msg.get("ok"):
        return {"ok": False, "paso": "texto", "respuesta": msg}
    doc = telegram("sendVideo", {"chat_id": destinatario, "supports_streaming": True,
        "reply_to_message_id": msg["result"]["message_id"],
        "caption": "🎬 %s → %s · pieza %d\nTexto completo en el mensaje al que responde este video.\n"
                   "Estado: PENDIENTE DE TU DECISIÓN\nsha256 %s…"
                   % (video["formato"], NOMBRE[video["canal"]], pieza_id, sha[:12]),
        "reply_markup": json.dumps(teclado(pieza_id, codigo, texto, video["canal"]))}, ruta)
    return {"ok": bool(doc.get("ok")), "paso": "video", "respuesta": doc if not doc.get("ok") else None}


# ── 3. publicadores reales (se construyen DESPUÉS de aprobar; antes de subir comprueban la conexión) ──

def _yt_credenciales():
    """Solo refresca el token guardado. Si caducó, NO abre el navegador (colgaría el bot): avisa."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    c = Credentials.from_authorized_user_file(str(_YT_DIR / "token.json"))
    if not c.valid:
        try:
            c.refresh(Request())
        except Exception as e:  # noqa: BLE001
            raise Desconectado("YouTube desconectado (%s): hay que volver a autorizar el canal"
                               % str(e).split(":")[0][:80]) from None
        (_YT_DIR / "token.json").write_text(c.to_json(), encoding="utf-8")
    return c


def publicador_youtube(ruta: Path, texto: str, *, tags: list[str], etiqueta_ia: bool):
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    from content_factory import guardia_publicacion as guardia
    yt = build("youtube", "v3", credentials=_yt_credenciales(), cache_discovery=False)
    titulo, _, descripcion = texto.partition("\n\n")

    def _publicar():
        guardia.exigir(ruta, texto, "youtube", en=_ahora())
        body = {"snippet": {"title": titulo[:100], "description": descripcion[:4900], "tags": tags, "categoryId": "27"},
                "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}
        if etiqueta_ia:
            body["status"]["containsSyntheticMedia"] = True
        try:
            resp = yt.videos().insert(part="snippet,status", body=body, media_body=MediaFileUpload(
                str(ruta), chunksize=-1, resumable=True, mimetype="video/mp4")).execute()
        except HttpError as e:
            # YouTube crea el video solo al terminar la subida: un 4xx (tope diario, permiso) es un rechazo SIN publicar.
            if 400 <= int(getattr(e.resp, "status", 0) or 0) < 500:
                raise distribucion.RechazoDelCanal("YouTube rechazó: %s" % str(e)[:160]) from None
            raise
        return {"video_id": resp["id"], "url": "https://youtube.com/shorts/%s" % resp["id"]}

    _publicar.ruta_mp4, _publicar.texto, _publicar.programada_para = str(ruta), texto, None
    return _publicar


def _cargar_fb():
    spec = importlib.util.spec_from_file_location("publicar_reels_fb_botones", _FB_PUBLICADOR)
    fb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fb)
    fb.cargar_env()
    return fb


def _fb_credenciales(fb) -> tuple[str, str]:
    import requests
    page = os.environ.get("FB_PAGE_ID", "").strip()
    tf = _FB_PUBLICADOR.parent / "token_pagina.txt"
    token = (tf.read_text(encoding="utf-8").strip() if tf.is_file() and tf.read_text(encoding="utf-8").strip()
             else os.environ.get("FB_PAGE_TOKEN", "").strip())
    if not page or not token:
        raise Desconectado("Facebook: faltan FB_PAGE_ID o el token de la página")
    token = fb.resolver_token_pagina(page, token)
    estado = "sin respuesta"
    try:
        r = requests.get("%s/%s" % (fb.API, page), params={"fields": "name", "access_token": token}, timeout=30)
        estado = "HTTP %s" % r.status_code
        if r.status_code == 200 and "name" in r.json():
            return page, token
    except Exception:  # noqa: BLE001 - una respuesta que no es JSON es el filtro web de la red
        pass
    raise Desconectado("Facebook no responde desde esta red o el token no sirve (%s)" % estado)


def publicador_facebook(ruta: Path, texto: str, *, etiqueta_ia: bool):
    from content_factory.adaptador_fb import construir_publicador
    fb = _cargar_fb()
    page, token = _fb_credenciales(fb)
    return construir_publicador(mp4_path=ruta, caption=texto, page_id=page, token=token, ia=etiqueta_ia)


def _cargar_tt():
    spec = importlib.util.spec_from_file_location("publicar_tiktok_botones", _TT_PUBLICADOR)
    tt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tt)
    return tt


def publicador_tiktok(ruta: Path, texto: str, *, etiqueta_ia: bool, tt=None):
    """TikTok (22-oct-2026). La app «AVILES Publicador» NO está auditada: TikTok rechaza publicar en público
    («unaudited_client_can_only_post_to_private_accounts», probado 22:27) y su API no programa. Lo que SÍ funciona
    (probado: SEND_TO_USER_INBOX) es mandar el video como BORRADOR a la bandeja de el operador; él lo publica o programa
    desde el celular. Antes de subir: token vigente (si no, Desconectado: no se subió nada).
    El texto aprobado viaja en la aprobación y en el mensaje de Telegram (la bandeja no recibe pie de texto)."""
    import requests
    from content_factory import guardia_publicacion as guardia
    tt = tt or _cargar_tt()
    try:
        tt.env()
        tt.token()
    except BaseException as e:  # noqa: BLE001 - token vencido o red: SystemExit/RuntimeError del módulo viejo
        raise Desconectado("TikTok desconectado (%s)" % str(e)[:120]) from None

    def _publicar():
        guardia.exigir(ruta, texto, "tiktok", en=_ahora())
        n = Path(ruta).stat().st_size
        fuente = {"source": "FILE_UPLOAD", "video_size": n, "chunk_size": n, "total_chunk_count": 1}
        try:
            d = tt._post("/post/publish/inbox/video/init/", {"source_info": fuente})
        except RuntimeError as e:
            raise distribucion.RechazoDelCanal("TikTok rechazó: %s" % str(e)[:160]) from None
        with open(ruta, "rb") as fh:
            r = requests.put(d["upload_url"], data=fh.read(), timeout=600, headers={
                "Content-Type": "video/mp4", "Content-Length": str(n), "Content-Range": "bytes 0-%d/%d" % (n - 1, n)})
        if r.status_code not in (200, 201):
            raise distribucion.RechazoDelCanal("TikTok no aceptó el archivo: HTTP %s" % r.status_code)
        return {"video_id": d["publish_id"], "url": "tiktok bandeja %s" % d["publish_id"]}

    _publicar.ruta_mp4, _publicar.texto, _publicar.programada_para = str(ruta), texto, None
    return _publicar


def verificar_en_plataforma(canal: str, vid: str) -> tuple[bool, str]:
    """Lee la plataforma: ¿el video ya es público? (False = todavía no se puede afirmar)."""
    import requests
    try:
        if canal == "tiktok":
            tt = _cargar_tt()
            tt.env()
            estado = "?"
            for _ in range(12):
                d = tt._post("/post/publish/status/fetch/", {"publish_id": vid})
                estado = d.get("status", "?")
                if estado in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX", "FAILED"):
                    break
                time.sleep(10)
            return estado in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"), "TikTok status=%s %s" % (
                estado, d.get("fail_reason") or "")
        if canal == "youtube":   # el token solo tiene permiso de subir: se usa el oEmbed público, como subir_suelto_youtube.py
            r = requests.get("https://www.youtube.com/oembed", timeout=20,
                             params={"url": "https://www.youtube.com/watch?v=%s" % vid, "format": "json"})
            return r.status_code == 200, "oEmbed público HTTP %s" % r.status_code
        fb = _cargar_fb()
        _, token = _fb_credenciales(fb)
        d = requests.get("%s/%s" % (fb.API, vid), params={"fields": "published,status", "access_token": token},
                         timeout=30).json()
        return d.get("published") is True, "published=%s, estado=%s" % (
            d.get("published"), (d.get("status") or {}).get("video_status"))
    except Exception as e:  # noqa: BLE001
        return False, "no se pudo leer la plataforma: %s" % str(e)[:120]


def _registro_legado(canal: str, clave: str, vid: str, url: str) -> None:
    """Anota en los registros viejos (rutas congeladas) para que los scripts manuales no lo vuelvan a subir."""
    log = {"youtube": _YT_DIR / "_youtube_publicados.json",
           "tiktok": _TT_PUBLICADOR.parents[2] / "docs/operations/evidencia/publicacion_tiktok_20261017/_tiktok_publicados.json",
           }.get(canal, _FB_PUBLICADOR.parent / "_publicados.json")
    datos = json.loads(log.read_text(encoding="utf-8")) if log.is_file() else {}
    if clave not in datos:
        datos[clave] = {"video_id": vid, "canal": canal, "url": url, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "origen": "boton_aprobar_telegram"}
        log.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")


def _construir(canal: str, ruta: Path, texto: str, pub: dict, video: dict):
    if canal == "youtube":
        return publicador_youtube(ruta, texto, tags=pub.get("tags_youtube", []), etiqueta_ia=bool(video.get("etiqueta_ia")))
    if canal == "tiktok":
        return publicador_tiktok(ruta, texto, etiqueta_ia=bool(video.get("etiqueta_ia")))
    return publicador_facebook(ruta, texto, etiqueta_ia=bool(video.get("etiqueta_ia")))


# ── 4. lo que pasa al apretar ──────────────────────────────────────────────────

def publicar(conn, pieza_id: int, *, en: str, construir=_construir, verificar=verificar_en_plataforma,
             legado=_registro_legado) -> dict:
    """Publica con la aprobación VIGENTE (no aprueba nada). Devuelve {'linea', 'botones'}."""
    carpeta, pub, video = localizar(conn, pieza_id)
    canal, ruta, texto = video["canal"], carpeta / video["archivo"], texto_de(pub, video)
    if canal == "tiktok" and video.get("modo") == "studio":
        # 30-oct: el operador no publica desde el celular; la bandeja solo duplicaba. La aprobación queda VIGENTE
        # y la programación en TikTok Studio se registra después con scripts/registrar_programado_studio.py.
        return {"linea": "📅 Aprobado para TikTok: se programa en TikTok Studio (no se manda a tu bandeja).",
                "botones": None}
    ledger = distribucion.Ledger(LEDGER)
    publicador = None   # con registro previo (BD o ledger) el distribuidor reconcilia o salta sin tocar la red
    if db.obtener_publicacion(conn, pieza_id, canal) is None and ledger.obtener(pieza_id, canal) is None:
        try:
            publicador = construir(canal, ruta, texto, pub, video)
        except Desconectado as e:
            return {"linea": "⛔ NO SE PUBLICÓ (no se subió nada): %s" % e, "botones": "reintentar"}
    try:
        r = distribucion.publicar_pieza(conn, pieza_id, publicador=publicador, ledger=ledger, canal=canal, fecha=en)
    except distribucion.SinAprobacionValida as e:
        return {"linea": "🔒 BLOQUEADO por la compuerta: %s" % e, "botones": None}
    res, vid = r["resultado"], r.get("identificador_remoto")
    if res in ("publicada", "ya_publicada", "reconciliada", "ya_reconciliada", "publicado_pendiente_bd"):
        url = {"youtube": "https://youtube.com/shorts/%s" % vid, "tiktok": "tiktok publish_id %s" % vid}.get(
            canal, "https://www.facebook.com/reel/%s" % vid)
        legado(canal, video.get("clave_legado", "expediente:%s:%s" % (carpeta.name, video["formato"])), vid, url)
        ap = db.aprobacion_vigente(conn, pieza_id)
        if ap is not None:   # ya cumplió: que ningún script viejo la vuelva a usar para subir otra vez
            db.invalidar_aprobacion(conn, int(ap["id"]), en, "ya publicada en %s (%s); no se reutiliza" % (canal, vid))
        ok, detalle = verificar(canal, vid)
        if ok and canal == "tiktok":
            return {"linea": "📥 EN TU BANDEJA DE TIKTOK como borrador (%s). Publícalo o prográmalo desde el celular; "
                             "copia el texto de este mensaje como descripción." % detalle, "botones": None}
        if ok:
            return {"linea": "✅ PUBLICADO Y CONFIRMADO en %s: %s (%s)" % (NOMBRE[canal], url, detalle), "botones": None}
        return {"linea": "⚠️ SUBIDO a %s (id %s) pero aún sin confirmar que sea público: %s. No se vuelve a subir."
                % (NOMBRE[canal], vid, detalle), "botones": "verificar"}
    if res == "error_api":
        return {"linea": "❌ %s lo rechazó SIN publicar: %s" % (NOMBRE[canal], r.get("error")), "botones": "reintentar"}
    if res in ("resultado_incierto", "intento_sin_confirmar"):
        return {"linea": "⚠️ RESULTADO INCIERTO en %s: pudo haber salido (%s). No se reintenta hasta revisar el canal."
                % (NOMBRE[canal], r.get("error")), "botones": None}
    return {"linea": "ℹ️ %s: %s" % (NOMBRE[canal], res), "botones": None}


def aprobar(conn, pieza_id: int, *, codigo: str, t8: str, comprobante: dict, en: str) -> dict | None:
    """Valida y REGISTRA la decisión por la compuerta (código de un solo uso). None = aprobada y guardada;
    si no, {'linea', 'botones'} con el motivo. No publica."""
    carpeta, pub, video = localizar(conn, pieza_id)
    texto = texto_de(pub, video)
    if db.sha256_texto(texto)[:8] != t8:
        return {"linea": "🔒 NO APROBADO: el texto cambió desde que te lo mandé. Pide que te lo reenvíe.", "botones": None}
    try:
        db.aprobar_pieza(conn, pieza_id, ruta_mp4=carpeta / video["archivo"], texto=texto, canales=[video["canal"]],
                         otorgada_en=en, comprobante=dict(comprobante, codigo=codigo))
    except db.AprobacionInvalida as e:
        return {"linea": "🔒 NO APROBADO: %s" % e, "botones": None}
    return None


def aprobar_y_publicar(conn, pieza_id: int, *, codigo: str, t8: str, comprobante: dict, en: str, **kw) -> dict:
    rechazo = aprobar(conn, pieza_id, codigo=codigo, t8=t8, comprobante=comprobante, en=en)
    return rechazo or publicar(conn, pieza_id, en=en, **kw)


# ── 5. manejador dentro del bot ────────────────────────────────────────────────

def _botones(pieza_id: int, cuales: str | None):
    if cuales == "reintentar":
        return {"inline_keyboard": [[{"text": "🔁 Reintentar publicación", "callback_data": "ar|%d" % pieza_id}]]}
    if cuales == "verificar":
        return {"inline_keyboard": [[{"text": "🔎 Verificar otra vez", "callback_data": "av|%d" % pieza_id}]]}
    return None


def _en_hilo(funcion, *a, **kw):
    """Corre en un hilo con su propia conexión (sqlite no comparte conexiones entre hilos)."""
    conn = db.conectar(db.RUTA_PRODUCCION, produccion=True)
    try:
        db.crear_esquema(conn)
        return funcion(conn, *a, **kw)
    finally:
        conn.close()


def _verificar_de_nuevo(conn, pieza_id: int) -> dict:
    _, _, video = localizar(conn, pieza_id)
    pubrow = db.obtener_publicacion(conn, pieza_id, video["canal"])
    if pubrow is None or not pubrow["identificador_remoto"]:
        return {"linea": "ℹ️ No hay publicación registrada para verificar.", "botones": None}
    ok, detalle = verificar_en_plataforma(video["canal"], pubrow["identificador_remoto"])
    return {"linea": ("✅ CONFIRMADO público en %s: %s" if ok else "⚠️ Aún sin confirmar en %s: %s")
            % (NOMBRE[video["canal"]], detalle), "botones": None if ok else "verificar"}


# 23-oct-2026: el bot atendía los botones de uno en uno y cada aprobación esperaba su publicación (20-30 s);
# los botones apretados en ese rato le llegaban a Telegram vencidos («Query is too old») y q.answer() abortaba la
# aprobación (piezas 188-190 perdidas a las 08:01-08:02). Ahora: el manejador no bloquea (block=False), contesta
# al instante, una respuesta vencida ya no detiene la decisión, y las publicaciones siguen saliendo de una en una.
_EN_CURSO: set[int] = set()
_CANDADO_PUBLICAR: dict = {}


def _candado_publicar() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    if _CANDADO_PUBLICAR.get("loop") is not loop:
        _CANDADO_PUBLICAR.update(loop=loop, candado=asyncio.Lock())
    return _CANDADO_PUBLICAR["candado"]


async def _contestar(q, *a, **k) -> None:
    """La respuesta al botón solo quita el reloj de Telegram; si llega vencida, la decisión sigue en pie
    (la seguridad la dan el código de un solo uso, el usuario y la huella del texto, no esta respuesta)."""
    try:
        await q.answer(*a, **k)
    except Exception as e:  # noqa: BLE001 - p. ej. BadRequest «Query is too old»
        logger.warning("botón: la respuesta a Telegram falló (%s: %s); sigo con la decisión",
                       type(e).__name__, str(e)[:120])


async def on_callback(update, context) -> None:
    q = update.callback_query
    chat = update.effective_chat
    usuario = update.effective_user
    cfg = context.bot_data.get("config")
    if chat is None or usuario is None or chat.id not in list(getattr(cfg, "allowed_chat_ids", []) or []):
        await _contestar(q, "No autorizado", show_alert=True)
        return
    partes = (q.data or "").split("|")
    accion, pieza_id = partes[0], int(partes[1])
    if accion != "ap" and str(usuario.id) != os.environ.get("VIDEOS_TXT_TELEGRAM_CHAT_ID", "").strip():
        await _contestar(q, "Solo el operador decide sobre esta pieza", show_alert=True)
        return
    logger.info("botón %s pieza %d recibido", accion, pieza_id)
    if pieza_id in _EN_CURSO:   # doble toque: la primera ya está en marcha; no se toca el mensaje
        await _contestar(q, "Esta pieza ya se está procesando")
        return
    _EN_CURSO.add(pieza_id)
    try:
        await _contestar(q)
        await _decidir(q, chat, usuario, accion, pieza_id, partes)
    finally:
        _EN_CURSO.discard(pieza_id)


async def _decidir(q, chat, usuario, accion, pieza_id, partes) -> None:
    """Orden (23-oct, revisión de Codex): 1) validar y GUARDAR la decisión; 2) decirlo en el mensaje;
    3) esperar turno y publicar; 4) poner el resultado. Si una edición del mensaje falla, la decisión ya
    está guardada; si el bot se reinicia esperando turno, la aprobación queda VIGENTE y el mensaje ofrece
    «Reintentar publicación» (no se publica nada solo)."""
    es_video = bool(getattr(q.message, "video", None))

    async def editar(texto, reply_markup=None):
        try:
            if es_video:
                await q.edit_message_caption(caption=texto[:1024], reply_markup=reply_markup)
            else:  # mensajes de aprobación ya enviados antes de recuperar la vista de video
                await q.edit_message_text(texto[:4096], reply_markup=reply_markup)
        except Exception as e:  # noqa: BLE001 - el mensaje es aviso; la decisión vive en la base
            logger.warning("botón: no pude editar el mensaje de la pieza %d (%s: %s)",
                           pieza_id, type(e).__name__, str(e)[:120])
    base = (q.message.text or getattr(q.message, "caption", None) or "").replace("Estado: PENDIENTE DE TU DECISIÓN", "").rstrip()
    if accion == "an":
        await editar(base + "\n\nEstado: 🗑 DESCARTADO por ti. No se publica.")
        return
    en = _ahora()
    loop = asyncio.get_running_loop()
    try:
        if accion == "ap":
            comprobante = {"origen": "telegram", "user_id": str(usuario.id), "chat_id": str(chat.id),
                           "message_id": str(q.message.message_id)}
            rechazo = await loop.run_in_executor(None, lambda: _en_hilo(
                aprobar, pieza_id, codigo=partes[2], t8=partes[3], comprobante=comprobante, en=en))
            if rechazo:
                await editar("%s\n\nEstado: %s (%s)" % (base, rechazo["linea"], time.strftime("%H:%M:%S")))
                return
            await editar(base + "\n\nEstado: ✅ APROBADO por ti (guardado). ⏳ Pendiente de publicación…",
                         reply_markup=_markup(pieza_id, "reintentar"))
        else:
            await editar(base + "\n\nEstado: " + {"ar": "🔁 Reintentando…", "av": "🔎 Verificando…"}[accion])
        async with _candado_publicar():   # de una en una, como antes: evita el límite de ráfaga de TikTok
            if accion == "av":
                r = await loop.run_in_executor(None, lambda: _en_hilo(_verificar_de_nuevo, pieza_id))
            else:
                r = await loop.run_in_executor(None, lambda: _en_hilo(publicar, pieza_id, en=en))
    except Exception as e:  # noqa: BLE001 - se muestra; nunca se reintenta solo
        r = {"linea": "❌ Falla inesperada (%s: %s). No se reintenta solo." % (type(e).__name__, str(e)[:200]),
             "botones": "reintentar" if accion != "av" else None}
    # 24-oct: el resultado (p. ej. el rechazo de TikTok) solo quedaba en el mensaje de Telegram; ahora también en la bitácora
    logger.info("botón %s pieza %d resultado: %s", accion, pieza_id, r.get("linea", "")[:300])
    marca = "✅ APROBADO por ti.\n" if accion == "ap" else ""
    texto = "%s\n\nEstado: %s%s (%s)" % (base, marca, r["linea"], time.strftime("%H:%M:%S"))
    await editar(texto, reply_markup=_markup(pieza_id, r.get("botones")))


def _markup(pieza_id: int, cuales: str | None):
    botones = _botones(pieza_id, cuales)
    if not botones:
        return None
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[InlineKeyboardButton(b["text"], callback_data=b["callback_data"]) for b in fila]
                                 for fila in botones["inline_keyboard"]])


def _build_handlers():
    from telegram.ext import CallbackQueryHandler
    return [CallbackQueryHandler(on_callback, pattern=r"^(ap|an|ar|av)\|", block=False)]


APROBACION_HANDLERS = _build_handlers()
