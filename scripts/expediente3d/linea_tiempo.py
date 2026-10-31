# -*- coding: utf-8 -*-
# Copia reutilizable (15-oct-2026) de docs/operations/evidencia/reels_baldor_20261014/ — la regla de carpetas pone el código reutilizable en scripts/.
"""Línea de tiempo del reel a partir de las voces verificadas: inicio/fin de cada beat (s y cuadro),
palabras con tiempo ABSOLUTO del reel, y pista de voz completa (voz.wav) con los huecos.
Uso: python linea_tiempo.py <dir_reel>"""
import json
import subprocess
import sys
from pathlib import Path

D = Path(sys.argv[1]).resolve()
FPS, LEAD, GAP, COLA = 30, 0.7, 0.45, 2.0
g = json.loads((D / "guion.json").read_text(encoding="utf-8"))
vz = json.loads((D / "voz" / "_voz.json").read_text(encoding="utf-8"))
# Compuerta (22-oct-2026): cada frase se vuelve a comparar con el TEXTO ACTUAL del guion usando lo que oyó whisper.
# Antes esta etapa ignoraba el «ok» de la voz, y una voz vieja o equivocada llegaba al render.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from verificar_voz import verificar, verificar_cola  # noqa: E402
fallas = []
for b in g["beats"]:
    if b["id"] not in vz:
        fallas.append("%s: no tiene voz" % b["id"])
        continue
    r = verificar(b["texto"], " ".join(p["w"] for p in vz[b["id"]]["palabras"]))
    if not r["ok"]:
        fallas.append("%s: %s" % (b["id"], r["motivo"]))
    cola = verificar_cola(vz[b["id"]]["dur"], vz[b["id"]]["palabras"])
    if cola:
        fallas.append("%s: %s" % (b["id"], cola))
if fallas:
    sys.exit("VOZ NO COINCIDE CON EL GUION — no se arma la línea de tiempo:\n  " + "\n  ".join(fallas))
t, beats, palabras, partes = LEAD, [], [], []
for b in g["beats"]:
    v = vz[b["id"]]
    beats.append({"id": b["id"], "escena": b.get("escena", b.get("estacion", "")), "ini": round(t, 3), "fin": round(t + v["dur"], 3),
                  "f_ini": round(t * FPS), "f_fin": round((t + v["dur"]) * FPS)})
    palabras += [{"beat": b["id"], "w": p["w"], "ini": round(t + p["ini"], 3), "fin": round(t + p["fin"], 3)}
                 for p in v["palabras"]]
    partes.append((t, D / "voz" / v["archivo"]))
    t += v["dur"] + GAP
total = t - GAP + COLA
tl = {"fps": FPS, "total": round(total, 3), "frames": round(total * FPS), "beats": beats, "palabras": palabras}
(D / "linea_tiempo.json").write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding="utf-8")
# pista de voz: cada beat con su retardo, mezclados sin normalizar volumen
cmd = ["ffmpeg", "-v", "error", "-y"]
for _, p in partes:
    cmd += ["-i", str(p)]
flt = "".join(f"[{i}]adelay={int(s * 1000)}:all=1[a{i}];" for i, (s, _) in enumerate(partes))
flt += "".join(f"[a{i}]" for i in range(len(partes))) + f"amix=inputs={len(partes)}:normalize=0,apad=whole_dur={total}"
subprocess.run(cmd + ["-filter_complex", flt, "-ar", "48000", "-ac", "1", str(D / "voz.wav")], check=True)
print(f"total {total:.2f}s = {tl['frames']} cuadros | beats:",
      " ".join(f"{b['id']}@{b['ini']:.1f}" for b in beats))
