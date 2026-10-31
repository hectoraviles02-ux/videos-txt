# -*- coding: utf-8 -*-
"""Versión TikTok de los reels de Python: MISMO render v2 (misma voz Kokoro em_alex, mismo código y look) pero
SIN música de fondo. Motivo (17-oct-2026): la pista de reels_v2_yt viene de la Biblioteca de audio de YouTube y
ninguna fuente extraída confirma su licencia para TikTok (ver docs/operations/ADECUACION_MONETIZACION.md, B4).
En TikTok la música se agrega, si se quiere, desde su propia biblioteca.

Uso: python render_reels_tiktok.py e11 e12 ...   (sin ids: los de _tiktok_programados.json + _tiktok_cola.json)
Salida: guiones/reels_python/2026-10-17_tiktok_sin_musica/<id>_<slug>_v3tt.mp4 (no toca reels_v2 ni reels_v2_yt).
v3tt (17-oct-2026): la voz recibe synthesis.texto_locucion.aplicar_lexico_codigo(narracion) («Páiton», «réinch»…);
el texto en pantalla sigue saliendo de la narración del banco. Solo se renderizan los reels cuya voz cambia:
los demás conservan su _v2tt (ya programado en TikTok)."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
POC = REPO / "docs/operations/evidencia/poc_terminal_shorts_20261005"
TT = REPO / "docs/operations/evidencia/publicacion_tiktok_20261017"
OUT = REPO / "guiones/reels_python/2026-10-17_tiktok_sin_musica"
sys.path.insert(0, str(POC)); sys.path.insert(0, str(REPO))
os.environ["VIDEOS_TXT_TELEGRAM_ENABLED"] = "false"

import generar_reels as g  # noqa: E402
import generar_reels_v2 as v2  # noqa: E402
from synthesis.texto_locucion import aplicar_lexico_codigo  # noqa: E402

OUT.mkdir(parents=True, exist_ok=True)
v2.REELS_V2 = OUT
v2.MUSIC_DIR = OUT / "_sin_musica"          # carpeta vacía → mezclar_musica() no encuentra pista → solo voz
v2.MUSIC_DIR.mkdir(exist_ok=True)


def ids_por_defecto():
    ids = list(json.loads((TT / "_tiktok_programados.json").read_text(encoding="utf-8")))
    ids += [c["id"] for c in json.loads((TT / "_tiktok_cola.json").read_text(encoding="utf-8"))]
    return [i for i in dict.fromkeys(ids) if i.startswith("e")]


def main():
    ids = sys.argv[1:] or ids_por_defecto()
    banco = {e["id"]: e for e in json.loads((POC / "_banco.json").read_text(encoding="utf-8"))}
    from pipeline.providers.kokoro_tts import KokoroTTSProvider  # misma voz que los reels publicados
    prov = KokoroTTSProvider(velocidad=g.SPEED)
    for eid in ids:
        ej = banco.get(eid)
        if not ej or ej.get("estado") != "OK":
            print("SALTO %s (no está OK en el banco)" % eid, flush=True); continue
        voz = aplicar_lexico_codigo(ej["narracion"])
        if voz == ej["narracion"]:
            print("SIN CAMBIO %s (se queda su _v2tt)" % eid, flush=True); continue
        vid = OUT / ("%s_%s_v3tt.mp4" % (eid, ej["titulo"].lower().replace(" ", "_")))
        if vid.is_file():
            print("YA %s" % vid.name, flush=True); continue
        t0 = time.time()
        raw = OUT / ("_%s_raw.wav" % eid); mast = OUT / ("_%s.wav" % eid)
        sdur, total = v2.narracion_v2(prov, voz, raw, mast)
        assert v2.mezclar_musica(mast, total, OUT / "_x.wav") is None, "no debe haber música"
        tmp = vid.with_suffix(".part.mp4")
        v2.render_reel_v2(ej, v2.LEAD, sdur, total, mast, tmp)
        tmp.replace(vid)
        raw.unlink(missing_ok=True); mast.unlink(missing_ok=True)
        print("OK %s (%.0fs)" % (vid.name, time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
