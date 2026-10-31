# -*- coding: utf-8 -*-
"""Manda a Telegram reels de Python que nunca salieron en Facebook, con botón APROBAR solo para Facebook
(31-oct-2026, operador: «los de python súbelos en cascada a Facebook»).

Elige reels con publicacion.json en guiones/reels_revision/, que no están en _publicados.json de Facebook,
que el juez de la fábrica marcó OK y que no se mandaron ya por este script (_enviados_fb.json).
NO aprueba ni publica nada: cada reel sale solo cuando operador aprieta su botón (R27).
Uso: python scripts/enviar_lote_facebook.py [--n 20] [--solo-mostrar]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from scripts import enviar_lote_diario as lote  # noqa: E402  (carga .env y reutiliza el juez)
from content_factory import db  # noqa: E402
from telegram_bot import aprobacion_botones as ab  # noqa: E402

FB = REPO / "docs/operations/evidencia/publicacion_fb_20261005/_publicados.json"
ENVIADOS = REPO / "docs/operations/evidencia/lote_diario_20261029/_enviados_fb.json"


def candidatos() -> list[str]:
    banco = [e["id"] for e in json.loads((lote.POC / "_banco.json").read_text(encoding="utf-8"))]
    juez = lote.veredictos()
    enviados = json.loads(ENVIADOS.read_text(encoding="utf-8")) if ENVIADOS.is_file() else {}
    # solo cuentan como enviados los que Telegram aceptó: un envío fallido vuelve a ser candidato
    fuera = set(json.loads(FB.read_text(encoding="utf-8"))) | {k for k, v in enviados.items() if v.get("facebook")}
    return [e for e in banco if e not in fuera and juez.get(e) == "OK"
            and (REPO / "guiones/reels_revision" / e / "publicacion.json").is_file()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--solo-mostrar", action="store_true")
    a = ap.parse_args()
    lista = candidatos()
    print("candidatos:", len(lista), "· lote:", lista[:a.n])
    if a.solo_mostrar:
        return 0
    chat = os.environ.get("VIDEOS_TXT_TELEGRAM_CHAT_ID", "").strip()
    reg = json.loads(ENVIADOS.read_text(encoding="utf-8")) if ENVIADOS.is_file() else {}
    conn = db.conectar(db.RUTA_PRODUCCION, produccion=True)
    db.crear_esquema(conn)
    try:
        for eid in lista[:a.n]:
            en = ab._ahora()
            res = [ab.enviar_para_aprobar(conn, pid, destinatario=chat, en=en)
                   for pid, v in ab.registrar(conn, REPO / "guiones/reels_revision" / eid, en=en)
                   if v["canal"] == "facebook"]
            ok = bool(res) and all(r["ok"] for r in res)
            reg[eid] = {"enviado_en": datetime.now().isoformat(timespec="seconds"), "facebook": ok}
            print(eid, "Facebook", "ok" if ok else res)
    finally:
        conn.close()
        ENVIADOS.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
