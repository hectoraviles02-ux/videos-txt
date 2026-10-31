# -*- coding: utf-8 -*-
"""Ensambla un tramo 16:9 (1920×1080, 30 fps): cuadros 3D de Blender (beats con «estacion») o fondo liso (beats 2D),
capa 2D horizontal, voz + música YouTube Audio Library con ducking, sonoridad −14 LUFS en DOS pasadas (loudnorm
medido y luego aplicado en lineal; en clips cortos una sola pasada se queda lejos de −14).
Hermano de scripts/expediente3d/componer.py (vertical, sin tocar).

Uso: python componer16x9.py <dir_trabajo> <musica.mp3> <salida.mp4>"""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capa16x9 import Capa16x9, W, H  # noqa: E402

RAMPA = 12  # cuadros de fundido entre un beat 3D y uno 2D


def main():
    D, MUS, OUT = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
    capa = Capa16x9(D)
    TL = capa.TL
    FPS, NF, T = TL["fps"], TL["frames"], TL["total"]
    quiere = [capa.es_3d(capa.beat_en(f / FPS)["id"]) for f in range(NF)]
    peso = []                                   # 1 = cuadro 3D, 0 = fondo liso, intermedio = fundido
    for f in range(NF):
        ventana = quiere[max(0, f - RAMPA // 2): f + RAMPA // 2 + 1]
        peso.append(sum(ventana) / len(ventana))
    fondo = Capa16x9.fondo().convert("RGBA")
    video = D / "_video_16x9.mp4"
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H),
                           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "slow", "-crf", "18",
                           "-pix_fmt", "yuv420p", str(video)], stdin=subprocess.PIPE)
    for f in range(NF):
        if peso[f] > 0:
            p = D / "cuadros3d" / ("f_%04d.png" % f)
            if not p.is_file():
                sys.exit("falta el cuadro 3D %s (rango de render insuficiente)" % p)
            base = Image.open(p).convert("RGBA")
            if peso[f] < 1:
                base = Image.blend(fondo, base, peso[f])
        else:
            base = fondo.copy()
        base.alpha_composite(capa.cuadro(f / FPS, f / max(1, NF - 1)))
        ff.stdin.write(base.convert("RGB").tobytes())
    ff.stdin.close()
    if ff.wait():
        sys.exit(1)

    # audio: mezcla (voz + música con ducking) → medir → normalizar a −14 LUFS en lineal
    mezcla = D / "_mezcla.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(D / "voz.wav"), "-stream_loop", "-1", "-i", str(MUS),
                    "-filter_complex",
                    "[0:a]aformat=sample_rates=48000:channel_layouts=stereo,asplit=2[voz][sc];"
                    "[1:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:%.3f,volume=0.20,afade=t=in:d=1,"
                    "afade=t=out:st=%.3f:d=1.8[m];[m][sc]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=350[md];"
                    "[voz][md]amix=inputs=2:normalize=0[a]" % (T, max(0.0, T - 1.8)),
                    "-map", "[a]", "-t", "%.3f" % T, str(mezcla)], check=True)
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(mezcla), "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True)
    m = json.loads(r.stderr[r.stderr.rindex("{"):r.stderr.rindex("}") + 1])
    af = ("loudnorm=I=-14:TP=-1.5:LRA=11:measured_I=%s:measured_TP=%s:measured_LRA=%s:measured_thresh=%s:offset=%s:linear=true"
          % (m["input_i"], m["input_tp"], m["input_lra"], m["input_thresh"], m["target_offset"]))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(mezcla), "-af", af, "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", "%.3f" % T,
                    "-movflags", "+faststart", str(OUT)], check=True)
    print("listo:", OUT, "| medido antes de normalizar: I=%s LUFS TP=%s" % (m["input_i"], m["input_tp"]))


if __name__ == "__main__":
    main()
