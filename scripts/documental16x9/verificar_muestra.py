# -*- coding: utf-8 -*-
"""Verifica un mp4 16:9 contra su guion: resolución, fps, duración, sonoridad (ebur128), hoja de cuadros y
transcripción whisper del AUDIO FINAL comparada palabra por palabra con el texto del guion.
Corre con el python que tiene faster-whisper (chatterbox_ab_env). ASR_DEVICE=cpu por defecto.

Uso: <py> verificar_muestra.py <video.mp4> <guion.json> <hoja.jpg> <informe.json>"""
from __future__ import annotations
import difflib
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path


def norm_pal(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return [w for w in re.split(r"[^a-z0-9]+", s) if w]


def main():
    mp4, guion, hoja, inf = (Path(x).resolve() for x in sys.argv[1:5])
    pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                    "stream=codec_type,width,height,r_frame_rate:format=duration", "-of", "json", str(mp4)],
                                   capture_output=True, text=True).stdout)
    v = next(s for s in pr["streams"] if s["codec_type"] == "video")
    dur = float(pr["format"]["duration"])
    e = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(mp4), "-filter_complex", "ebur128=peak=true",
                        "-f", "null", "-"], capture_output=True, text=True).stderr
    resumen = e[e.rindex("Summary:"):]
    lufs = float(re.search(r"I:\s+(-?[\d.]+) LUFS", resumen).group(1))
    tp = float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", resumen).group(1))
    # hoja de cuadros: 12 cuadros repartidos en todo el video, 4×3
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp4), "-vf",
                    "fps=12/%.3f,scale=480:-1,tile=4x3:padding=6:color=black" % dur, "-frames:v", "1", str(hoja)], check=True)
    from faster_whisper import WhisperModel
    dev = os.environ.get("ASR_DEVICE", "cpu")
    asr = WhisperModel("large-v3", device=dev, compute_type="float16" if dev == "cuda" else "int8")
    segs, _ = asr.transcribe(str(mp4), language="es", condition_on_previous_text=False)
    oido = " ".join(s.text.strip() for s in segs)
    g = json.loads(guion.read_text(encoding="utf-8"))
    esperado = " ".join(b["texto"] for b in g["beats"] if b.get("texto"))
    a, b = norm_pal(esperado), norm_pal(oido)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    difs = [{"op": op, "guion": " ".join(a[i1:i2]), "oido": " ".join(b[j1:j2])}
            for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]
    r = {"video": str(mp4), "ancho": v["width"], "alto": v["height"], "fps": v["r_frame_rate"], "duracion_s": round(dur, 3),
         "lufs_integrado": lufs, "true_peak_dbfs": tp, "hoja": str(hoja),
         "palabras_guion": len(a), "palabras_oidas": len(b), "coincidencia_palabras": round(sm.ratio(), 4),
         "diferencias": difs, "transcripcion": oido, "texto_guion": esperado}
    inf.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: r[k] for k in r if k not in ("transcripcion", "texto_guion")}, ensure_ascii=False, indent=1))
    print("OÍDO:", oido)


if __name__ == "__main__":
    main()
