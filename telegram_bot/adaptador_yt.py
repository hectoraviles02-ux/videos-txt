# -*- coding: utf-8 -*-
"""Adaptador mínimo YouTube: publica UNA pieza reutilizando subir_lote_youtube.

NO reimplementa el publicador. Dedup por _youtube_publicados.json ANTES de publicar.
Clasifica: PUBLICADO | YA_PUBLICADO | LIMITE (uploadLimitExceeded) | ERROR.
Cero reintentos.
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_YTPUB = REPO / "docs/operations/evidencia/publicacion_youtube_20261005"
if str(_YTPUB) not in sys.path:
    sys.path.insert(0, str(_YTPUB))


def _ruta_bd_compuerta():
    return None   # None = la cola real (content_factory/content.db)


def _compuerta(ruta, texto, canal, programada_para=None):
    """Etapa 1 (21-oct-2026): no se sube nada sin aprobacion vigente de operador en la cola
    (SHA-256 del archivo + texto + canal + horario). Lanza PublicacionBloqueada."""
    import sys as _sys
    from datetime import datetime as _dt
    _raiz = str(Path(__file__).resolve().parents[1])
    if _raiz not in _sys.path:
        _sys.path.insert(0, _raiz)
    from content_factory import guardia_publicacion
    return guardia_publicacion.exigir(ruta, texto, canal, programada_para=programada_para,
                                      en=_dt.now().astimezone().isoformat(), ruta_bd=_ruta_bd_compuerta())


def publicar_youtube(eid: str) -> dict:
    import subir_lote_youtube as lote   # reusa meta, archivo_de, oembed_publico, BANCO, YTDIR, LOG, yt_mod

    pub = json.loads(lote.LOG.read_text(encoding="utf-8")) if lote.LOG.is_file() else {}
    # IDEMPOTENCIA: comprobar ANTES de publicar
    if eid in pub:
        return {"estado": "YA_PUBLICADO", "url": pub[eid].get("url"), "video_id": pub[eid].get("video_id")}

    f = lote.archivo_de(eid)
    if not f:
        return {"estado": "ERROR", "detalle": "no hay archivo v2-yt para %s" % eid}

    banco = {e["id"]: e for e in json.loads(lote.BANCO.read_text(encoding="utf-8"))}
    titulo, desc = lote.meta(eid, banco)
    try:
        _compuerta(f, desc, "youtube")
    except Exception as e:  # noqa: BLE001 - PublicacionBloqueada u otro: no se sube
        return {"estado": "BLOQUEADO", "detalle": str(e)[:300]}
    try:
        yt = lote.yt_mod.servicio()
        from googleapiclient.http import MediaFileUpload
        body = {"snippet": {"title": titulo, "description": desc,
                            "tags": ["python", "programacion", "shorts", "aprenderpython"], "categoryId": "27"},
                "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}
        media = MediaFileUpload(str(f), chunksize=-1, resumable=True, mimetype="video/mp4")
        resp = yt.videos().insert(part="snippet,status", body=body, media_body=media).execute()
        vid = resp["id"]
        efect = resp.get("status", {}).get("privacyStatus")
        oe = lote.oembed_publico(vid)
        url = "https://youtube.com/shorts/%s" % vid
        pub[eid] = {"video_id": vid, "canal": "youtube", "url": url, "privacy_solicitada": "public",
                    "privacy_efectiva_insert": efect, "oembed_publico": oe,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "resultado": "subido"}
        lote.LOG.write_text(json.dumps(pub, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"estado": "PUBLICADO", "url": url, "video_id": vid}
    except Exception as e:
        msg = str(e)
        if "uploadLimitExceeded" in msg or "exceeded the number of videos" in msg:
            return {"estado": "LIMITE", "detalle": "tope diario del canal (uploadLimitExceeded)"}
        return {"estado": "ERROR", "detalle": msg[:200]}
