# -*- coding: utf-8 -*-
"""Etapa 2 (21-oct-2026): mide al juez de narraciones contra casos con etiqueta conocida.

Clase POSITIVA = la narracion esta MAL (dice algo que el codigo o la salida real no respaldan).
  VP = narracion mala que el juez rechaza       FN = narracion mala que el juez deja pasar (PELIGROSO)
  VN = narracion buena que el juez deja pasar   FP = narracion buena rechazada sin razon

Ninguna prediccion del modelo se usa como etiqueta. Las etiquetas salen solo de:
  - el banco (`narracion_llm_descartada` / `narracion` con su `nota_revision` del 14-oct;
    autor NO confirmado), y
  - las respuestas de el operador a su paquete de validacion (`etiquetas_el operador.csv`).

Uso:
  python scripts/medir_juez.py correr     -> corre juez y verificador sobre todos los casos (reanudable)
  python scripts/medir_juez.py paquete    -> arma el paquete para el operador (sin mostrarle el veredicto)
  python scripts/medir_juez.py metricas   -> matrices de confusion con las etiquetas disponibles
"""
from __future__ import annotations

import csv
import json
import random
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
EVID = REPO / "docs/operations/evidencia/etapa2_calidad_juez_20261021"
CORRIDAS = EVID / "10_corridas_juez.jsonl"
PAQUETE = EVID / "20_paquete_validacion_el operador.md"
PLANTILLA = EVID / "21_etiquetas_el operador.csv"
CLAVES = EVID / "22_claves_paquete.json"
REPETICIONES_ETIQUETADOS = 3


def casos() -> list[dict]:
    """Todas las narraciones REALES del banco, mas las 4 descartadas. Etiqueta solo donde hay fuente."""
    import verificar_narracion_reel as v
    salida = []
    for e in v._banco().values():
        base = {"eid": e["id"], "titulo": e["titulo"], "code": e["code"], "output": e["output"]}
        if e.get("narracion_llm_descartada"):
            salida.append(dict(base, caso="D-" + e["id"], narracion=e["narracion_llm_descartada"],
                               etiqueta="MAL", campo="narracion_llm_descartada",
                               fuente_etiqueta="banco: nota_revision 14-oct (autor no confirmado)",
                               nota=e.get("nota_revision", "")))
            salida.append(dict(base, caso="B-" + e["id"], narracion=e["narracion"], etiqueta="BIEN",
                               campo="narracion",
                               fuente_etiqueta="banco: narracion reescrita 14-oct (autor no confirmado)",
                               nota=e.get("nota_revision", "")))
        else:
            salida.append(dict(base, caso="B-" + e["id"], narracion=e["narracion"], etiqueta=None,
                               campo="narracion", fuente_etiqueta=None, nota=""))
    return salida


def _hechas() -> Counter:
    hechas = Counter()
    if CORRIDAS.is_file():
        for linea in CORRIDAS.read_text(encoding="utf-8").splitlines():
            if linea.strip():
                hechas[json.loads(linea)["caso"]] += 1
    return hechas


def _recursos(etiqueta: str) -> dict:
    def correr(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip()
        except Exception as e:  # sin la herramienta se anota, no se inventa
            return "no disponible: %s" % e
    return {"momento": etiqueta, "ollama_ps": correr(["ollama", "ps"]),
            "gpu": correr(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                           "--format=csv,noheader"])}


def correr() -> int:
    """Corre juez (modelo) y verificador (sin modelo) por separado. Reanudable: no repite lo hecho."""
    import time
    import verificar_narracion_reel as v
    from content_factory.verificacion_numerica import verificar_numeros
    EVID.mkdir(parents=True, exist_ok=True)
    recursos = [_recursos("inicio")]
    hechas = _hechas()
    t0 = time.monotonic()
    with CORRIDAS.open("a", encoding="utf-8") as f:
        for c in casos():
            veces = REPETICIONES_ETIQUETADOS if c["etiqueta"] else 1
            for rep in range(hechas[c["caso"]], veces):
                juez = v.juez_llm(c["code"], c["output"], c["narracion"])
                mate = verificar_numeros(c["code"], c["output"], c["narracion"])
                f.write(json.dumps({"caso": c["caso"], "repeticion": rep + 1, "modelo": v.MODELO,
                                    "juez": juez, "matematica": mate}, ensure_ascii=False) + "\n")
                f.flush()
                print(c["caso"], rep + 1, juez["veredicto"], mate["veredicto"], juez["segundos"], flush=True)
                if len(recursos) == 1 and time.monotonic() - t0 > 60:
                    recursos.append(_recursos("en carga"))
    recursos.append(_recursos("fin"))
    recursos.append({"segundos_totales_esta_corrida": round(time.monotonic() - t0, 1)})
    (EVID / "11_recursos.json").write_text(json.dumps(recursos, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def _corridas() -> dict:
    por_caso: dict[str, list] = {}
    for linea in CORRIDAS.read_text(encoding="utf-8").splitlines():
        if linea.strip():
            d = json.loads(linea)
            por_caso.setdefault(d["caso"], []).append(d)
    return por_caso


def paquete(minimo: int = 30, semilla: int = 20261021) -> int:
    """Paquete CIEGO para el operador: narracion junto a la salida real, sin el veredicto del juez.

    Seleccion estratificada (para que haya casos de todos los tipos): las 8 con etiqueta del banco
    (para confirmarlas), todas las que el juez rechazo, todas las que el verificador marco, y el
    resto al azar (semilla fija) entre las que el juez dejo pasar, hasta llegar al minimo."""
    por_caso = _corridas()
    todos = {c["caso"]: c for c in casos()}
    elegidos = [k for k, c in todos.items() if c["etiqueta"]]
    rechazados = sorted(k for k, r in por_caso.items() if k not in elegidos
                        and r[0]["juez"]["veredicto"] == "FALLA")
    marcados = sorted(k for k, r in por_caso.items() if k not in elegidos and k not in rechazados
                      and r[0]["matematica"]["veredicto"] != "OK")
    elegidos += rechazados + marcados
    resto = sorted(k for k in por_caso if k not in elegidos)
    random.Random(semilla).shuffle(resto)
    elegidos += resto[:max(0, minimo - len(elegidos))]
    orden = sorted(elegidos)
    random.Random(semilla + 1).shuffle(orden)  # el numero no delata el estrato
    claves = {"%02d" % (i + 1): k for i, k in enumerate(orden)}
    estratos = {k: ("etiqueta del banco" if todos[k]["etiqueta"] else "juez rechazo" if k in rechazados
                    else "verificador marco" if k in marcados else "azar entre aprobadas") for k in orden}
    CLAVES.write_text(json.dumps({"claves": claves, "estrato": estratos, "semilla": semilla},
                                 ensure_ascii=False, indent=2), encoding="utf-8")

    lineas = ["# Paquete de validación — narraciones de reels (Etapa 2, 21-oct-2026)", "",
              "Para cada caso: lee la **voz** y la **salida real**. Responde una sola pregunta:",
              "", "> ¿La voz dice algo que **contradice** la salida real o que **no se puede comprobar** con ella?",
              "", "- **BIEN**: todo lo que dice la voz cuadra con la salida real (o es explicación general correcta).",
              "- **MAL**: la voz dice un resultado, un número o un hecho que la salida real contradice.",
              "- **NO SÉ**: la voz habla de lo que hace el código por dentro y la salida no alcanza para saberlo.",
              "", "No se muestra lo que opinó el juez: así tu respuesta no se contagia.",
              "Contesta en `21_etiquetas_el operador.csv` (columna `tu_respuesta`) o por Telegram así: `07 MAL`.", ""]
    with PLANTILLA.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["numero", "titulo", "tu_respuesta (BIEN / MAL / NO SE)", "comentario"])
        for num, k in claves.items():
            c = todos[k]
            w.writerow([num, c["titulo"], "", ""])
            lineas += ["## %s · %s" % (num, c["titulo"]), "", "**Voz:** %s" % c["narracion"], "",
                       "**Salida real:**", "", "```", c["output"].strip()[:600] or "(vacía)", "```", "",
                       "Tu respuesta: BIEN / MAL / NO SÉ", ""]
    PAQUETE.write_text("\n".join(lineas), encoding="utf-8")
    print("paquete: %d casos (%s)" % (len(claves), dict(Counter(estratos.values()))))
    return 0


def _confusion(pares) -> dict:
    """pares: (etiqueta BIEN/MAL, prediccion RECHAZA/PASA)."""
    m = Counter()
    for etiqueta, pred in pares:
        m[("VP" if pred == "RECHAZA" else "FN") if etiqueta == "MAL" else ("FP" if pred == "RECHAZA" else "VN")] += 1
    vp, fn, fp, vn = m["VP"], m["FN"], m["FP"], m["VN"]
    return {"VP": vp, "FN_peligrosos": fn, "FP_rechazo_injusto": fp, "VN": vn, "n": vp + fn + fp + vn,
            "sensibilidad": round(vp / (vp + fn), 3) if vp + fn else None,
            "especificidad": round(vn / (vn + fp), 3) if vn + fp else None}


def leer_respuestas(ruta: Path, claves: dict) -> dict:
    """Lee la plantilla aunque Excel la haya guardado a su modo: numero sin cero ("1" por "01"),
    cp1252 en vez de UTF-8, ";" en vez de ",", encabezado cambiado. La respuesta es la 3a columna.
    Devuelve {"etiquetas": {caso: BIEN|MAL}, "no_se": n, "vacias": n, "no_reconocidas": [...], "filas": n}."""
    crudo = ruta.read_bytes()
    try:
        texto = crudo.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = crudo.decode("cp1252")
    try:
        dialecto = csv.Sniffer().sniff(texto.splitlines()[0], delimiters=",;	")
    except csv.Error:
        dialecto = csv.excel
    filas = list(csv.reader(texto.splitlines(), dialecto))[1:]
    salida = {"etiquetas": {}, "no_se": 0, "vacias": 0, "no_reconocidas": [], "filas": len(filas)}
    for fila in filas:
        if not fila or not fila[0].strip():
            continue
        try:
            num = "%02d" % int(fila[0].strip())
        except ValueError:
            salida["no_reconocidas"].append(fila[0])
            continue
        r = (fila[2] if len(fila) > 2 else "").strip().upper().replace("É", "E")
        if num not in claves:
            salida["no_reconocidas"].append(num)
        elif r in ("BIEN", "MAL"):
            salida["etiquetas"][claves[num]] = r
        elif r in ("NO SE", "NOSE", "NO SÉ"):
            salida["no_se"] += 1
        elif not r:
            salida["vacias"] += 1
        else:
            salida["no_reconocidas"].append("%s=%s" % (num, r))
    return salida


def _etiquetas_el operador() -> dict:
    """Respuestas de el operador, si ya existen. NO SE y vacio no son etiqueta (se cuentan aparte)."""
    if not (PLANTILLA.is_file() and CLAVES.is_file()):
        return {"etiquetas": {}, "no_se": 0, "vacias": 0, "no_reconocidas": [], "filas": 0}
    return leer_respuestas(PLANTILLA, json.loads(CLAVES.read_text(encoding="utf-8"))["claves"])


def _desplegado(juez: str, mate: str) -> str:
    """Lo que hace HOY `verificar()`: BLOQUEO -> FALLA; si no, el modelo; un AVISO nunca sale OK."""
    if mate == "BLOQUEO":
        return "FALLA"
    if juez == "OK" and mate == "AVISO":
        return "REVISAR"
    return juez


def metricas() -> int:
    por_caso = _corridas()
    todos = {c["caso"]: c for c in casos()}
    lectura = _etiquetas_el operador()
    el operador = lectura["etiquetas"]
    fuentes = {"banco_14oct (autor no confirmado)": {k: c["etiqueta"] for k, c in todos.items() if c["etiqueta"]},
               "el operador": el operador}
    informe = {"clase_positiva": "narracion MAL", "fuentes": {},
               "lectura_csv_el operador": {k: v for k, v in lectura.items() if k != "etiquetas"} |
                                     {"etiquetas_validas": len(el operador)}}
    for nombre, etiquetas in fuentes.items():
        if not etiquetas:
            informe["fuentes"][nombre] = "sin etiquetas todavia"
            continue
        bloque = {}
        for rep in range(1, REPETICIONES_ETIQUETADOS + 1):
            pares, caidos = [], 0
            for k, et in etiquetas.items():
                corr = [r for r in por_caso.get(k, []) if r["repeticion"] == rep]
                if not corr:
                    continue
                if corr[0]["juez"].get("juez_caido"):
                    caidos += 1
                    continue
                pares.append((et, "RECHAZA" if corr[0]["juez"]["veredicto"] == "FALLA" else "PASA"))
            if pares or caidos:
                bloque["juez_modelo_repeticion_%d" % rep] = dict(_confusion(pares), juez_caido_aparte=caidos)
        pares_v, pares_c = [], []
        for k, et in etiquetas.items():
            corr = por_caso.get(k)
            if not corr:
                continue
            mate = corr[0]["matematica"]["veredicto"]
            pares_v.append((et, "RECHAZA" if mate == "BLOQUEO" else "PASA"))
            juez = corr[0]["juez"]["veredicto"]
            pares_c.append((et, "RECHAZA" if mate == "BLOQUEO" or juez == "FALLA" else "PASA"))
        bloque["verificador_numeros_solo_BLOQUEO"] = _confusion(pares_v)
        bloque["combinado_cualquiera_rechaza_repeticion_1"] = _confusion(pares_c)
        # Regla DESPLEGADA: pasa solo lo que sale OK; REVISAR y FALLA no pasan solos.
        tres = Counter()
        pares_d = []
        for k, et in etiquetas.items():
            corr = por_caso.get(k)
            if corr:
                d = _desplegado(corr[0]["juez"]["veredicto"], corr[0]["matematica"]["veredicto"])
                tres[(et, d)] += 1
                pares_d.append((et, "PASA" if d == "OK" else "RECHAZA"))
        bloque["desplegado_verificar_repeticion_1"] = _confusion(pares_d)
        bloque["desplegado_tres_salidas"] = {"%s->%s" % k: n for k, n in sorted(tres.items())}
        bloque["detalle"] = [{"caso": k, "etiqueta": et,
                              "juez": [r["juez"]["veredicto"] for r in por_caso.get(k, [])],
                              "numeros": (por_caso.get(k) or [{"matematica": {"veredicto": "-"}}])[0]["matematica"]["veredicto"]}
                             for k, et in sorted(etiquetas.items())]
        informe["fuentes"][nombre] = bloque
    seg = [r["juez"]["segundos"] for rs in por_caso.values() for r in rs]
    informe["sin_etiqueta"] = {
        "casos": sum(1 for k in por_caso if not todos.get(k, {}).get("etiqueta") and k not in el operador),
        "juez_rechaza": sum(1 for k, rs in por_caso.items() if not todos[k]["etiqueta"] and rs[0]["juez"]["veredicto"] == "FALLA"),
        "verificador": dict(Counter(rs[0]["matematica"]["veredicto"] for k, rs in por_caso.items() if not todos[k]["etiqueta"])),
        "juez_caido": sum(1 for rs in por_caso.values() for r in rs if r["juez"].get("juez_caido"))}
    informe["tiempo"] = {"llamadas_juez": len(seg), "segundos_total": round(sum(seg), 1),
                         "segundos_por_narracion_media": round(sum(seg) / len(seg), 1) if seg else None,
                         "maximo": max(seg) if seg else None}
    (EVID / "12_metricas.json").write_text(json.dumps(informe, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(informe, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    accion = sys.argv[1] if len(sys.argv) > 1 else ""
    sys.exit({"correr": correr, "paquete": paquete, "metricas": metricas}.get(accion, lambda: print(__doc__) or 2)())
