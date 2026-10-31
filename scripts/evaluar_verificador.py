# -*- coding: utf-8 -*-
"""Mide el gate de fidelidad contra el conjunto de evaluacion con calificacion de referencia.

Uso:
  python scripts/evaluar_verificador.py                  # solo capa determinista (sin LLM)
  python scripts/evaluar_verificador.py --modelo qwen2.5:7b-instruct-q4_K_M   # + juez Ollama

Metricas (lo que pidio el operador para elegir verificador):
  - exactitud por clase y matriz de confusion
  - FALSOS BLOQUEOS: esperada RESPALDADA y el gate bloquea
  - FUGAS: esperada PARCIAL/SIN EVIDENCIA/CONTRADICHA y el gate deja pasar (lo grave)
  - contradiccion confundida: SIN EVIDENCIA marcada CONTRADICHA
  - fragmentos inventados por el juez; tiempo
Salida: tabla en pantalla + JSON con el detalle (--salida).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from synthesis import gate_fidelidad as G  # noqa: E402

FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "fidelidad_elpais"
CLASES = [G.RESPALDADA, G.PARCIAL, G.SIN_EVIDENCIA, G.CONTRADICHA]


def cargar():
    conj = json.loads((FIX / "conjunto_evaluacion.json").read_text(encoding="utf-8"))
    fuente = json.loads((FIX / "fuente_elpais.json").read_text(encoding="utf-8"))
    return conj, G.texto_de_fuente(fuente)


def validar_fragmentos(conj, texto) -> list[str]:
    """Todo fragmento de referencia debe existir LITERAL en la fuente."""
    malos = []
    for c in conj["casos"]:
        f = c.get("fragmento") or ""
        if c["esperada"] != G.SIN_EVIDENCIA and not f:
            malos.append(f"{c['id']}: sin fragmento")
        elif f and G._norm(f) not in G._norm(texto):
            malos.append(f"{c['id']}: fragmento NO literal")
    return malos


def evaluar(conj, texto, juez=None, lote: int = 3) -> dict:
    """`lote`: afirmaciones por llamada al juez (el bot manda una escena, ~3 frases).
    Los casos con validacion 'en_revision' se evaluan pero NO entran a las metricas."""
    filas, t0 = [], time.time()
    casos = conj["casos"]
    veredictos: list = [None] * len(casos)
    juez_fallo = []
    if juez is not None:
        for i in range(0, len(casos), lote):
            grupo = casos[i:i + lote]
            try:
                veredictos[i:i + lote] = juez(texto, [c["afirmacion"] for c in grupo])
            except G.JuezNoDisponible as e:
                juez_fallo.append(f"{grupo[0]['id']}..{grupo[-1]['id']}: {e}")
    t_juez = time.time() - t0
    for c, v in zip(casos, veredictos):
        a = G.verificar_afirmacion(c["afirmacion"], texto, v or None)
        filas.append({"id": c["id"], "categoria": c["categoria"], "esperada": c["esperada"],
                      "obtenida": a.clasificacion, "ambiguo": c.get("ambiguo", False),
                      "en_revision": c.get("validacion") == "en_revision",
                      "cobertura": a.cobertura, "motivos": a.motivos,
                      "fragmento_inventado": any("NO existe en la fuente" in m for m in a.motivos),
                      "juez": v, "juez_dijo": (v or {}).get("clasificacion")})
    m = [f for f in filas if not f["en_revision"]]
    ok = sum(f["esperada"] == f["obtenida"] for f in m)
    falsos = [f["id"] for f in m if f["esperada"] == G.RESPALDADA and f["obtenida"] in G.BLOQUEAN]
    fugas = [f["id"] for f in m if f["esperada"] in G.BLOQUEAN and f["obtenida"] not in G.BLOQUEAN]
    sin_como_contra = [f["id"] for f in m if f["esperada"] == G.SIN_EVIDENCIA and f["obtenida"] == G.CONTRADICHA]
    contra_no_detectada = [f["id"] for f in m if f["esperada"] == G.CONTRADICHA and f["obtenida"] != G.CONTRADICHA]
    por_clase = {}
    for k in CLASES:
        esp = [f for f in m if f["esperada"] == k]
        obt = [f for f in m if f["obtenida"] == k]
        tp = sum(f["esperada"] == k for f in obt)
        por_clase[k] = {"n": len(esp), "recall": round(tp / len(esp), 2) if esp else None,
                        "precision": round(tp / len(obt), 2) if obt else None}
    matriz = Counter((f["esperada"], f["obtenida"]) for f in m)
    # lo que el JUEZ dijo por si solo (antes de la capa determinista)
    juez_ok = sum(1 for f in m if f["juez_dijo"] and
                  str(f["juez_dijo"]).upper().replace("_", " ") == f["esperada"])

    def _jd(f):
        return str(f["juez_dijo"] or "").upper().replace("_", " ")
    con_juez = [f for f in m if f["juez_dijo"]]
    juez_solo = {
        "fugas": [f["id"] for f in con_juez if f["esperada"] in G.BLOQUEAN and _jd(f) == G.RESPALDADA],
        "falsos_bloqueos": [f["id"] for f in con_juez if f["esperada"] == G.RESPALDADA and _jd(f) != G.RESPALDADA],
        "sin_evidencia_como_contradicha": [f["id"] for f in con_juez
                                           if f["esperada"] == G.SIN_EVIDENCIA and _jd(f) == G.CONTRADICHA],
    }
    return {"total_metricas": len(m), "aciertos": ok, "falsos_bloqueos": falsos, "fugas": fugas,
            "sin_evidencia_como_contradicha": sin_como_contra,
            "contradicha_no_detectada": contra_no_detectada,
            "fragmentos_inventados": [f["id"] for f in m if f["fragmento_inventado"]],
            "juez_solo_aciertos": juez_ok, "juez_solo": juez_solo, "juez_fallos": juez_fallo,
            "segundos": round(time.time() - t0, 1), "segundos_juez": round(t_juez, 1),
            "por_clase": por_clase, "en_revision": [
                {"id": f["id"], "esperada_provisional": f["esperada"], "obtenida": f["obtenida"]}
                for f in filas if f["en_revision"]],
            "matriz": {f"{e} -> {o}": n for (e, o), n in sorted(matriz.items())}, "filas": filas}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modelo", help="modelo Ollama para el juez (sin esto: solo determinista)")
    ap.add_argument("--ollama", default="http://localhost:11434")
    ap.add_argument("--salida", help="JSON con el detalle")
    ap.add_argument("--nli", action="store_true", help="juez NLI local (scripts/juez_nli.py)")
    ap.add_argument("--reparar-espacios", action="store_true",
                    help="inserta el espacio entre oraciones pegadas en la fuente antes de evaluar")
    ap.add_argument("--nli-dispositivo", default=None, help="cpu | cuda (por omision: cuda si hay)")
    a = ap.parse_args()
    conj, texto = cargar()
    if a.reparar_espacios:
        # la extraccion pega oraciones sin espacio («…publica.Marcha…»); eso hacia fallar la
        # comprobacion literal de fragmentos de 2 oraciones (falso «fragmento inventado»)
        import re
        texto = re.sub(r"([.!?”»\"])(?=[A-ZÁÉÍÓÚÑ“«])", r"\1 ", texto)
    malos = validar_fragmentos(conj, texto)
    print(f"fragmentos de referencia: {len(conj['casos']) - len(malos)}/{len(conj['casos'])} literales")
    for m in malos:
        print("  ✗", m)
    if malos:
        sys.exit(2)
    if a.nli:
        from juez_nli import MODELO, juez_nli
        juez = juez_nli(a.nli_dispositivo)
        nombre = f"NLI {MODELO.split('/')[-1]} ({juez.dispositivo})"
    else:
        juez = G.juez_ollama(a.ollama, a.modelo) if a.modelo else None
        nombre = a.modelo or "determinista"
    r = evaluar(conj, texto, juez)
    r["verificador"] = nombre
    print(f"verificador: {r['verificador']} · {r['segundos']} s (juez {r['segundos_juez']} s)")
    print(f"aciertos gate: {r['aciertos']}/{r['total_metricas']} · juez por sí solo: {r['juez_solo_aciertos']}")
    print(f"FUGAS (debía bloquear y dejó pasar): {len(r['fugas'])} {r['fugas']}")
    print(f"falsos bloqueos (legítimas bloqueadas): {len(r['falsos_bloqueos'])} {r['falsos_bloqueos']}")
    print(f"sin evidencia marcada contradicha: {r['sin_evidencia_como_contradicha']}")
    print(f"contradicha no detectada como tal: {r['contradicha_no_detectada']}")
    print(f"fragmentos inventados por el juez: {r['fragmentos_inventados']}")
    print(f"fallos del juez: {r['juez_fallos']}")
    js = r["juez_solo"]
    print(f"JUEZ POR SÍ SOLO -> fugas: {len(js['fugas'])} {js['fugas']} · falsos bloqueos: "
          f"{len(js['falsos_bloqueos'])} · ausencia como contradicción: {js['sin_evidencia_como_contradicha']}")
    print("por clase (recall = cuántas de esa clase reconoce; precisión = cuántas de las que etiqueta son correctas):")
    for k, v in r["por_clase"].items():
        print(f"  {k:13} n={v['n']}  recall={v['recall']}  precisión={v['precision']}")
    print(f"en revisión (fuera de métricas): {r['en_revision']}")
    print("matriz (esperada -> obtenida):")
    for k, n in r["matriz"].items():
        print(f"  {k}: {n}")
    print("detalle:")
    for f in r["filas"]:
        marca = "  " if f["esperada"] == f["obtenida"] else "✗ "
        print(f"  {marca}{f['id']:4} {f['esperada']:13} -> {f['obtenida']:13} cov={f['cobertura']:.2f}"
              f" juez={f['juez_dijo'] or '-'}{' (ambiguo)' if f['ambiguo'] else ''}"
              f"{' (EN REVISIÓN)' if f['en_revision'] else ''}")
    if a.salida:
        Path(a.salida).parent.mkdir(parents=True, exist_ok=True)
        Path(a.salida).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        print("guardado:", a.salida)


if __name__ == "__main__":
    main()
