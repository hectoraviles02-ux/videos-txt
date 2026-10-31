# -*- coding: utf-8 -*-
"""Manda a Telegram el lote del día: 6 reels de Python ya hechos, con botón APROBAR para TikTok y para YouTube
(29-oct-2026, decisión de operador: «frenar la fábrica» y «6 al día a Telegram», porque hay ~257 reels sin
publicar y TikTok castiga la 7.ª publicación en 24 h).

Elige reels que:
- tienen su MP4 en reels_v2_yt/;
- no están en TikTok ni en YouTube (registros _tiktok_*.json y _youtube_publicados.json);
- el juez de la fábrica marcó OK (último veredicto en logs/*.log); sin veredicto o FALLA/REVISAR, no entra;
- no se mandaron ya en un lote anterior (_enviados.json).
Primero los más sencillos (menos líneas de código).

NO aprueba ni publica nada: solo manda los botones (R27). Los códigos caducan a las 72 h.
Uso: python scripts/enviar_lote_diario.py [--n 6] [--solo-mostrar]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from review_channels.telegram_channel import _load_env  # noqa: E402

_load_env()
from content_factory import db  # noqa: E402
from telegram_bot import aprobacion_botones as ab  # noqa: E402
from scripts import enviar_tiktok_pendientes as tt  # noqa: E402

POC = REPO / "docs/operations/evidencia/poc_terminal_shorts_20261005"
YT = REPO / "docs/operations/evidencia/publicacion_youtube_20261005/_youtube_publicados.json"
ENVIADOS = REPO / "docs/operations/evidencia/lote_diario_20261029/_enviados.json"


def veredictos() -> dict[str, str]:
    """Último veredicto del juez por reel, leído de las bitácoras en orden de fecha."""
    v = {}
    for log in sorted((REPO / "logs").glob("*.log"), key=lambda p: p.stat().st_mtime):
        for m in re.finditer(r"\b(e\d+) «[^»]*» juez=([A-Z_]+)", log.read_text(encoding="utf-8", errors="replace")):
            v[m.group(1)] = m.group(2)
    return v


def ya_en_youtube() -> set[str]:
    ya = set()
    if YT.is_file():
        for k in json.loads(YT.read_text(encoding="utf-8")):
            m = re.search(r"\b(e\d+)", k)
            ya.add(m.group(1) if m else k)
    return ya


def enviados() -> dict:
    return json.loads(ENVIADOS.read_text(encoding="utf-8")) if ENVIADOS.is_file() else {}


def candidatos() -> list[str]:
    banco = {e["id"]: e for e in json.loads((POC / "_banco.json").read_text(encoding="utf-8"))}
    juez, fuera = veredictos(), tt.ya_en_tiktok() | ya_en_youtube() | set(enviados())
    ids = [e for e in tt.pendientes() if e not in fuera and juez.get(e) == "OK" and e in banco
           and (REPO / "guiones/reels_revision" / e / "publicacion.json").is_file()]
    # 30-oct: primero los de la vida diaria (en YouTube sacan 228-359 vistas contra 28-35 de los conceptos).
    ids.sort(key=lambda e: (not cotidiano(banco[e]["titulo"]), len(banco[e]["code"]), int(e[1:])))
    # Un tema por lote y nada que repita un tema ya publicado, programado o enviado («INSERTA ORDENADO» = «INSERTAR ORDENADO»).
    # Dentro del mismo lote, tampoco dos que compartan palabra («FORMATEA NÚMEROS» y «FORMATO DINÁMICO»).
    vistos = {tema(banco[e]["titulo"]) for e in fuera if e in banco}
    usados = {k for e in fuera if e in banco for k in cotidiano(banco[e]["titulo"])}   # «SEGUNDOS VIVOS» = «SEGUNDOS DE VIDA»
    salida, palabras = [], set()
    for e in ids:
        t = tema(banco[e]["titulo"])
        if t not in vistos and not (cotidiano(banco[e]["titulo"]) & usados):
            vistos.add(t)
            salida.append(e)
    lote, resto = [], []
    for e in salida:
        t = set(tema(banco[e]["titulo"]))
        (resto if t & palabras else lote).append(e)
        if e in lote:
            palabras |= t
    return lote + resto


# Inicio de palabra → grupo. Mismo grupo = mismo tema («SEGURA CONTRASEÑA» = «PASS SEGURA»).
COTIDIANO = {"ahorr": "ahorro", "supermerc": "compras", "compras": "compras", "precio": "compras", "descuent": "compras",
             "pesos": "dinero", "dinero": "dinero", "propina": "dinero", "gasto": "dinero", "pago": "dinero",
             "sorteo": "sorteo", "calific": "calificacion", "segundos": "edad", "edad": "edad", "contrase": "pass",
             "pass": "pass", "correo": "correo", "tabla": "multiplicar", "multiplic": "multiplicar", "piedra": "juego",
             "adivin": "juego", "dado": "juego", "garraf": "garrafon"}


def cotidiano(titulo: str) -> set:
    palabras = re.findall(r"\w+", titulo.lower())
    return {g for k, g in COTIDIANO.items() for p in palabras if p.startswith(k)}


def tema(titulo: str) -> tuple:
    return tuple(sorted(p[:5] for p in re.findall(r"\w+", titulo.lower()) if len(p) > 2))


def enviar_youtube(eid: str, conn, chat: str) -> dict:
    carpeta = REPO / "guiones/reels_revision" / eid
    en = ab._ahora()
    res = [ab.enviar_para_aprobar(conn, pid, destinatario=chat, en=en)
           for pid, v in ab.registrar(conn, carpeta, en=en) if v["canal"] == "youtube"]
    return {"ok": bool(res) and all(r["ok"] for r in res), "n": len(res)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--solo-mostrar", action="store_true")
    a = ap.parse_args()
    lista = candidatos()
    print("candidatos:", len(lista), "· lote:", lista[:a.n])
    if a.solo_mostrar:
        return 0
    chat = os.environ.get("VIDEOS_TXT_TELEGRAM_CHAT_ID", "").strip()
    reg = enviados()
    conn = db.conectar(db.RUTA_PRODUCCION, produccion=True)
    db.crear_esquema(conn)
    try:
        for eid in lista[:a.n]:
            r_tt, r_yt = tt.enviar(eid, conn, chat), enviar_youtube(eid, conn, chat)
            reg[eid] = {"enviado_en": datetime.now().isoformat(timespec="seconds"),
                        "tiktok": r_tt.get("ok"), "youtube": r_yt.get("ok")}
            print(eid, "TikTok", "ok" if r_tt.get("ok") else r_tt, "· YouTube", "ok" if r_yt.get("ok") else r_yt)
    finally:
        conn.close()
        ENVIADOS.parent.mkdir(parents=True, exist_ok=True)
        ENVIADOS.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
