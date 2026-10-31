# -*- coding: utf-8 -*-
"""Motor pizarra: dibuja un video completo a partir de <carpeta_tema>/guion.json + tiempos.json + mezcla.wav.
NADA propio de un tema vive aquí: títulos, números, colores, fórmulas, escena y gráfica salen del guion.
Anclas de tiempo: "t3" (inicio del tramo), "t3:fin", "t3/palabra", "t3/palabra#2", con desfase opcional "+0.4" / "-0.3".
Uso: python motor.py <carpeta_tema> [salida.mp4] [--muestras s1 s2 ...]"""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
import componentes as C  # noqa: E402
from componentes import FPS, H, PANEL, W, col, mezcla, pegar, ts  # noqa: E402

PS = (40, 300, 1040, 860)     # escena (objeto, contadores, medidor)
PG = (40, 890, 1040, 1520)    # centro (gráfica o barras)
PF = (40, 1550, 1040, 1740)   # fórmula
FUNC = {k: getattr(math, k) for k in ("exp", "log", "sqrt", "sin", "cos", "pi")}
FUNC.update(abs=abs, min=min, max=max, round=round)


class Tema:
    def __init__(self, carpeta: Path):
        self.dir = carpeta
        self.g = json.loads((carpeta / "guion.json").read_text(encoding="utf-8"))
        self.tiempos = json.loads((carpeta / "tiempos.json").read_text(encoding="utf-8"))
        self.dur = self.tiempos["duracion"]
        self.n = int(round(self.dur * FPS))
        self.anclas_usadas = {}
        self.pistas = {k: [(self.ancla(a), float(v)) for a, v in pts] for k, pts in self.g.get("pistas", {}).items()}
        for k, pts in self.pistas.items():
            if any(pts[i + 1][0] < pts[i][0] for i in range(len(pts) - 1)):
                raise SystemExit(f"pista «{k}»: las anclas no van en orden de tiempo {[round(p[0], 2) for p in pts]}")
        self.vars_expr = self.g.get("variables", {})
        # pulsos: cuando la voz nombra algo que está en pantalla, eso late (objetivo, instante)
        self.pulsos = [(pu["objetivo"], self.ancla(pu["en"])) for pu in self.g.get("pulsos", [])]
        self._prep()

    def pulso(self, objetivo, t, d=0.7):
        """0..1: intensidad del latido de «objetivo» en el instante t (seno de 0.7 s tras cada ancla)."""
        u = [(t - t0) / d for o, t0 in self.pulsos if o == objetivo]
        return max([math.sin(math.pi * x) for x in u if 0 < x < 1], default=0.0)

    # ---------- tiempos ----------
    @staticmethod
    def _norm(s):
        s = unicodedata.normalize("NFD", s.lower())
        return "".join(c for c in s if c.isalnum())

    def ancla(self, a):
        if isinstance(a, (int, float)):
            return float(a)
        m = re.fullmatch(r"(t\d+)(?::(fin)|/([^#+\-]+)(?:#(\d+))?)?([+-]\d+(?:\.\d+)?)?", a.strip())
        if not m:
            raise SystemExit(f"ancla mal escrita: «{a}»")
        tid, fin, pal, num, off = m.groups()
        tr = next((x for x in self.tiempos["tramos"] if x["id"] == tid), None)
        if tr is None:
            raise SystemExit(f"ancla «{a}»: no existe el tramo {tid}")
        if pal:
            hits = [p for p in tr["palabras"] if self._norm(p["w"]) == self._norm(pal)]
            k = int(num or 1)
            if len(hits) < k:
                raise SystemExit(f"ancla «{a}»: no encuentro «{pal}» #{k} en {tid}: {[p['w'] for p in tr['palabras']]}")
            t = hits[k - 1]["ini"]
            self.anclas_usadas[a] = {"t": round(t, 3), "modo_tramo": tr.get("modo", "guiado")}
        else:
            t = tr["fin"] if fin else tr["ini"]
        return t + (float(off) if off else 0.0)

    def _prep(self):
        """precalcula pistas (interpolación suave entre anclas), sus derivadas y las variables, cuadro por cuadro."""
        tt = np.arange(self.n + 1) / FPS
        self.val = []
        prev = None
        for f, t in enumerate(tt):
            v = {}
            for k, pts in self.pistas.items():
                v[k] = self._interp(pts, t)
            if prev is not None:
                for k in self.pistas:
                    v["d_" + k] = (v[k] - prev[k]) * FPS
            else:
                for k in self.pistas:
                    v["d_" + k] = 0.0
            v["tiempo"] = t
            for k, e in self.vars_expr.items():
                v[k] = self.eval(e, v)
            self.val.append(v)
            prev = v

    @staticmethod
    def _interp(pts, t):
        if t <= pts[0][0]:
            return pts[0][1]
        for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
            if t <= t1:
                return v0 if t1 <= t0 else v0 + (v1 - v0) * C.suave((t - t0) / (t1 - t0))
        return pts[-1][1]

    @staticmethod
    def eval(expr, v, **extra):
        try:
            return float(eval(expr, {"__builtins__": {}}, {**FUNC, **v, **extra}))
        except Exception as e:  # noqa: BLE001
            raise SystemExit(f"expresión inválida «{expr}»: {e}")


# ---------- componentes de escena ----------
def contadores(d, t, v, spec, tema):
    if not spec:
        return
    vis = ts(t, tema.ancla(spec["desde"]), 0.5)
    for i, fila in enumerate(spec["filas"]):
        a = vis
        if "desde" in fila:
            a *= ts(t, tema.ancla(fila["desde"]), 0.4)
        if "hasta" in fila:
            a *= 1 - ts(t, tema.ancla(fila["hasta"]), 0.4)
        if a <= 0:
            continue
        pu = tema.pulso(f"contador:{i}", t)
        d.text((PS[0] + 36 + 14 * pu, PS[1] + 36 + 52 * i), fila["texto"].format(**v), font=C.fuente(40, mono=True), fill=mezcla(PANEL, mezcla(col(fila["color"]), C.COLOR["ambar"], pu), a))


def medidor(d, t, v, spec, tema):
    if not spec:
        return
    cx, cy, R = 880, 470, 112
    vis = ts(t, tema.ancla(spec["desde"]), 0.5)
    if vis <= 0:
        return
    ini, fin = 200, -20
    val = Tema.eval(spec["expr"], v)
    frac = min(1.0, max(0.0, (val - spec["min"]) / (spec["max"] - spec["min"])))
    ang = ini + (fin - ini) * frac
    d.arc((cx - R, cy - R, cx + R, cy + R), -ini, -fin, fill=mezcla(PANEL, C.BORDE, vis), width=8)
    d.arc((cx - R, cy - R, cx + R, cy + R), -ini, -ang, fill=mezcla(PANEL, col(spec.get("color", "ambar")), vis), width=8)
    for k in np.linspace(spec["min"], spec["max"], 6):
        a = np.radians(ini + (fin - ini) * (k - spec["min"]) / (spec["max"] - spec["min"]))
        d.text((cx + (R - 22) * np.cos(a) - 14, cy - (R - 22) * np.sin(a) - 12), f"{k:.0f}", font=C.fuente(27), fill=mezcla(PANEL, C.COLOR["tenue"], vis))
    a = np.radians(ang)
    d.line((cx, cy, cx + (R - 30) * np.cos(a), cy - (R - 30) * np.sin(a)), fill=mezcla(PANEL, C.COLOR["rosa"], vis), width=6)
    d.ellipse((cx - 10, cy - 10, cx + 10, cy + 10), fill=mezcla(PANEL, C.COLOR["blanco"], vis))
    d.text((cx - 92, cy + 40), f"{val:5.1f} {spec['unidad']}", font=C.fuente(44, mono=True), fill=mezcla(PANEL, C.COLOR["blanco"], vis))


def obj_precio(im, d, t, v, spec, tema):
    """etiqueta de precio con valor en vivo e insignias de cada etapa."""
    a = ts(t, tema.ancla(spec["desde"]), 0.5)
    if a <= 0:
        return
    x0, y0, x1, y1 = 380, 540, 980, 780
    pu = tema.pulso("objeto", t)
    if pu > 0:
        d.rounded_rectangle((x0 - 14 * pu, y0 - 14 * pu, x1 + 14 * pu, y1 + 14 * pu), 36, outline=mezcla(PANEL, C.COLOR["ambar"], 0.6 * pu * a), width=4)
    d.rounded_rectangle((x0, y0, x1, y1), 30, fill=mezcla(PANEL, (40, 34, 18), a), outline=mezcla(PANEL, C.COLOR["ambar"], a), width=5 + int(4 * pu))
    d.ellipse((x0 + 26, (y0 + y1) / 2 - 16, x0 + 58, (y0 + y1) / 2 + 16), outline=mezcla(PANEL, C.COLOR["ambar"], a), width=4)
    txt = spec["texto"].format(**v)
    f = C.fuente(110, mono=True)
    d.text(((x0 + x1) / 2 + 20 - d.textlength(txt, font=f) / 2, (y0 + y1) / 2 - 66), txt, font=f, fill=mezcla(PANEL, C.COLOR["blanco"], a))
    for i, e in enumerate(spec.get("insignias", [])):
        k = ts(t, tema.ancla(e["en"]), 0.4) * a
        if k <= 0:
            continue
        s = C.texto_img(e["texto"], 30, col(e.get("color", "rosa")), 2)
        pu = tema.pulso(f"insignia:{i}", t)
        bx, by = 575 + i * 170, y0 - 66 + 20 * (1 - k) - 10 * pu
        d.rounded_rectangle((bx, by, bx + s.width + 30, by + 50), 16, fill=mezcla(PANEL, (50, 20, 34), k), outline=mezcla(PANEL, mezcla(col(e.get("color", "rosa")), C.COLOR["ambar"], pu), k), width=3 + int(3 * pu))
        pegar(im, s, bx + 15, by + 6, k)


def obj_taza(im, d, t, v, spec, tema):
    """taza con líquido cuyo color sigue al calor (0..1) y vapor proporcional al calor."""
    a = ts(t, tema.ancla(spec["desde"]), 0.5)
    if a <= 0:
        return
    calor = min(1.0, max(0.0, Tema.eval(spec["calor"], v)))
    x0, y0, x1, y1 = 470, 560, 730, 760
    liquido = mezcla((110, 72, 46), (196, 112, 52), calor)
    pu = tema.pulso("objeto", t)
    if pu > 0:
        cx, cy, rr = (x0 + x1) / 2, (y0 + y1) / 2, 150 + 40 * pu
        d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=mezcla(PANEL, C.COLOR["ambar"], 0.7 * pu * a), width=4)
    d.rounded_rectangle((x0, y0, x1, y1), 30, fill=mezcla(PANEL, (226, 232, 240), a))
    d.rounded_rectangle((x0 + 14, y0 + 14, x1 - 14, y0 + 54), 18, fill=mezcla(PANEL, liquido, a))
    d.arc((x1 - 40, y0 + 40, x1 + 60, y0 + 150), -90, 90, fill=mezcla(PANEL, (226, 232, 240), a), width=16)
    d.rounded_rectangle((x0 - 40, y1 - 6, x1 + 40, y1 + 14), 10, fill=mezcla(PANEL, (180, 190, 205), a))
    rng = np.random.default_rng(7)  # vapor determinista: mismas partículas en cada render
    fases = rng.uniform(0, 1, 18)
    xs = rng.uniform(x0 + 40, x1 - 40, 18)
    for fase, xb in zip(fases, xs):
        u = (t * 0.45 + fase) % 1.0
        y = y0 - 10 - u * 230
        x = xb + 18 * math.sin(u * 6.28 + fase * 10)
        al = a * calor * (1 - u) * math.sin(math.pi * min(1, u * 4))
        if al > 0.02:
            r = 9 + 16 * u
            d.ellipse((x - r, y - r, x + r, y + r), outline=mezcla(PANEL, C.COLOR["vapor"], al), width=3)


def obj_tanque(im, d, t, v, spec, tema):
    """tanque graduado que se llena con nivel = expr y chorro de gotas cuando el caudal fluye."""
    a = ts(t, tema.ancla(spec["desde"]), 0.5)
    if a <= 0:
        return
    nivel = Tema.eval(spec["nivel"], v)
    cap = spec["capacidad"]
    flujo = Tema.eval(spec["flujo"], v)
    x0, y0, x1, y1 = 600, 450, 840, 770
    h = (y1 - y0) * min(1.0, max(0.0, nivel / cap))
    pu = tema.pulso("objeto", t)
    if pu > 0:
        d.rectangle((x0 - 12 * pu, y0 - 12 * pu, x1 + 12 * pu, y1 + 12 * pu), outline=mezcla(PANEL, C.COLOR["ambar"], 0.7 * pu * a), width=4)
    if h >= 6:  # tanque casi vacío: sin rectángulo de agua (error medido 19-oct: altura negativa)
        d.rectangle((x0 + 6, y1 - h, x1 - 6, y1 - 4), fill=mezcla(PANEL, (40, 110, 180), a))
        d.line((x0 + 6, y1 - h, x1 - 6, y1 - h), fill=mezcla(PANEL, C.COLOR["agua"], a), width=4)
    d.rectangle((x0, y0, x1, y1), outline=mezcla(PANEL, C.COLOR["tenue"], a), width=4)
    for k in range(0, cap + 1, spec.get("marcas", 20)):
        y = y1 - (y1 - y0) * k / cap
        d.line((x1, y, x1 + 18, y), fill=mezcla(PANEL, C.COLOR["tenue"], a), width=3)
        d.text((x1 + 26, y - 16), f"{k} L", font=C.fuente(27), fill=mezcla(PANEL, C.COLOR["tenue"], a))
    # boquilla y gotas (deterministas)
    hx, hy = x0 + 40, y0 - 120
    d.rounded_rectangle((hx - 70, hy - 18, hx + 70, hy + 18), 12, fill=mezcla(PANEL, (150, 160, 176), a))
    d.line((hx + 70, hy, hx + 150, hy - 26), fill=mezcla(PANEL, (150, 160, 176), a), width=10)  # no sale del panel (19-oct)
    if flujo > 0.01:
        rng = np.random.default_rng(11)
        fases = rng.uniform(0, 1, 60)
        xs = rng.uniform(hx - 60, hx + 60, 60)
        dens = min(1.0, flujo)
        for i, (fase, xb) in enumerate(zip(fases, xs)):
            if i > 60 * dens:
                break
            u = (t * 1.6 + fase) % 1.0
            y = hy + 24 + u * (y1 - h - hy - 24)
            d.line((xb + 30 * u, y, xb + 30 * u, y + 26), fill=mezcla(PANEL, C.COLOR["agua"], a * 0.9), width=6)


OBJETOS = {"precio": obj_precio, "taza": obj_taza, "tanque": obj_tanque}


# ---------- centro: gráfica ----------
def grafica(im, t, v, spec, tema):
    vis = ts(t, tema.ancla(spec["desde"]), 0.6)
    if vis <= 0:
        return
    (x0v, x1v), (y0v, y1v) = spec["x"], spec["y"]
    gx0, gy0, gx1, gy1 = PG[0] + 120, PG[1] + 130, PG[2] - 60, PG[3] - 90  # renglón 1: notas; renglón 2: nombre del eje
    SS = 2
    capa = Image.new("RGBA", ((gx1 - gx0 + 80) * SS, (gy1 - gy0 + 80) * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)
    ox, oy = 40 * SS, (gy1 - gy0 + 40) * SS
    f = lambda x: Tema.eval(spec["f"], v, x=x)  # noqa: E731

    def P(x, y):
        return ox + (x - x0v) / (x1v - x0v) * (gx1 - gx0) * SS, oy - (y - y0v) / (y1v - y0v) * (gy1 - gy0) * SS

    al = int(255 * vis)
    ejes = ts(t, tema.ancla(spec["desde"]), 0.7)
    d.line((ox, oy, ox + (gx1 - gx0) * SS * ejes, oy), fill=C.COLOR["tenue"] + (al,), width=3 * SS)
    d.line((ox, oy, ox, oy - (gy1 - gy0) * SS * ejes), fill=C.COLOR["tenue"] + (al,), width=3 * SS)
    # hasta dónde se ve la curva: una pista (p. ej. «tau»), o trazo completo en «trazo_s» segundos desde «desde»
    if "recorrido" in spec:
        xmax = Tema.eval(spec["recorrido"], v)
    else:
        xmax = x0v + (x1v - x0v) * ts(t, tema.ancla(spec["desde"]) + 0.3, spec.get("trazo_s", 1.2))
    if "area" in spec:  # área bajo la curva hasta «hasta»
        ar = spec["area"]
        aa = ts(t, tema.ancla(ar["desde"]), 0.4) * (1 - (ts(t, tema.ancla(ar["hasta_t"]), 0.4) if "hasta_t" in ar else 0))
        xa = min(Tema.eval(ar["hasta"], v), x1v)
        if aa > 0 and xa > x0v + 1e-3:
            xs = np.linspace(x0v, xa, 160)
            poly = [P(x0v, y0v)] + [P(x, f(x)) for x in xs] + [P(xa, y0v)]
            d.polygon(poly, fill=col(ar["color"]) + (int(90 * aa * vis),))
    if xmax > x0v + 1e-3:
        xs = np.linspace(x0v, min(xmax, x1v), 240)
        d.line([P(x, f(x)) for x in xs], fill=col(spec.get("color", "cian")) + (al,), width=5 * SS, joint="curve")
    for ly in spec.get("lineas_y", []):  # líneas horizontales punteadas (p. ej. una asíntota)
        k = ts(t, tema.ancla(ly["en"]), 0.6)
        if k > 0:
            y = P(0, ly["y"])[1]
            for s in np.arange(0, k, 0.04):
                d.line((ox + (gx1 - gx0) * SS * s, y, ox + (gx1 - gx0) * SS * min(s + 0.02, k), y), fill=col(ly.get("color", "tenue")) + (int(220 * vis),), width=2 * SS)
    for de in spec.get("deltas", []):  # escalón Δ: horizontal de x0 a x1 y vertical hasta la curva
        k = ts(t, tema.ancla(de["en"]), 0.5) * (1 - (ts(t, tema.ancla(de["hasta"]), 0.4) if "hasta" in de else 0))
        if k > 0:
            ya, yb = f(de["x0"]), f(de["x1"])
            d.line((P(de["x0"], ya), P(de["x0"] + (de["x1"] - de["x0"]) * k, ya)), fill=C.COLOR["violeta"] + (int(255 * vis),), width=4 * SS)
            if k > 0.5:
                kk = (k - 0.5) * 2
                d.line((P(de["x1"], ya), P(de["x1"], ya + (yb - ya) * kk)), fill=col(de.get("color", "rosa")) + (int(255 * vis),), width=4 * SS)
    for tg in spec.get("tangentes", []):
        k = ts(t, tema.ancla(tg["en"]), 0.9) * (1 - (ts(t, tema.ancla(tg["hasta"]), 0.4) if "hasta" in tg else 0))
        if k > 0:
            x = tg["x"]
            h = 1e-4 * (x1v - x0v)
            m = (f(x + h) - f(x - h)) / (2 * h)
            span = tg.get("largo", (x1v - x0v) * 0.35) * k
            d.line((P(x - span, f(x) - m * span), P(x + span, f(x) + m * span)), fill=col(tg.get("color", "ambar")) + (int(255 * vis),), width=4 * SS)
    if "punto" in spec:
        x = Tema.eval(spec["punto"], v)
        if x0v <= x <= x1v:
            px, py = P(x, f(x))
            ra = (15 + 4 * math.sin(t * 6) + 22 * tema.pulso("punto", t)) * SS
            d.ellipse((px - ra, py - ra, px + ra, py + ra), outline=C.COLOR["blanco"] + (int(180 * vis),), width=3 * SS)
            d.ellipse((px - 7 * SS, py - 7 * SS, px + 7 * SS, py + 7 * SS), fill=C.COLOR["blanco"] + (al,))
    capa = capa.resize((capa.width // SS, capa.height // SS), Image.LANCZOS)
    im.alpha_composite(capa, (gx0 - 40, gy0 - 40))
    dd = ImageDraw.Draw(im)
    c = mezcla(PANEL, C.COLOR["tenue"], vis)
    dd.text((gx1 - 90, gy1 + 46), spec["eje_x"], font=C.fuente(28), fill=c)  # debajo de los números del eje
    dd.text((gx0 - 80, gy0 - 56), spec["eje_y"], font=C.fuente(28), fill=c)
    paso = spec.get("marcas_x", 1)
    for k in np.arange(math.ceil(x0v / paso) * paso, x1v + 1e-9, paso):
        x = gx0 + (k - x0v) / (x1v - x0v) * (gx1 - gx0)
        dd.line((x, gy1, x, gy1 + 10), fill=c, width=2)
        dd.text((x - 10, gy1 + 12), f"{k:g}", font=C.fuente(27), fill=c)

    def pt(x, y):
        return gx0 + (x - x0v) / (x1v - x0v) * (gx1 - gx0), gy1 - (y - y0v) / (y1v - y0v) * (gy1 - gy0)

    for p in spec.get("puntos", []):
        k = ts(t, tema.ancla(p["en"]), 0.35) * (1 - (ts(t, tema.ancla(p["hasta"]), 0.4) if "hasta" in p else 0)) * vis
        if k > 0:
            x, y = pt(p["x"], f(p["x"]))
            rr = 12 + 10 * (1 - k)
            dd.ellipse((x - rr, y - rr, x + rr, y + rr), outline=mezcla(PANEL, col(p.get("color", "lima")), k), width=4)
            dx, dy = p.get("dx", 18), p.get("dy", -56)
            dd.text((x + dx, y + dy), p["etiqueta"], font=C.fuente(30), fill=mezcla(PANEL, col(p.get("color", "lima")), k))
    for grupo in ("tangentes", "deltas", "lineas_y"):
        for e in spec.get(grupo, []):
            if "etiqueta" not in e:
                continue
            k = ts(t, tema.ancla(e["en"]) + 0.4, 0.4) * (1 - (ts(t, tema.ancla(e["hasta"]), 0.4) if "hasta" in e else 0)) * vis
            if k > 0:
                if grupo == "tangentes":
                    x, y = pt(e["x"], f(e["x"]))
                elif grupo == "deltas":
                    x, y = pt(e["x1"], (f(e["x0"]) + f(e["x1"])) / 2)
                else:
                    x, y = pt(x1v * 0.55, e["y"])
                dd.text((x + e.get("dx", 18), y + e.get("dy", -60)), e["etiqueta"], font=C.fuente(30), fill=mezcla(PANEL, col(e.get("color", "ambar")), k))
    for e in spec.get("textos", []):  # texto ligado a variables, p. ej. el área en litros
        k = ts(t, tema.ancla(e["en"]), 0.4) * (1 - (ts(t, tema.ancla(e["hasta"]), 0.4) if "hasta" in e else 0)) * vis
        if k > 0:
            x, y = pt(Tema.eval(e["x"], v), Tema.eval(e["y"], v))
            dd.text((x, y), e["texto"].format(**v), font=C.fuente(e.get("tam", 34)), fill=mezcla(PANEL, col(e.get("color", "blanco")), k))


# ---------- centro: barras ----------
def barras(im, t, v, spec, tema):
    d = ImageDraw.Draw(im)
    bs = spec["barras"]
    n = len(bs)
    base_y, alto = PG[3] - 120, PG[3] - PG[1] - 260
    ancho = (PG[2] - PG[0] - 120) / n
    xs = [PG[0] + 60 + ancho * i + ancho * 0.2 for i in range(n)]
    tops = []
    for i, b in enumerate(bs):
        k = ts(t, tema.ancla(b["en"]), 0.8)
        val = b["valor"]
        h = alto * val / spec["max"] * k
        x0, x1 = xs[i], xs[i] + ancho * 0.55
        tops.append((x0, x1, base_y - h, k))
        if k <= 0:
            continue
        c = col(b["color"])
        pu = tema.pulso(f"barra:{i}", t)
        if pu > 0:
            d.rectangle((x0 - 10 * pu, base_y - h - 10 * pu, x1 + 10 * pu, base_y), outline=mezcla(PANEL, C.COLOR["ambar"], pu * k), width=4)
        d.rectangle((x0, base_y - h, x1, base_y), fill=mezcla(PANEL, c, (0.35 + 0.3 * pu) * k), outline=mezcla(PANEL, c, k), width=4)
        et = spec.get("formato", "{valor}").format(valor=val * k if k < 1 else val)
        f = C.fuente(34, mono=True)
        d.text(((x0 + x1) / 2 - d.textlength(et, font=f) / 2, base_y - h - 50), et, font=f, fill=mezcla(PANEL, C.COLOR["blanco"], k))
        renglones = [b["etiqueta"]]
        if C.texto_img(b["etiqueta"], 27, c, 1).width > ancho * 0.95 and " " in b["etiqueta"]:
            pal = b["etiqueta"].split()
            mitad = (len(pal) + 1) // 2
            renglones = [" ".join(pal[:mitad]), " ".join(pal[mitad:])]
        for j, r in enumerate(renglones):
            pegar(im, C.texto_img(r, 27, c, 1), (x0 + x1) / 2, base_y + 34 + 36 * j, k, centro=True)
    d.line((PG[0] + 50, base_y, PG[2] - 50, base_y), fill=C.COLOR["tenue"], width=3)
    for fl in spec.get("flechas", []):
        k = ts(t, tema.ancla(fl["en"]), 0.5)
        a, b = tops[fl["de"]], tops[fl["a"]]
        if k <= 0 or a[3] <= 0:
            continue
        xa, ya = a[1] + 6, max(a[2], b[2]) + 40
        xb = xa + (b[0] - 6 - xa) * k
        d.line((xa, ya, xb, ya), fill=C.COLOR["ambar"], width=4)
        if k > 0.95:
            d.polygon([(xb, ya), (xb - 14, ya - 9), (xb - 14, ya + 9)], fill=C.COLOR["ambar"])
        s = C.texto_img(fl["texto"], 30, C.COLOR["ambar"], 1)
        pegar(im, s, (xa + b[0]) / 2, ya + 30, k, centro=True)


def notas(im, t, spec, tema):
    for e in spec.get("notas", []):
        k = ts(t, tema.ancla(e["en"]), 0.45) * (1 - (ts(t, tema.ancla(e["hasta"]), 0.4) if "hasta" in e else 0))
        if k > 0:
            s = C.texto_img(e["texto"], 34, col(e.get("color", "ambar")), 3)
            pegar(im, s, W / 2, PG[1] + 40 + 16 * (1 - k), k, 1 + 0.08 * tema.pulso("nota", t), centro=True)


CENTROS = {"grafica": grafica, "barras": barras}


# ---------- fórmulas ----------
class Formulas:
    def __init__(self, tema):
        self.items = []
        for fo in tema.g.get("formulas", []):
            capas = C.formula([(tx, col(c)) for tx, c in fo["tokens"]], fo.get("tam", 60))
            self.items.append((fo, capas, tema.ancla(fo["desde"]), tema.ancla(fo["hasta"]) if "hasta" in fo else 1e9))

    def dibujar(self, im, d, t, tema):
        for fo, capas, t0, t1 in self.items:
            a = ts(t, t0, 0.45) * (1 - ts(t, t1, 0.35))
            if a <= 0:
                continue
            for i, capa in enumerate(capas):
                ai = a * ts(t, t0 + i * 0.15, 0.4)
                pu = tema.pulso("formula", t)
                pegar(im, capa, PF[0] + 30 + 30 * (1 - ai), PF[1] + 22 - 18 * ts(t, t1, 0.35) - 8 * pu, ai)
            pu = tema.pulso("formula", t)
            if pu > 0:
                bb = [c.getbbox() for c in capas if c.getbbox()]
                if bb:
                    xa, xb = PF[0] + 30 + min(b[0] for b in bb), PF[0] + 30 + max(b[2] for b in bb)
                    yb = PF[1] + 22 + max(b[3] for b in bb) + 8
                    d.line((xa, yb, xa + (xb - xa) * pu, yb), fill=mezcla(PANEL, C.COLOR["ambar"], a), width=4)
            mk = fo.get("marco")
            if mk:
                k = ts(t, tema.ancla(mk["en"]), 0.4) * a
                if k > 0:
                    bx0, by0, bx1, by1 = capas[mk["token"]].getbbox()
                    ox, oy = PF[0] + 30, PF[1] + 22
                    d.rounded_rectangle((ox + bx0 - 16, oy + by0 - 12, ox + bx1 + 16, oy + by1 + 12), 14, outline=mezcla(PANEL, C.COLOR["ambar"], k), width=4)


# ---------- cuadro ----------
class Motor:
    def __init__(self, carpeta):
        self.tema = Tema(Path(carpeta))
        g = self.tema.g
        self.fondo = C.fondo()
        self.tit = C.texto_img(g["titulo"], 44, C.COLOR["blanco"], 3)
        if self.tit.width > W - 80:
            raise SystemExit(f"título demasiado ancho ({self.tit.width}px): acórtalo en el guion")
        self.rem = C.texto_img(g["remate"]["texto"], 44, C.COLOR["ambar"], 4) if "remate" in g else None
        self.ley = C.texto_img(g["leyenda"], 27, C.COLOR["tenue"], 2)
        self.firma = C.texto_img("AVIL·ES  ·  @avil.es", 30, C.COLOR["tenue"], 2)
        self.form = Formulas(self.tema)
        self.obj = OBJETOS[g["escena"]["tipo"]]
        self.centro = CENTROS[g["centro"]["tipo"]]

    def cuadro(self, f):
        tema, g = self.tema, self.tema.g
        t = f / FPS
        v = tema.val[min(f, tema.n)]
        im = self.fondo.copy()
        d = ImageDraw.Draw(im)
        ka = ts(t, 0.0, 0.6)
        rk = 0.0
        if self.rem:
            t_rem = tema.ancla(g["remate"]["en"])
            rk = ts(t, t_rem, 0.25) * (1 - ts(t, t_rem + 1.4, 0.5))
        for caja in (PS, PG, PF):
            borde = mezcla(C.BORDE, C.COLOR["ambar"], rk) if caja is PG else C.BORDE
            C.panel(d, caja, mezcla(C.FONDO, borde, ka), 3 + int(2 * rk * (caja is PG)))
        pegar(im, self.tit, W / 2, 150 + 24 * (1 - ts(t, 0.1, 0.6)), ts(t, 0.1, 0.6), centro=True)
        if self.rem:
            k2 = ts(t, tema.ancla(g["remate"]["en"]), 0.5)
            if k2 > 0:
                y2 = 232 + 18 * (1 - k2)
                d.rounded_rectangle((W / 2 - self.rem.width / 2 - 26, y2 - 40, W / 2 + self.rem.width / 2 + 26, y2 + 36), 18,
                                    fill=mezcla(C.FONDO, (52, 40, 14), k2), outline=mezcla(C.FONDO, C.COLOR["ambar"], k2), width=3)
                pegar(im, self.rem, W / 2, y2, k2, centro=True)
        self.obj(im, d, t, v, g["escena"], tema)
        medidor(d, t, v, g.get("medidor"), tema)
        contadores(d, t, v, g.get("contadores"), tema)
        # leyenda de «ilustrativo» al final y con fondo propio: ningún objeto puede taparla (hallazgo de Codex 19-oct, taza)
        lx, ly = W / 2 + 120, PS[3] - 36
        d.rounded_rectangle((lx - self.ley.width / 2 - 14, ly - self.ley.height / 2 - 4, lx + self.ley.width / 2 + 14, ly + self.ley.height / 2 + 2), 12,
                            fill=mezcla(C.FONDO, PANEL, ka))
        pegar(im, self.ley, lx, ly, ka * 0.9, centro=True)
        self.centro(im, t, v, g["centro"], tema)
        notas(im, t, g["centro"], tema)
        self.form.dibujar(im, d, t, tema)
        pegar(im, self.firma, W / 2, 1800, ka, centro=True)
        return im.convert("RGB")


def render(carpeta, salida):
    m = Motor(carpeta)
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
                          "-i", "-", "-i", str(Path(carpeta) / "mezcla.wav"), "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
                          "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest",
                          "-movflags", "+faststart", str(salida)], stdin=subprocess.PIPE)
    for f in range(m.tema.n):
        p.stdin.write(m.cuadro(f).tobytes())
    p.stdin.close()
    if p.wait():
        raise SystemExit("ffmpeg falló")
    return m


if __name__ == "__main__":
    carpeta = Path(sys.argv[1])
    if "--muestras" in sys.argv:
        m = Motor(carpeta)
        for s in sys.argv[sys.argv.index("--muestras") + 1:]:
            m.cuadro(min(m.tema.n - 1, int(float(s) * FPS))).save(carpeta / f"cuadro_{s}s.png")
        raise SystemExit
    render(carpeta, Path(sys.argv[2]) if len(sys.argv) > 2 else carpeta / "render.mp4")
