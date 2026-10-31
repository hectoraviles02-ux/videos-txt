# -*- coding: utf-8 -*-
"""Revisión de reels por Telegram: enviar con botones + publicar al elegir.

- NO toca handlers.py. Se registra desde bot.py (REELS_HANDLERS).
- Las acciones DIRECTAS antiguas siguen bloqueadas. El envío del ciclo y del
  asistente usa ahora aprobacion_botones: video reproducible y aprobación por pieza.
- (antes) Botones: [YouTube] [Facebook] [Ambos] [No publicar]. SIN publicación automática.
- Autoriza por chat_id (config.allowed_chat_ids). Idempotencia ANTES de publicar
  (lee los JSON existentes; no republica). Fuente de verdad: los JSON actuales.
- Estados de mensaje legibles; no borra el resultado anterior.
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
POC = REPO / "docs/operations/evidencia/poc_terminal_shorts_20261005"
_FBPUB = REPO / "docs/operations/evidencia/publicacion_fb_20261005"
for _p in (str(_FBPUB), str(POC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

REELS_V2 = POC / "reels_v2"
REELS_V2_YT = POC / "reels_v2_yt"


# ── rutas de reel ────────────────────────────────────────────────────────
def ruta_reel(eid: str) -> Path | None:
    """Archivo para la VISTA PREVIA: prefiere reels_v2 (el PULIDO, con música, = lo que
    se publica en Facebook), y si no existe usa reels_v2_yt. Antes preferia v2-yt (sin
    música) y eso hacia que la preview != lo publicado en FB (MF-001). Corregido."""
    for d in (REELS_V2, REELS_V2_YT):   # preview = el pulido (FB); fallback v2-yt
        hits = sorted(d.glob("%s_*.mp4" % eid))
        if hits:
            return hits[0]
    return None


# ── publicadores (reutilizan lo existente; dedup ANTES) ────────────────────
BLOQUEO = ("publicacion directa desactivada (Etapa 1, 21-oct-2026): todo video pasa por la "
           "cola con la aprobacion de operador atada a su huella")


def _fb_publicar(eid: str) -> dict:
    return {"estado": "BLOQUEADO", "detalle": BLOQUEO}
    import publicar_reels_fb as fb  # noqa: E402 - codigo anterior, inalcanzable a proposito
    fb.cargar_env()
    pub = fb.publicados()
    if eid in pub:   # IDEMPOTENCIA antes de publicar
        return {"estado": "YA_PUBLICADO", "detalle": str(pub[eid].get("video_id"))}
    items = {it["id"]: it for it in fb.cola()}
    if eid not in items:
        return {"estado": "ERROR", "detalle": "no hay archivo v2 para %s en reels_v2/" % eid}
    page_id = os.environ.get("FB_PAGE_ID", "").strip()
    tf = fb.HERE / "token_pagina.txt"
    token = (tf.read_text(encoding="utf-8").strip() if tf.is_file() and tf.read_text(encoding="utf-8").strip()
             else os.environ.get("FB_PAGE_TOKEN", "").strip())
    if not page_id or not token:
        return {"estado": "ERROR", "detalle": "falta FB_PAGE_ID o token de página"}
    token = fb.resolver_token_pagina(page_id, token)
    try:
        vid, _resp = fb.publicar_uno(page_id, token, items[eid])
        pub[eid] = {"video_id": vid, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        fb.guardar(pub)
        return {"estado": "PUBLICADO", "video_id": vid,
                "url": "https://www.facebook.com/reel/%s" % vid}
    except Exception as e:
        # código de error ESTRUCTURADO de Meta (no parseo de texto "613")
        code = None
        resp = getattr(e, "response", None)
        if resp is not None:
            try:
                code = resp.json().get("error", {}).get("code")
            except Exception:
                code = None
        if code == 613:
            return {"estado": "LIMITE", "detalle": "rate limit 613 (30/24h)"}
        return {"estado": "ERROR", "detalle": ("code=%s " % code if code else "") + str(e)[:160]}


def _yt_publicar(eid: str) -> dict:
    return {"estado": "BLOQUEADO", "detalle": BLOQUEO}
    from telegram_bot import adaptador_yt  # noqa: E402 - codigo anterior, inalcanzable a proposito
    return adaptador_yt.publicar_youtube(eid)


# ── formato de estado ──────────────────────────────────────────────────────
def _fmt(canal: str, r: dict) -> str:
    e = r.get("estado")
    if e == "PUBLICADO":
        return "✅ PUBLICADO EN %s: %s" % (canal, r.get("url") or r.get("video_id") or "")
    if e == "YA_PUBLICADO":
        return "↩️ YA PUBLICADO EN %s (no republico)" % canal
    if e == "BLOQUEADO":
        return "🔒 BLOQUEADO EN %s: %s" % (canal, r.get("detalle", ""))
    if e == "LIMITE":
        return "⏸️ LÍMITE %s: %s — queda PENDIENTE, sin reintentar" % (canal, r.get("detalle", ""))
    return "❌ ERROR %s: %s" % (canal, r.get("detalle", ""))


# ── envío con botones (lo usa el disparador, fuera del bot) ────────────────
def teclado_dict(eid: str) -> dict:
    # Etapa 1: sin botones de publicacion directa (ver BLOQUEO).
    return {"inline_keyboard": [
        [{"text": "🗑 No publicar", "callback_data": "rv|no|%s" % eid}],
    ]}


def enviar_reel_con_botones(eid: str, mp4_path: str | None = None, chat_id: str | None = None) -> dict:
    """Entrada compartida por ciclo diario y asistente: usa la aprobación vigente."""
    import re
    from telegram_bot import aprobacion_botones as ab
    from content_factory import db
    if not re.fullmatch(r"e[0-9]+", eid):
        return {"ok": False, "error": "identificador de reel inválido"}
    chat_id = str(chat_id or os.environ.get("VIDEOS_TXT_TELEGRAM_CHAT_ID", "")).strip()
    if not os.environ.get("VIDEOS_TXT_TELEGRAM_BOT_TOKEN") or not chat_id:
        return {"ok": False, "error": "falta token o chat_id"}
    banco = {e["id"]: e for e in json.loads((POC / "_banco.json").read_text(encoding="utf-8"))}
    if eid not in banco:
        return {"ok": False, "error": "reel sin guion registrado"}
    e = banco[eid]
    videos = []
    for canal, directorio, registro in (
        ("facebook", REELS_V2, _FBPUB / "_publicados.json"),
        ("youtube", REELS_V2_YT, REPO / "docs/operations/evidencia/publicacion_youtube_20261005/_youtube_publicados.json"),
    ):
        publicados = json.loads(registro.read_text(encoding="utf-8")) if registro.is_file() else {}
        if eid in publicados:  # no convertir una publicación histórica en un candidato nuevo
            continue
        rutas = sorted(directorio.glob("%s_*.mp4" % eid))
        if canal == "facebook" and mp4_path:
            rutas = [Path(mp4_path).resolve()]
        if len(rutas) > 1:
            return {"ok": False, "error": "más de una versión para %s/%s" % (eid, canal)}
        if rutas and rutas[0].is_file():
            videos.append({"formato": "reel_" + canal, "archivo": str(rutas[0].resolve()),
                           "canal": canal, "etiqueta_ia": canal == "facebook", "clave_legado": eid})
    if not videos:
        return {"ok": False, "error": "sin variantes nuevas disponibles (ausentes o ya publicadas)"}
    carpeta = REPO / "guiones/reels_revision" / eid
    carpeta.mkdir(parents=True, exist_ok=True)
    manifiesto = carpeta / "publicacion.json"
    pub = {"tema": e["titulo"], "titulo_youtube": e["titulo"],
           "descripcion": "\n\n".join(x for x in (e.get("caption"), e.get("narracion")) if x),
           "origen": "reel_python", "videos": videos}
    if not pub["descripcion"]:
        return {"ok": False, "error": "reel sin texto de publicación"}
    # Conservar el manifiesto revisado; cambios editoriales requieren revisión explícita.
    if manifiesto.exists():
        previo = json.loads(manifiesto.read_text(encoding="utf-8"))
        if any(previo.get(k) != pub[k] for k in ("tema", "titulo_youtube", "descripcion")):
            return {"ok": False, "error": "el texto cambió: revisar el manifiesto existente"}
    else:
        manifiesto.write_text(json.dumps(pub, ensure_ascii=False, indent=2), encoding="utf-8")
    conn = db.conectar(db.RUTA_PRODUCCION, produccion=True)
    try:
        db.crear_esquema(conn)
        en = ab._ahora()
        canales = {v["canal"] for v in videos}
        resultados = [ab.enviar_para_aprobar(conn, pid, destinatario=chat_id, en=en)
                      for pid, v in ab.registrar(conn, carpeta, en=en) if v["canal"] in canales]
        return {"ok": bool(resultados) and all(r["ok"] for r in resultados), "resultados": resultados}
    finally:
        conn.close()


# ── handler de callbacks (corre dentro del bot) ────────────────────────────
async def _editar(q, texto: str, quitar_botones: bool = False) -> None:
    markup = None if quitar_botones else q.message.reply_markup
    try:
        await q.edit_message_caption(caption=texto[:1024], reply_markup=markup)
    except Exception:
        try:
            await q.edit_message_text(text=texto[:4000], reply_markup=markup)
        except Exception:
            pass


async def on_callback(update, context) -> None:
    q = update.callback_query
    chat_id = update.effective_chat.id if update.effective_chat else None
    cfg = context.bot_data.get("config")
    allowed = list(getattr(cfg, "allowed_chat_ids", []) or [])
    if allowed and chat_id not in allowed:
        await q.answer("No autorizado", show_alert=True)
        return
    await q.answer()
    data = q.data or ""
    if not data.startswith("rv|"):
        return
    try:
        _, accion, eid = data.split("|", 2)
    except ValueError:
        return

    base = q.message.caption if q.message and q.message.caption else (q.message.text if q.message else "")
    base = base or ("Reel %s" % eid)

    if accion == "no":
        await _editar(q, base.replace("Estado: PENDIENTE", "Estado: DESCARTADO") + "\n\n🗑 DESCARTADO", quitar_botones=True)
        return

    if accion in ("fb", "yt", "both"):
        # Etapa 1: un boton viejo (de un mensaje anterior) ya no publica. Se decide AQUI, en
        # el servidor, no solo ocultando el boton.
        await _editar(q, base + "\n\n🔒 " + BLOQUEO, quitar_botones=True)
        return

    await _editar(q, base.replace("Estado: PENDIENTE", "Estado: publicando…") + "\n\n⏳ publicando (%s)…" % accion, quitar_botones=True)

    loop = asyncio.get_event_loop()
    lineas = []
    if accion in ("fb", "both"):
        r = await loop.run_in_executor(None, _fb_publicar, eid)
        lineas.append(_fmt("FACEBOOK", r))
    if accion in ("yt", "both"):
        r = await loop.run_in_executor(None, _yt_publicar, eid)
        lineas.append(_fmt("YOUTUBE", r))

    cabecera = base.split("\n\n")[0]
    final = "%s\n\nResultado (%s):\n%s" % (cabecera, time.strftime("%H:%M:%S"), "\n".join(lineas))
    await _editar(q, final, quitar_botones=True)


def _build_handlers():
    from telegram.ext import CallbackQueryHandler
    return [CallbackQueryHandler(on_callback, pattern=r"^rv\|")]


REELS_HANDLERS = _build_handlers()
