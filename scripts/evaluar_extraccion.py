# -*- coding: utf-8 -*-
"""Mide una extracción de dossier contra el dossier de REFERENCIA (10 hechos centrales de la nota de
El País, tests/fixtures/fidelidad_elpais/dossier_prueba.json).

Uso:
  python scripts/evaluar_extraccion.py --modelo qwen2.5:14b-instruct-q4_K_M [--salida X.json]
  python scripts/evaluar_extraccion.py --revalidar-anterior   # la extracción del 13-oct con el validador actual

Métricas:
- cobertura de hechos centrales: un hecho de referencia queda CUBIERTO si los claims aceptados que
  se solapan con su cita reúnen todas sus cifras y al menos el 60 % de su vocabulario; si no, PARCIAL u OMITIDO
- atribución correcta: en los hechos de referencia que no son «hecho», los claims que los cubren
  tienen el tipo correcto (no «hecho») y nombran al mismo emisor
- sujetos o nombres sin evidencia: claims aceptados que nombran algo que no está en su evidencia
- posibles falsos rechazos: rechazados cuya cita SÍ respalda la afirmación (NLI) y que cayeron solo
  por reglas de forma (para revisión humana)
- claims, aceptados, rechazados, cobertura de oraciones y cifras, llamadas al modelo, tiempo
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from synthesis import dossier_extraccion as D  # noqa: E402
from synthesis import gate_fidelidad as G  # noqa: E402
from synthesis import guion_anclado as A  # noqa: E402

FIX = RAIZ / "tests" / "fixtures" / "fidelidad_elpais"
UMBRAL_VOCAB = 0.60


def _span(texto_n: str, cita: str):
    c = G._norm(G._espaciar(cita))
    i = texto_n.find(c) if c else -1
    return (i, i + len(c)) if i >= 0 else None


def medir(res: dict, texto: str, juez=None) -> dict:
    ref = json.loads((FIX / "dossier_prueba.json").read_text(encoding="utf-8"))["claims"]
    tn = G._norm(G._espaciar(texto))
    acept = [(c, _span(tn, A._cita(c))) for c in res["claims"]]
    hechos = []
    for r in ref:
        sr = _span(tn, A._cita(r))
        solap = [c for c, s in acept if s and sr and s[0] < sr[1] and sr[0] < s[1]]
        cifras_ref = {t for t in G._tokens(r["afirmacion"]) if t.startswith("#")}
        cifras_got = {t for c in solap for t in G._tokens(c["afirmacion"]) if t.startswith("#")}
        voc_ref = G._contenido(G._tokens(A._cita(r)))
        voc_got = set().union(*[G._contenido(G._tokens(A._cita(c))) for c in solap]) if solap else set()
        frac = len(voc_ref & voc_got) / len(voc_ref) if voc_ref else 0
        if solap and cifras_ref <= cifras_got and frac >= UMBRAL_VOCAB:
            estado = "CUBIERTO"
        elif solap:
            estado = "PARCIAL"
        else:
            estado = "OMITIDO"
        atr_ok = None
        if r.get("tipo_enunciado", "hecho") != "hecho" and solap:
            a_ref = G._contenido(G._tokens(r.get("atribuido_a", "")))
            atr_ok = all(c.get("tipo_enunciado") != "hecho" for c in solap) and \
                any(G._contenido(G._tokens(c.get("atribuido_a", ""))) & a_ref for c in solap)
        hechos.append({"ref": r["id"], "estado": estado, "vocabulario": round(frac, 2),
                       "cifras_faltantes": sorted(x[1:] for x in cifras_ref - cifras_got),
                       "claims": [c["id"] for c in solap], "atribucion_correcta": atr_ok})
    # nombres (con mayuscula) del claim que no están en su evidencia
    sin_ev = []
    for c in res["claims"]:
        ev = G._norm(A._evidencia(c))
        for w in re.findall(r"(?<!^)(?<![.!?]\s)\b[A-ZÁÉÍÓÚÑ][\wáéíóúñ]{2,}", c["afirmacion"]):
            if G._norm(w) not in ev:
                sin_ev.append({"claim": c["id"], "nombre": w, "afirmacion": c["afirmacion"]})
    # posibles falsos rechazos (solo reglas de forma; el NLI sí respalda)
    reglas_forma = ("no puede ser «hecho»", "atribuido_a", "ubicación", "sujeto")
    falsos = []
    for c in res["rechazados"]:
        m = c.get("motivos_rechazo", [])
        if m and all(any(k in x for k in reglas_forma) for x in m) and juez is not None:
            v = juez(A._evidencia(c), [c["afirmacion"]])[0]
            if str(v.get("clasificacion", "")).upper() == "RESPALDADA":
                falsos.append({"claim": c["id"], "afirmacion": c["afirmacion"], "motivos": m})
    cuenta = {e: sum(h["estado"] == e for h in hechos) for e in ("CUBIERTO", "PARCIAL", "OMITIDO")}
    atr = [h["atribucion_correcta"] for h in hechos if h["atribucion_correcta"] is not None]
    return {"llm": res.get("llm"), "segundos": res.get("segundos"), "llamadas": res.get("llamadas"),
            "claims_total": len(res["claims"]) + len(res["rechazados"]),
            "aceptados": len(res["claims"]), "rechazados": len(res["rechazados"]),
            "hechos_centrales": cuenta, "atribucion_correcta": f"{sum(atr)}/{len(atr)}",
            "nombres_sin_evidencia": sin_ev, "posibles_falsos_rechazos": falsos,
            "sin_cubrir": res.get("sin_cubrir", []), "completo": res.get("completo"), "detalle_hechos": hechos}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modelo")
    ap.add_argument("--revalidar-anterior", action="store_true")
    ap.add_argument("--salida")
    a = ap.parse_args()
    fuente = json.loads((FIX / "fuente_elpais.json").read_text(encoding="utf-8"))
    texto = G.texto_de_fuente(fuente)
    from synthesis.juez_nli import juez_nli
    juez = juez_nli()
    if a.revalidar_anterior:
        p = RAIZ / "docs/operations/MOTOR_DOCUMENTAL_PERIODISTICO/GATE_FIDELIDAD_20261013/guion_anclado/extraccion_14b.json"
        prev = json.loads(p.read_text(encoding="utf-8"))
        acept, rech = [], []
        for c in prev["claims"]:
            e = A.validar_dossier({"claims": [c]}, texto, juez).errores
            (rech.append(dict(c, motivos_rechazo=e)) if e else acept.append(c))
        res = {"llm": prev["modelo"] + " (extracción anterior, validador actual)", "segundos": prev["segundos"],
               "llamadas": 1, "claims": acept, "rechazados": rech, "sin_cubrir": ["(no medido)"], "completo": None}
    else:
        res = D.extraer(fuente, D.llm_ollama("http://localhost:11434", a.modelo), juez, medio="El País")
    m = medir(res, texto, juez)
    print(json.dumps({k: v for k, v in m.items() if k != "detalle_hechos"}, ensure_ascii=False, indent=1)[:4000])
    for h in m["detalle_hechos"]:
        print(f"  {h['ref']:18} {h['estado']:9} vocab={h['vocabulario']} cifras_faltan={h['cifras_faltantes']} "
              f"atrib={h['atribucion_correcta']} claims={h['claims']}")
    if a.salida:
        Path(a.salida).write_text(json.dumps({"medicion": m, "extraccion": res}, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
        print("guardado:", a.salida)


if __name__ == "__main__":
    main()
