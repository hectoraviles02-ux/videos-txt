# -*- coding: utf-8 -*-
"""Mezcla del motor pizarra: la MISMA cadena validada en laboratorio (construir.mezclar: ducking, ganancia, limitador,
loudnorm lineal a −14 LUFS). Sin música (por defecto, hasta verificar licencia por pista y destino): solo voz normalizada."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path



def ff(*args):
    """igual que laboratorio/motor_eventos_20261018/construir.py: sobrescribe (-y) y se detiene si ffmpeg falla."""
    p = subprocess.run(["ffmpeg", "-hide_banner", "-y", *args], capture_output=True, text=True)
    if p.returncode:
        raise SystemExit(p.stderr[-1500:])
    return p.stderr


def mezclar(voz_wav: Path, musica: Path, nivel_db: float, total: float, lufs: float, out: Path) -> dict:
    graf = (f"[1:a]aloop=loop=-1:size=2e9,atrim=0:{total:.3f},volume={nivel_db}dB,afade=t=in:d=1.0,"
            f"afade=t=out:st={total-1.4:.3f}:d=1.4[m];"
            f"[0:a]apad=whole_dur={total:.3f},asplit=2[v][vk];"
            f"[m][vk]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=350[md];"
            f"[v][md]amix=inputs=2:normalize=0[mx]")
    crudo = out.with_suffix(".crudo.wav")
    tmp = out.with_suffix(".pre.wav")
    ff("-i", str(voz_wav), "-i", str(musica), "-filter_complex", graf, "-map", "[mx]", "-ar", "48000", str(crudo))

    def medir(path):
        e = ff("-i", str(path), "-af", f"loudnorm=I={lufs}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-")
        return json.loads(e[e.rindex("{"):e.rindex("}") + 1])

    # falla real detectada por validar.py (−15.2 LUFS): la voz de Kokoro llega a ~−27 LUFS con picos altos;
    # loudnorm lineal no puede subir +13 dB sin pasarse de pico. Orden correcto: ganancia -> limitador -> lineal.
    m0 = medir(crudo)
    gan = lufs - float(m0["input_i"]) + 0.5
    ff("-i", str(crudo), "-af", f"volume={gan:.2f}dB,alimiter=limit=0.79:attack=5:release=60:level=disabled",
       "-ar", "48000", str(tmp))
    crudo.unlink()
    m = medir(tmp)
    ff("-i", str(tmp), "-af",
       f"loudnorm=I={lufs}:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
       f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true",
       "-ar", "48000", str(out))
    tmp.unlink()
    return {"medido_crudo": m0, "ganancia_db": round(gan, 2), "medido_tras_limitador": m}


# ── 5b. capa de documento horneada (estabilización, 18-oct) ──────────────────────────────────
# Causa medida: Chrome rasteriza distinto la página PDF grande escalada en cada render (PSNR 39–47 dB entre
# renders iguales). Aquí la página se rasteriza UNA vez (hash), y cámara + resaltado se calculan con
# aritmética determinista en numpy/OpenCV; Remotion solo reproduce ese video y encima pone los textos.


def mezclar_solo_voz(voz_wav: Path, total: float, lufs: float, out: Path) -> dict:
    """Sin música: voz con silencio hasta «total», ganancia → limitador → loudnorm lineal (misma cadena, sin ducking)."""
    crudo = out.with_suffix(".crudo.wav")
    ff("-i", str(voz_wav), "-af", f"apad=whole_dur={total:.3f}", "-ar", "48000", str(crudo))

    def medir(path):
        e = ff("-i", str(path), "-af", f"loudnorm=I={lufs}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-")
        return json.loads(e[e.rindex("{"):e.rindex("}") + 1])

    m0 = medir(crudo)
    gan = lufs - float(m0["input_i"]) + 0.5
    tmp = out.with_suffix(".pre.wav")
    ff("-i", str(crudo), "-af", f"volume={gan:.2f}dB,alimiter=limit=0.79:attack=5:release=60:level=disabled", "-ar", "48000", str(tmp))
    crudo.unlink()
    m1 = medir(tmp)
    ff("-i", str(tmp), "-af", f"loudnorm=I={lufs}:TP=-1.5:LRA=11:measured_I={m1['input_i']}:measured_TP={m1['input_tp']}:"
       f"measured_LRA={m1['input_lra']}:measured_thresh={m1['input_thresh']}:offset={m1['target_offset']}:linear=true", "-ar", "48000", str(out))
    tmp.unlink()
    return {"medido_crudo": m0, "ganancia_db": round(gan, 2)}
