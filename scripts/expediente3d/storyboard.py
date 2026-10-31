# -*- coding: utf-8 -*-
"""Storyboard de aprobación de un Expediente a partir de su guion.json (sin voz ni GPU).
Duración estimada con la velocidad REAL medida de la voz del documental: 2.97 palabras/s (Expediente 02).
Uso: python storyboard.py <dir_pieza>  → <dir>/STORYBOARD_APROBACION.md"""
import json
import sys
from pathlib import Path

PAL_S, PAUSA, COLA = 2.97, 0.45, 2.7
ESC = {"monolito": "pantalla flotante", "globo": "planeta de puntos", "racks": "sala de servidores",
       "barras": "barras de datos", "linea": "línea de tiempo"}
D = Path(sys.argv[1]).resolve()
g = json.loads((D / "guion.json").read_text(encoding="utf-8"))
L = ["# %s · Storyboard para aprobación" % g["titulo"], "",
     "Estado: **%s**." % g.get("estado", "borrador"),
     "Duración estimada a %.2f palabras/s (medido en la voz real) + %.2f s entre frases." % (PAL_S, PAUSA), "",
     "| Beat | 40 s | Seg. aprox. | Escena 3D | Efecto | Tarjeta en pantalla | Fuente (hechos) | Narración |",
     "|---|---|---|---|---|---|---|---|"]
t, t40 = 0.7, 0.7
for b in g["beats"]:
    d = len(b["texto"].split()) / PAL_S
    tj = b.get("tarjeta", {})
    desc = tj.get("titulo") or tj.get("cifra") or " · ".join(tj.get("items", []) or tj.get("lineas", [])) or tj.get("tipo", "")
    ef = ", ".join(e["tipo"] for e in b.get("efectos", [])) or "—"
    L.append("| %s | %s | %.0f–%.0f | %s | %s | %s | %s (%s) | %s |" % (
        b["id"], "✓" if b.get("corto") else "", t, t + d, ESC.get(b.get("estacion"), b.get("estacion")), ef,
        str(desc).replace("|", "/"), b.get("fuente", ""), b.get("hechos", ""), b["texto"]))
    t += d + PAUSA
    if b.get("corto"):
        t40 += d + PAUSA
L += ["", "**Duración estimada:** larga ≈ %.0f s · corta (beats ✓) ≈ %.0f s." % (t - PAUSA + COLA, t40 - PAUSA + COLA),
      "", "## Fuentes", ""] + ["- **%s**: %s" % (k, v) for k, v in g.get("fuentes", {}).items()]
(D / "STORYBOARD_APROBACION.md").write_text("\n".join(L) + "\n", encoding="utf-8")
print("larga ≈ %.0f s | corta ≈ %.0f s → %s" % (t - PAUSA + COLA, t40 - PAUSA + COLA, D / "STORYBOARD_APROBACION.md"))
