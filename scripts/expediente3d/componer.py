# -*- coding: utf-8 -*-
"""Ensambla un Expediente 3D: cuadros de Blender + capa 2D + voz + música (baja sola cuando habla la voz).

DOS versiones del MISMO render (no se vuelve a renderizar):
  largo  → todos los beats (~90 s, YouTube/Facebook)
  corto  → solo los beats con "corto": true en el guion (~40 s, reels); se cortan los tramos del render
           y de la voz, y se pegan en orden
Sonoridad -14 LUFS. Uso:  python componer.py <dir_pieza> largo|corto <musica.mp3> <salida.mp4>
"""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capa_expediente3d import Capa  # noqa: E402

D, VERSION, MUS, OUT = Path(sys.argv[1]).resolve(), sys.argv[2], Path(sys.argv[3]).resolve(), Path(sys.argv[4]).resolve()
TL = json.loads((D / "linea_tiempo.json").read_text(encoding="utf-8"))
G = json.loads((D / "guion.json").read_text(encoding="utf-8"))
FPS = TL["fps"]
CORTO = {b["id"] for b in G["beats"] if b.get("corto")}
MARGEN = 0.35  # s antes y después de cada beat (la pausa entre frases es de 0.45 s)

# tramos [ini, fin] en segundos del render completo
if VERSION == "largo":
    tramos = [(0.0, TL["total"])]
else:
    bs = [b for b in TL["beats"] if b["id"] in CORTO]
    tramos = [(max(0.0, b["ini"] - MARGEN), (b["fin"] + MARGEN) if b is not bs[-1] else min(TL["total"], b["fin"] + 2.0))
              for b in bs]
    tramos[0] = (0.0, tramos[0][1]) if bs and bs[0] is TL["beats"][0] else tramos[0]
cuadros = [f for a, b in tramos for f in range(round(a * FPS), min(TL["frames"], round(b * FPS)))]
# a qué beat pertenece cada cuadro (solo en la corta; en la larga la capa lo deduce del tiempo)
beat_de = ([b["id"] for b, (a, z) in zip(bs, tramos) for f in range(round(a * FPS), min(TL["frames"], round(z * FPS)))]
           if VERSION != "largo" else [None] * len(cuadros))
T = len(cuadros) / FPS
print("versión %s: %d tramos, %.1f s, %d cuadros" % (VERSION, len(tramos), T, len(cuadros)), flush=True)

# voz: los mismos tramos de voz.wav, pegados
voz_v = D / ("_voz_%s.wav" % VERSION)
flt = "".join("[0:a]atrim=%.3f:%.3f,asetpts=PTS-STARTPTS[a%d];" % (a, b, i) for i, (a, b) in enumerate(tramos))
flt += "".join("[a%d]" % i for i in range(len(tramos))) + "concat=n=%d:v=0:a=1[v]" % len(tramos)
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(D / "voz.wav"), "-filter_complex", flt, "-map", "[v]",
                str(voz_v)], check=True)

# 1) VIDEO sin audio, UNA vez por versión (lo caro: capa 2D cuadro por cuadro). FB y YT lo reusan.
video_v = D / ("_video_%s.mp4" % VERSION)
if not video_v.is_file():
    capa = Capa(D)
    tmp = video_v.with_suffix(".tmp.mp4")
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "1080x1920",
                           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "slow", "-crf", "18",
                           "-pix_fmt", "yuv420p", str(tmp)], stdin=subprocess.PIPE)
    for i, f in enumerate(cuadros):
        base = Image.open(D / "cuadros3d" / ("f_%04d.png" % f)).convert("RGBA")
        base.alpha_composite(capa.cuadro(f / FPS, i / max(1, len(cuadros) - 1), beat_de[i]))
        ff.stdin.write(base.convert("RGB").tobytes())
    ff.stdin.close()
    if ff.wait():
        sys.exit(1)
    tmp.replace(video_v)

# 2) audio (voz + música con ducking, -14 LUFS) y empaquetado, copiando el video sin recodificar
cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(video_v), "-i", str(voz_v), "-stream_loop", "-1", "-i", str(MUS),
       "-filter_complex",
       "[1:a]aformat=sample_rates=48000:channel_layouts=stereo,asplit=2[voz][sc];"
       "[2:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:%.3f,volume=0.20,afade=t=in:d=1,"
       "afade=t=out:st=%.3f:d=1.8[m];[m][sc]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=350[md];"
       "[voz][md]amix=inputs=2:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[a]" % (T, max(0.0, T - 1.8)),
       "-map", "0:v", "-map", "[a]", "-t", "%.3f" % T, "-c:v", "copy",
       "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(OUT)]
sys.exit(subprocess.run(cmd).returncode)
