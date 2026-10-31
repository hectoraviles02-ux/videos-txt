# -*- coding: utf-8 -*-
"""Manda a Telegram, con botón «APROBAR y publicar en TikTok», cada reel que todavía no está en TikTok
(22-oct-2026, pedido de el operador: «sube todos los videos que están pendientes de la fábrica a TikTok»).

Pendiente = tiene versión sin música en reels_v2_yt/ y no aparece en _tiktok_publicados.json ni en
_tiktok_programados.json. NO sube nada: sube el botón, cuando el operador lo aprieta (R27).
Uso: python scripts/enviar_tiktok_pendientes.py [e108 e109 ...]   (sin ids: todos los pendientes)
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from review_channels.telegram_channel import _load_env  # noqa: E402

_load_env()
from content_factory import db  # noqa: E402
from telegram_bot import aprobacion_botones as ab  # noqa: E402

POC = REPO / "docs/operations/evidencia/poc_terminal_shorts_20261005"
TT = REPO / "docs/operations/evidencia/publicacion_tiktok_20261017"
HASHTAGS = "#python #programacion #aprendepython #codigo"


def ya_en_tiktok() -> set[str]:
    ya = set()
    for f in ("_tiktok_publicados.json", "_tiktok_programados.json"):
        p = TT / f
        if p.is_file():
            for k in json.loads(p.read_text(encoding="utf-8")):
                m = re.search(r"\b(e\d+)", k)
                ya.add(m.group(1) if m else k)
    return ya


def pendientes() -> list[str]:
    ids = {re.match(r"(e\d+)_", p.name).group(1) for p in (POC / "reels_v2_yt").glob("e*.mp4")}
    return sorted(ids - ya_en_tiktok(), key=lambda x: int(x[1:]))


def enviar(eid: str, conn, chat: str) -> dict:
    banco = {e["id"]: e for e in json.loads((POC / "_banco.json").read_text(encoding="utf-8"))}
    if eid not in banco:
        return {"ok": False, "error": "sin guion en el banco"}
    rutas = sorted((POC / "reels_v2_yt").glob("%s_*.mp4" % eid))
    if len(rutas) != 1:
        return {"ok": False, "error": "%d versiones sin música" % len(rutas)}
    e = banco[eid]
    carpeta = REPO / "guiones/reels_revision" / ("%s_tiktok" % eid)
    carpeta.mkdir(parents=True, exist_ok=True)
    manifiesto = carpeta / "publicacion.json"
    pub = {"tema": e["titulo"], "titulo_youtube": e["titulo"],
           "descripcion": "%s\n\n%s\n\n%s" % (e.get("caption") or e["titulo"], e.get("narracion", ""), HASHTAGS),
           "origen": "reel_python",
           "videos": [{"formato": "reel_tiktok", "archivo": str(rutas[0].resolve()), "canal": "tiktok",
                       "etiqueta_ia": True, "clave_legado": eid}]}   # voz sintética: TikTok pide etiqueta IA
    if not manifiesto.exists():
        manifiesto.write_text(json.dumps(pub, ensure_ascii=False, indent=2), encoding="utf-8")
    en = ab._ahora()
    resultados = [ab.enviar_para_aprobar(conn, pid, destinatario=chat, en=en)
                  for pid, v in ab.registrar(conn, carpeta, en=en) if v["canal"] == "tiktok"]
    return {"ok": bool(resultados) and all(r["ok"] for r in resultados), "n": len(resultados)}


def main(ids: list[str]) -> int:
    chat = os.environ.get("VIDEOS_TXT_TELEGRAM_CHAT_ID", "").strip()
    ids = ids or pendientes()
    conn = db.conectar(db.RUTA_PRODUCCION, produccion=True)
    db.crear_esquema(conn)
    try:
        for eid in ids:
            r = enviar(eid, conn, chat)
            print(eid, "enviado con botón TikTok" if r["ok"] else r)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main(sys.argv[1:]))
