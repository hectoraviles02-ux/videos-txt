# -*- coding: utf-8 -*-
"""Criterios de aceptacion D1 (subtitulos de narracion) y D2 (transiciones) sobre inputProps.

D1: por cada ventana narracion_tts del plan: las lineas de subtitulo que caen en ella, unidas,
    reproducen EXACTAMENTE UNA VEZ el texto de pantalla del beat (en orden); ninguna sale de su
    ventana; ningun subtitulo (de narracion o literal) se encima en tiempo con otro.
D2: los planos son contiguos sin hueco ni solape (fromF+durF == siguiente.fromF) y, si el plan
    declara transiciones por capitulo, solo hay fundido en cambios de capitulo / inicio / fin.
Solo lee; devuelve listas de errores."""
import re


def _n(s):
    return re.sub(r"\s+", " ", s or "").strip()


def errores_d1(props, plan, guion, fps=30):
    beats = {b["id"]: b for b in guion["beats"]}
    subs = sorted(props["subtitles"], key=lambda s: s["fromF"])
    e = []
    for a, b in zip(subs, subs[1:]):
        if b["fromF"] < a["fromF"] + a["durF"]:
            e.append(f"subtitulos encimados en frame {b['fromF']}: '{a['text'][:25]}' / '{b['text'][:25]}'")
    for au in plan["pista_audio"]:
        if au["tipo"] != "narracion_tts":
            continue
        bt = beats[au["beat_id"]]
        if not bt.get("mostrar_subtitulo_narracion", True):
            continue
        i, o = round(au["in"] * fps), round(au["out"] * fps)
        dentro = [s for s in subs if i <= s["fromF"] < o]
        fuera = [s for s in dentro if s["fromF"] + s["durF"] > o]
        if fuera:
            e.append(f"{au['beat_id']}: {len(fuera)} subtitulo(s) se salen de la ventana")
        esperado = _n(bt.get("texto_pantalla") or bt.get("texto"))
        obtenido = _n(" ".join(s["text"] for s in dentro))
        if obtenido != esperado:
            veces = obtenido.count(esperado[:40]) if esperado else 0
            e.append(f"{au['beat_id']}: texto de subtitulos != texto del beat (inicio aparece {veces} veces)")
    return e


def errores_d2(props, plan, guion):
    vis = sorted(props["visual"], key=lambda p: p["fromF"])
    e = []
    for a, b in zip(vis, vis[1:]):
        if a["fromF"] + a["durF"] != b["fromF"]:
            e.append(f"planos {a['plano']}->{b['plano']}: no contiguos ({a['fromF'] + a['durF']} != {b['fromF']})")
    if plan.get("transiciones") == "capitulos":
        for a, b in zip(vis, vis[1:]):
            cambio = a.get("capitulo") != b.get("capitulo")
            if a.get("fundidoSalida", True) != cambio or b.get("fundidoEntrada", True) != cambio:
                e.append(f"planos {a['plano']}->{b['plano']}: fundido={a.get('fundidoSalida', True)} pero "
                         f"cambio de capitulo={cambio} ({a.get('capitulo')}->{b.get('capitulo')})")
    return e
