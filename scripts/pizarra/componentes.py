# -*- coding: utf-8 -*-
"""Componentes de dibujo del estilo pizarra, extraídos de los prototipos aprobados (render.py, render_auto.py) sin
modificarlos. Sin nada propio de un tema: todo lo que cambia entre videos viene del guion.
Regla de legibilidad (hallazgo de Codex 18-oct): ningún texto por debajo de TAM_MIN px."""
from __future__ import annotations

from pathlib import Path

import matplotlib
import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1080, 1920, 30
TAM_MIN = 27
FUENTES = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
BOLD = str(FUENTES / "DejaVuSans-Bold.ttf")
MONO = str(FUENTES / "DejaVuSansMono-Bold.ttf")
matplotlib.rcParams["mathtext.fontset"] = "cm"

FONDO = (9, 14, 27)
PANEL = (14, 22, 38)
BORDE = (36, 56, 82)
COLOR = {"ambar": (246, 196, 72), "blanco": (232, 238, 246), "tenue": (120, 136, 160), "rosa": (255, 92, 152),
         "cian": (41, 197, 246), "lima": (178, 226, 84), "violeta": (160, 128, 245), "agua": (70, 160, 235),
         "vapor": (210, 220, 235)}


def col(nombre):
    if nombre not in COLOR:
        raise SystemExit(f"color desconocido en el guion: «{nombre}» (válidos: {sorted(COLOR)})")
    return COLOR[nombre]


def suave(x):
    x = min(1.0, max(0.0, x))
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def ts(t, t0, d):
    return suave((t - t0) / d)


def mezcla(a, b, k):
    return tuple(int(round(a[i] + (b[i] - a[i]) * k)) for i in range(3))


_fuentes = {}


def fuente(tam, mono=False):
    if tam < TAM_MIN:
        raise SystemExit(f"texto de {tam} px: menor que el mínimo legible ({TAM_MIN} px)")
    k = (tam, mono)
    if k not in _fuentes:
        _fuentes[k] = ImageFont.truetype(MONO if mono else BOLD, tam)
    return _fuentes[k]


def texto_img(s, tam, color, espaciado=0):
    f = fuente(tam)
    ancho = sum(f.getlength(c) for c in s) + espaciado * max(0, len(s) - 1)
    im = Image.new("RGBA", (int(ancho) + 4, int(tam * 1.4)), (0, 0, 0, 0))
    d, x = ImageDraw.Draw(im), 0
    for c in s:
        d.text((x, 0), c, font=f, fill=color)
        x += f.getlength(c) + espaciado
    return im


def formula(tokens, tam_px, alto=140, ancho=940):
    """tokens [(tex, color)] -> capas RGBA del mismo tamaño (una por token), alineadas en la misma línea base."""
    if tam_px < TAM_MIN:
        raise SystemExit(f"fórmula de {tam_px} px: menor que el mínimo legible")
    fig = Figure(figsize=(ancho / 100, alto / 100), dpi=100)
    canvas = FigureCanvasAgg(fig)
    fig.patch.set_alpha(0)
    pt = tam_px * 72 / 100
    objs, x = [], 0.0
    r = canvas.get_renderer()
    for tex, c in tokens:
        t = fig.text(x / ancho, 0.32, tex, fontsize=pt, color=tuple(v / 255 for v in c), va="baseline")
        objs.append(t)
        x += t.get_window_extent(renderer=r).width + tam_px * 0.18
    if x > ancho:
        raise SystemExit(f"fórmula demasiado ancha ({x:.0f} px > {ancho}): acórtala en el guion")
    capas = []
    for o in objs:
        for p in objs:
            p.set_visible(p is o)
        canvas.draw()
        capas.append(Image.fromarray(np.asarray(canvas.buffer_rgba()).copy()))
    return capas


def pegar(base, sprite, x, y, alfa=1.0, escala=1.0, centro=False):
    if alfa <= 0.003:
        return
    s = sprite
    if escala != 1.0:
        s = s.resize((max(1, int(round(s.width * escala))), max(1, int(round(s.height * escala)))), Image.BICUBIC)
    if alfa < 1.0:
        a = np.asarray(s.getchannel("A"), dtype=np.float32) * alfa
        s = s.copy()
        s.putalpha(Image.fromarray(a.astype(np.uint8)))
    if centro:
        x, y = x - s.width / 2, y - s.height / 2
    base.alpha_composite(s, (int(round(x)), int(round(y))))


def panel(dib, caja, borde, grosor=3):
    dib.rounded_rectangle(caja, 26, fill=PANEL, outline=borde, width=grosor)


def fondo():
    yy, xx = np.mgrid[0:H, 0:W]
    rad = np.sqrt(((xx - W / 2) / W) ** 2 + ((yy - H * 0.42) / H) ** 2)
    luz = np.clip(1.0 - rad * 1.1, 0, 1)[..., None]
    return Image.fromarray((np.array(FONDO) + luz * np.array([10, 16, 26])).astype(np.uint8)).convert("RGBA")
