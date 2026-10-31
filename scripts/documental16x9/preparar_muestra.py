# -*- coding: utf-8 -*-
"""Arma la carpeta de trabajo de una MUESTRA con un subconjunto de beats del guion de la pieza.
Los textos se copian tal cual del guion.json principal (la voz de la muestra dice exactamente lo aprobado).
gen_voz.py, linea_tiempo.py y blender_expediente.py recorren TODO el guion de su carpeta; por eso la muestra
vive en su propia subcarpeta con su propio guion.json.

Uso: python preparar_muestra.py <dir_pieza> <subcarpeta> <beat_id> [<beat_id> …]"""
from __future__ import annotations
import json
import sys
from pathlib import Path


def main():
    pieza, sub, ids = Path(sys.argv[1]).resolve(), sys.argv[2], sys.argv[3:]
    g = json.loads((pieza / "guion.json").read_text(encoding="utf-8"))
    por_id = {b["id"]: b for b in g["beats"]}
    faltan = [i for i in ids if i not in por_id]
    if faltan:
        sys.exit("beats inexistentes: %s" % faltan)
    m = {k: v for k, v in g.items() if k not in ("beats", "resumen", "avisos")}
    m["beats"] = [por_id[i] for i in ids]
    m["muestra_de"] = str(pieza / "guion.json")
    d = pieza / sub
    d.mkdir(exist_ok=True)
    (d / "guion.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    print("muestra:", d, "| beats:", ", ".join(ids), "| palabras:", sum(b["palabras"] for b in m["beats"]))


if __name__ == "__main__":
    main()
