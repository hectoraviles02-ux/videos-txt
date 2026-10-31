# -*- coding: utf-8 -*-
"""Revisiones audiovisuales del motor pizarra, copiadas sin cambios de laboratorio/motor_eventos_20261018/validar.py
(las que el motor usa). Falla cerrada: sin video legible ninguna revisión puede pasar (regresión 22/22 en laboratorio)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import cv2
import numpy as np


def ff(*a):
    return subprocess.run(["ffmpeg", "-hide_banner", *a], capture_output=True, text=True).stderr


def leer_cuadros(path, w=480, gray=True):
    # falla cerrada (hallazgo de Codex 19-oct): sin archivo o sin cuadros, ninguna revisión puede dar PASA
    if not Path(path).is_file():
        raise FileNotFoundError(f"video inexistente: {path}")
    cap, out = cv2.VideoCapture(str(path)), []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        fr = cv2.resize(fr, (w, int(round(fr.shape[0] * w / fr.shape[1] / 2) * 2)), interpolation=cv2.INTER_AREA)
        out.append(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY) if gray else fr)
    if len(out) < 2:
        raise ValueError(f"video ilegible o vacío ({len(out)} cuadros): {path}")
    return np.array(out)


# 1. fuente única ------------------------------------------------------------------------------


def c_movimiento_pizarra(video, fps=30, umbral_px=8, umbral_frac=0.001, max_quieto_s=3.0):
    """Movimiento para estilo pizarra (autorizado por operador 19-oct): un cuadro «se mueve» si cambia al menos el 0.1 % de
    sus píxeles en más de 8 niveles (a 480 px de ancho). El límite sigue siendo < 3 s quieto; solo cambia CÓMO se mide.
    Motivo: c_movimiento promedia el cuadro entero y no ve cambios locales (línea que se traza, auto que avanza), MF-011.
    Calibración (19-oct, fijada antes de corregir el video): congelado Knight 20 s, congelado pizarra 10 s y un punto de
    10 px moviéndose a 20 px/s FALLAN con 0.05–0.5 %; Knight 16:9 PASA con todos. No sustituye a c_movimiento en Knight."""
    fr = leer_cuadros(video).astype(np.int16)
    frac = np.array([(np.abs(fr[i] - fr[i - 1]) > umbral_px).mean() for i in range(1, len(fr))])
    run = peor = 0
    for x in frac:
        run = run + 1 if x < umbral_frac else 0
        peor = max(peor, run)
    r = {"tramo_quieto_max_s": round(peor / fps, 2), "pct_cuadros_con_cambio": round(float((frac >= umbral_frac).mean() * 100), 1)}
    return (r["tramo_quieto_max_s"] < max_quieto_s,
            f"tramo quieto máx {r['tramo_quieto_max_s']} s (pixeles>{umbral_px} niveles < {umbral_frac * 100:.1f} %); "
            f"cuadros con cambio {r['pct_cuadros_con_cambio']}%", r)


def c_parpadeo(video):
    """Rachas de 1 a 3 cuadros cuyo brillo se aparta >20 % de su entorno estable (imagen que no se pintó).
    Versión 2 (18-oct): la v1 solo veía cuadros AISLADOS y dejó pasar pares sin fondo (30-31, 62-63, 78-79)."""
    b = np.array([float(x.mean()) for x in leer_cuadros(video, w=320)])
    malos = []
    for L in (1, 2, 3):
        for i in range(4, len(b) - L - 4):
            env = np.concatenate([b[i - 4:i], b[i + L:i + L + 4]])
            med = np.median(env)
            if np.ptp(env) < 0.08 * med and np.all(np.abs(b[i:i + L] - med) > 0.2 * med):
                malos.extend(range(i, i + L))
    malos = sorted(set(malos))
    return not malos, f"cuadros con salto de brillo > 20 % (rachas de 1-3): {malos or 'ninguno'}", {"cuadros": malos}


def c_audio(video):
    if not Path(video).is_file():  # falla cerrada: sin archivo no hay medición
        raise FileNotFoundError(f"video inexistente: {video}")
    e = ff("-i", str(video), "-af", "ebur128=peak=true", "-f", "null", "-")
    if not re.findall(r"I:\s+(-?[\d.]+) LUFS", e):
        raise ValueError(f"sin pista de audio medible: {video}")
    i = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", e)[-1])
    tp = float(re.findall(r"Peak:\s+(-?[\d.]+) dBFS", e)[-1])
    return abs(i + 14) <= 1 and tp <= -1.0, f"{i} LUFS (meta −14 ± 1), true peak {tp} dBFS (≤ −1)", {"lufs": i, "tp": tp}
