# -*- coding: utf-8 -*-
"""Lote nocturno de reels de Python (nivel intermedio) — pedido de el operador el 22-oct-2026:
«haz videos de python para intermedios, quiero que toda la noche te la pases haciendo estos videos».

Reusa lo que ya existe, igual que la ruta manual de scripts/ciclo_diario.py:
  telegram_bot.producir_reel.producir(tema)  -> código EJECUTADO (salida real) + voz + render FB/YT
  scripts/verificar_narracion_reel.verificar -> la narración contra el código y su salida
  telegram_bot.reels_review.enviar_reel_con_botones -> a Telegram con APROBAR (nada se publica solo)

Reglas:
- Toma el candado de la fábrica SOLO durante cada reel y espera entre reels: si el operador manda una URL al
  bot, el bot puede tomar la fábrica en la pausa.
- Narración con FALLA: no se manda con botón; solo un aviso con el motivo.
- Para detenerlo: crear el archivo logs/DETENER_LOTE_PYTHON (o cerrar el proceso).
Uso: python scripts/lote_python.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from review_channels.telegram_channel import _load_env  # noqa: E402

_load_env()
from telegram_bot import candado  # noqa: E402
from telegram_bot import producir_reel as pr  # noqa: E402
from telegram_bot import reels_review as rr  # noqa: E402
from verificar_narracion_reel import verificar  # noqa: E402

LOG = REPO / "logs" / ("lote_python_%s.log" % datetime.now().strftime("%Y%m%d"))
DETENER = REPO / "logs" / "DETENER_LOTE_PYTHON"
PAUSA_S = 60

TEMAS = [
    "generadores con yield para producir una secuencia bajo demanda",
    "decorador simple que mide cuántas veces se llama una función",
    "closures: una función que recuerda un contador",
    "*args y **kwargs para una función flexible",
    "sorted con key y lambda para ordenar diccionarios por dos criterios",
    "collections.Counter para contar las palabras más frecuentes",
    "collections.defaultdict para agrupar elementos por categoría",
    "namedtuple para representar puntos con nombre",
    "dataclasses para una clase Producto con valores por defecto",
    "itertools.groupby para agrupar elementos consecutivos",
    "itertools.combinations para todas las parejas posibles",
    "functools.lru_cache para acelerar fibonacci recursivo",
    "desempaquetado extendido con asterisco en listas",
    "operador morsa := dentro de un while o comprehension",
    "comprehension de diccionario invirtiendo llaves y valores",
    "comprehension anidada para aplanar una matriz",
    "slicing con paso negativo para invertir y saltar elementos",
    "any y all para validar una lista de condiciones",
    "try except else finally y en qué orden se ejecutan",
    "context manager con una clase y __enter__ __exit__",
    "__repr__ y __str__ en una clase propia",
    "property para validar un atributo de una clase",
    "herencia y super() para extender un método",
    "classmethod como constructor alternativo",
    "iterador propio con __iter__ y __next__",
    "dict.get y setdefault para evitar KeyError",
    "unir diccionarios con el operador |",
    "match case para clasificar comandos",
    "bisect para insertar en una lista ordenada",
    "heapq para obtener los tres valores más grandes",
    "copy frente a deepcopy con listas anidadas",
    "is frente a == con listas iguales",
    "el error del argumento por defecto mutable",
    "expresión generadora frente a lista en sum",
    "enumerate con start y zip para recorrer dos listas",
    "nonlocal para modificar una variable de la función externa",
    "str.join frente a concatenar en un ciclo",
    "f-strings con formato de ancho, decimales y separador de miles",
    "map y filter comparados con una comprehension",
    "sets: intersección, unión y diferencia entre dos listas",
]


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    linea = "%s %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(linea + "\n")
    print(linea, flush=True)


def telegram(texto: str) -> None:
    from telegram_bot import aprobacion_botones as ab
    import os
    try:
        ab._telegram("sendMessage", {"chat_id": os.environ["VIDEOS_TXT_TELEGRAM_CHAT_ID"].strip(),
                                     "text": texto[:4000], "disable_web_page_preview": "true"})
    except Exception as e:  # noqa: BLE001 - un aviso fallido no detiene el lote
        log("aviso Telegram falló: %s" % e)


def un_reel(tema: str) -> dict:
    t0 = time.time()
    with candado.tomar("lote-python") as libre:
        if not libre:
            return {"ocupado": candado.quien()}
        r = pr.producir("%s (nivel intermedio)" % tema)
        if not r.get("eid"):
            return {"error": r.get("error"), "segundos": round(time.time() - t0)}
        e = {x["id"]: x for x in json.loads(pr.BANCO.read_text(encoding="utf-8"))}[r["eid"]]
        juez = verificar(e["code"], e["output"], e["narracion"])
    return {**r, "juez": juez, "guion": e, "segundos": round(time.time() - t0)}


def main() -> int:
    hechos, fallas, errores = 0, 0, 0
    log("=== INICIO lote Python intermedio: %d temas" % len(TEMAS))
    telegram("🌙 Arranqué el lote nocturno de Python (nivel intermedio): %d temas. Cada reel te llega con "
             "botones; nada se publica solo. Si le mandas una URL al bot y dice «fábrica ocupada», "
             "reenvíala en 3-4 min: el lote deja la fábrica libre entre reel y reel." % len(TEMAS))
    for i, tema in enumerate(TEMAS, 1):
        if DETENER.exists():
            log("DETENIDO por archivo %s" % DETENER)
            break
        for _ in range(60):                       # si el bot está ocupado (URL de el operador), espera
            if not candado.ocupado():
                break
            time.sleep(30)
        r = un_reel(tema)
        if r.get("ocupado"):
            log("OCUPADO (%s): salto «%s»" % (r["ocupado"], tema))
            errores += 1
        elif r.get("error"):
            errores += 1
            log("FALLO %d/%d «%s»: %s" % (i, len(TEMAS), tema, str(r["error"])[:160]))
        else:
            j, e = r["juez"], r["guion"]
            log("OK %d/%d %s «%s» juez=%s (%ds)" % (i, len(TEMAS), r["eid"], r["titulo"], j["veredicto"], r["segundos"]))
            if j["veredicto"] == "FALLA":
                fallas += 1
                telegram("⛔ %s «%s» NO te lo mando con botón: la narración no coincide con el código.\n"
                         "Motivo: %s\nNarración: %s\nSalida real: %s"
                         % (r["eid"], r["titulo"], j["motivo"], e["narracion"], e["output"][:300]))
            else:
                env = rr.enviar_reel_con_botones(r["eid"])
                hechos += 1
                telegram("🐍 %d/%d · %s «%s» — juez: %s\nNarración: %s\nSalida real: %s\n"
                         "Revisa que la voz diga lo mismo que la salida antes de aprobar."
                         % (i, len(TEMAS), r["eid"], r["titulo"], j["veredicto"], e["narracion"], e["output"][:300]))
                if not env.get("ok", True):
                    log("  envío con botones: %s" % env)
        time.sleep(PAUSA_S)
    resumen = "🌅 Lote Python terminado: %d enviados para revisar, %d frenados por narración, %d fallos." % (
        hechos, fallas, errores)
    log("=== FIN " + resumen)
    telegram(resumen)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
