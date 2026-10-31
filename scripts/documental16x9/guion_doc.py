# -*- coding: utf-8 -*-
"""Utilidades del guion de un documental horizontal (16:9).

- F(alias, seg, cita, afirma)   → fuente verificada contra procesado/ (aborta si la cita no es literal).
- D(salida_txt, linea, afirma)  → cifra que sale de una demo propia: la línea debe estar literal en demos/salidas/*.txt.
- escribir(guion, dir)          → guion.json + GUION_DOCUMENTAL.md con minutaje estimado (palabras / ritmo).
  En el .md cada cita va en formato «procesado/....json — «cita»» para que la compruebe
  `python scripts/validar_monetizacion.py --verificar-fuentes --md GUION_DOCUMENTAL.md`.
"""
from __future__ import annotations
import json
import re
from pathlib import Path

from fuentes import REPO, cita, ruta

ETIQUETA = {
    "sec": "SEC, orden 34-70694 (2013)", "npm": "npm, blog oficial (2016)", "gitlab": "GitLab, postmortem oficial (2017)",
    "cf_a": "Cloudflare, postmortem (2019)", "cf_b": "Cloudflare, aviso del 2-jul-2019",
    "cs_blog": "CrowdStrike, detalles técnicos (2024)", "cs_rca": "CrowdStrike, análisis de causa raíz (2024)",
    "ms": "Microsoft, blog oficial (2024)", "demo": "Cálculo propio en Python con cifras de la fuente",
}


def F(alias: str, seg: int, texto: str, afirma: str) -> dict:
    return {"tipo": "fuente", "alias": alias, "ruta": ruta(alias), "segmento": seg, "cita": cita(alias, seg, texto),
            "afirma": afirma}


def D(dir_pieza: Path, salida: str, linea: str, afirma: str) -> dict:
    p = Path(dir_pieza) / "demos" / "salidas" / salida
    if linea not in p.read_text(encoding="utf-8"):
        raise AssertionError("%s no contiene «%s»" % (p, linea))
    return {"tipo": "demo", "alias": "demo", "ruta": str(p.relative_to(REPO)).replace("\\", "/"), "linea": linea,
            "afirma": afirma}


def palabras(s: str) -> int:
    return len([w for w in re.split(r"\s+", s.strip()) if re.search(r"\w", w)])


def mmss(s: float) -> str:
    s = round(s)
    return "%d:%02d" % (s // 60, s % 60)


def escribir(g: dict, d: Path, ritmo: float = 2.70) -> dict:
    d = Path(d)
    t = 0.0
    caps: dict = {}
    seg_rec: dict = {}
    for b in g["beats"]:
        b["palabras"] = palabras(b.get("texto", ""))
        b["seg_estimados"] = round(b.get("seg_reservados", b["palabras"] / ritmo), 1)
        b["ini_estimado"], t = round(t, 1), t + b["seg_estimados"]
        b["fuente"] = ",".join(sorted({f["alias"] for f in b.get("fuentes", [])}))
        c = caps.setdefault(b["capitulo"], {"titulo": b["capitulo_titulo"], "palabras": 0, "seg": 0.0, "ini": b["ini_estimado"]})
        c["palabras"] += b["palabras"]
        c["seg"] += b["seg_estimados"]
        seg_rec[b["recurso"]] = seg_rec.get(b["recurso"], 0.0) + b["seg_estimados"]
    g["etiquetas_fuente"] = ETIQUETA
    g["resumen"] = {"ritmo_palabras_s": ritmo, "palabras": sum(c["palabras"] for c in caps.values()),
                    "seg_total": round(t, 1), "minutaje_total": mmss(t),
                    "capitulos": {k: {"titulo": v["titulo"], "ini": mmss(v["ini"]), "palabras": v["palabras"],
                                      "seg": round(v["seg"], 1), "dur": mmss(v["seg"])} for k, v in caps.items()},
                    "recursos_pct": {k: round(100 * v / t, 1) for k, v in sorted(seg_rec.items())}}
    (d / "guion.json").write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")

    r = g["resumen"]
    md = ["# %s — guion" % g["titulo"], "",
          "Generado por `%s` (no editar a mano: se edita el constructor y se vuelve a correr)." % g.get("constructor", ""),
          "", "**Estado:** GUION para aprobación de el operador. Sin voz completa, sin render completo, sin publicar.",
          "**Formato:** %s · **Ritmo de estimación:** %.2f palabras/s (medido 2.64–2.77, pausas incluidas)." % (g.get("formato", ""), ritmo),
          "**Total estimado:** %s (%d palabras narradas%s)." % (r["minutaje_total"], r["palabras"],
                                                                "; incluye tiempo reservado sin texto" if any("seg_reservados" in b for b in g["beats"]) else ""),
          "**Recursos visuales (por tiempo):** " + " · ".join("%s %.1f %%" % (k, v) for k, v in r["recursos_pct"].items()), "",
          "Convenciones: el `texto` es lo que dice la voz (cifras en palabras; «Python» se pronuncia «Páiton» por el léxico). "
          "Cada beat lista sus fuentes: ruta del procesado, raya y cita literal entre comillas angulares, o la línea literal de una demo propia en "
          "`demos/salidas/`. **DERIVADO** = cálculo nuestro; **SIMULACIÓN** = modelo ilustrativo, no el sistema real.", "",
          "## Minutaje por capítulo", "", "| # | Capítulo | Inicio | Duración | Palabras |", "|---|---|---|---|---|"]
    for k, v in r["capitulos"].items():
        md.append("| %s | %s | %s | %s | %d |" % (k, v["titulo"], v["ini"], v["dur"], v["palabras"]))
    md.append("| | **Total** | | **%s** | **%d** |" % (r["minutaje_total"], r["palabras"]))
    cap = None
    for b in g["beats"]:
        if b["capitulo"] != cap:
            cap = b["capitulo"]
            md += ["", "## %s. %s" % (cap, b["capitulo_titulo"]), ""]
        md.append("### %s · %s · %s (≈ %.0f s, %d palabras)" % (b["id"], mmss(b["ini_estimado"]), b["recurso"],
                                                               b["seg_estimados"], b["palabras"]))
        if b.get("pendiente"):
            md.append("> **PENDIENTE:** %s" % b["pendiente"])
        if b.get("texto"):
            md.append("> %s" % b["texto"])
        md.append("")
        md.append("- **Visual:** %s" % b.get("visual", ""))
        for f in b.get("fuentes", []):
            if f["tipo"] == "fuente":
                md.append("- %s — segmentos[%d]: %s — «%s»" % (f["afirma"], f["segmento"], f["ruta"], f["cita"]))
            else:
                md.append("- %s — salida de demo `%s`: `%s`" % (f["afirma"], f["ruta"], f["linea"].strip()))
    md += ["", "## Pendientes y avisos", ""] + ["- " + a for a in g.get("avisos", [])]
    (d / "GUION_DOCUMENTAL.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return g
