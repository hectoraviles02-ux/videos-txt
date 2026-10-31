# -*- coding: utf-8 -*-
"""Capa 2D HORIZONTAL (1920×1080) del documental — Pillow, RGBA sobre el cuadro 3D o sobre un fondo liso.

Hermana de scripts/expediente3d/capa_expediente3d.py (vertical, sin tocar). Fijo en todo el video:
  · arriba-izq. logo AVIL·ES · arriba-der. «CAP. N · TÍTULO» · barra de avance
  · abajo: subtítulos karaoke (palabras de la voz verificada) · «Fuente: …» del beat · firma @avil.es
Sin cuadrícula (el operador, 14-oct) y sin «Comenta …» (ADECUACION B10).
Tarjetas por beat (guion.json → beat.tarjeta):
  {"tipo": "dato", "cifra": "2.29 s", "texto": "...", "palabra": "segundos", "lado": "izq"}
  {"tipo": "matriz", "filas": [2, 3, 4], "palabras": ["GitLab", …]}   ← demos/salidas/matriz_patron.json
Uso como módulo: Capa16x9(dir).cuadro(t, avance) -> Image RGBA ; Capa16x9.fondo() -> fondo liso RGB.
"""
from __future__ import annotations
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1920, 1080
FW = "C:/Windows/Fonts/"
SEG, SEGB, MONO = "segoeui.ttf", "segoeuib.ttf", "CascadiaCode.ttf"
BLANCO, AMAR, CIAN, ROJO, GRIS, VERDE = (255, 255, 255), (255, 214, 64), (86, 214, 196), (255, 84, 70), (170, 178, 196), (120, 230, 150)
_F: dict = {}


def fnt(n, s):
    if (n, s) not in _F:
        _F[(n, s)] = ImageFont.truetype(FW + n, s)
    return _F[(n, s)]


def ease(x):
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def vis(t, a, b, fi=0.3, fo=0.3):
    if t < a or t > b:
        return 0.0
    return min(ease((t - a) / fi), ease((b - t) / fo))


def texto(img, xy, s, font, col, a, anchor="mm", sombra=True):
    if a <= 0:
        return
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    if sombra:
        d.text((xy[0] + 2, xy[1] + 4), s, font=font, fill=(0, 0, 0, int(170 * a)), anchor=anchor)
        lay = lay.filter(ImageFilter.GaussianBlur(4))
        d = ImageDraw.Draw(lay)
    d.text(xy, s, font=font, fill=col + (int(255 * a),), anchor=anchor)
    img.alpha_composite(lay)


def caja(img, xy, a, borde=CIAN, radio=22, relleno=(12, 15, 24), alfa=215, sombra=True):
    if a <= 0:
        return
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    x0, y0, x1, y1 = xy
    if sombra:
        ImageDraw.Draw(lay).rounded_rectangle((x0 + 5, y0 + 10, x1 + 5, y1 + 10), radio, fill=(0, 0, 0, int(140 * a)))
        lay = lay.filter(ImageFilter.GaussianBlur(12))
    ImageDraw.Draw(lay).rounded_rectangle(xy, radio, fill=relleno + (int(alfa * a),), outline=borde + (int(110 * a),), width=3)
    img.alpha_composite(lay)


def envolver(d, s, font, ancho):
    lineas, act = [], ""
    for w in s.split():
        prueba = (act + " " + w).strip()
        if d.textlength(prueba, font=font) <= ancho:
            act = prueba
        else:
            lineas.append(act)
            act = w
    return lineas + ([act] if act else [])


def norm(w):
    return w.strip("¿?¡!.,;:«»\"'()").lower()


class Capa16x9:
    def __init__(self, d: Path):
        self.D = Path(d)
        self.TL = json.loads((self.D / "linea_tiempo.json").read_text(encoding="utf-8"))
        self.G = json.loads((self.D / "guion.json").read_text(encoding="utf-8"))
        self.B = {b["id"]: b for b in self.TL["beats"]}
        self.GB = {b["id"]: b for b in self.G["beats"]}
        self.ETQ = self.G.get("etiquetas_fuente", {})
        # subtítulos con la ORTOGRAFÍA DEL GUION: si whisper oyó el mismo número de palabras que tiene el texto del beat,
        # se toma cada palabra del guion con el tiempo de whisper (whisper escribe «CloudStrike», el guion «CrowdStrike»)
        self.PAL = []
        for bt in self.TL["beats"]:
            oidas = [p for p in self.TL["palabras"] if p["beat"] == bt["id"]]
            esc = [w for w in self.GB[bt["id"]].get("texto", "").split() if any(c.isalnum() for c in w)]
            if len(esc) == len(oidas):
                oidas = [dict(p, w=w) for p, w in zip(oidas, esc)]
            self.PAL += oidas
        self.grupos, g = [], []
        for i, p in enumerate(self.PAL):
            g.append(p)
            sig = self.PAL[i + 1] if i + 1 < len(self.PAL) else None
            if len(g) == 6 or p["w"].rstrip()[-1:] in ".,?!:;" or sig is None or sig["beat"] != p["beat"]:
                self.grupos.append(g)
                g = []
        self.matriz = None
        for cand in (self.D / "demos/salidas/matriz_patron.json",
                     Path(self.G.get("muestra_de", "")).parent / "demos/salidas/matriz_patron.json"):
            if cand.is_file():
                self.matriz = json.loads(cand.read_text(encoding="utf-8"))
                break

    # ── utilidades ──
    def t_pal(self, beat, palabra=None, n=1, desfase=0.0):
        if palabra:
            k = 0
            for p in self.PAL:
                if p["beat"] == beat and norm(p["w"]) == norm(palabra):
                    k += 1
                    if k == n:
                        return p["ini"] + desfase
        return self.B[beat]["ini"] + desfase

    def beat_en(self, t):
        for b in self.TL["beats"]:
            if b["ini"] <= t <= b["fin"]:
                return b
        cerca = [b for b in self.TL["beats"] if b["ini"] - 0.5 <= t <= b["fin"] + 0.5]
        if cerca:
            return min(cerca, key=lambda b: min(abs(t - b["ini"]), abs(t - b["fin"])))
        prev = [b for b in self.TL["beats"] if b["fin"] <= t]
        return prev[-1] if prev else self.TL["beats"][0]

    def es_3d(self, beat_id):
        return bool(self.GB[beat_id].get("estacion"))

    @staticmethod
    def fondo() -> Image.Image:
        """Fondo liso de los beats 2D: degradado azul noche + viñeta. Sin cuadrícula."""
        g = Image.linear_gradient("L").resize((W, H))
        base = Image.merge("RGB", [g.point(lambda v: 6 + v * 8 // 255), g.point(lambda v: 8 + v * 10 // 255),
                                   g.point(lambda v: 14 + v * 18 // 255)])
        halo = Image.radial_gradient("L").resize((W, H))
        luz = Image.new("RGB", (W, H), (22, 34, 58))
        return Image.composite(base, luz, halo.point(lambda v: min(255, 90 + v)))

    # ── elementos fijos ──
    def fijos(self, img, a, avance, b):
        d = ImageDraw.Draw(img)
        x, y, f = 56, 34, fnt(MONO, 40)
        for s, col in (("AVIL", CIAN), ("·", (140, 146, 160)), ("ES", (240, 218, 80))):
            d.text((x, y), s, font=f, fill=col + (int(255 * a),))
            x += d.textlength(s, font=f)
        gb = self.GB[b["id"]]
        cap = "CAP. %s · %s" % (gb.get("capitulo", ""), gb.get("capitulo_titulo", "").upper())
        texto(img, (W - 56, 58), cap, fnt(SEGB, 28), ROJO, a, anchor="rm")
        d.rounded_rectangle((56, 104, W - 56, 109), 3, fill=(255, 255, 255, int(40 * a)))
        d.rounded_rectangle((56, 104, 56 + (W - 112) * max(0.0, min(1.0, avance)), 109), 3, fill=ROJO + (int(220 * a),))
        texto(img, (W - 56, H - 40), "@avil.es", fnt(MONO, 32), CIAN, a, anchor="rm", sombra=False)

    def fuente(self, img, t, b):
        gb = self.GB[b["id"]]
        alias = [x for x in gb.get("fuente", "").split(",") if x]
        if (gb.get("tarjeta") or {}).get("tipo") == "matriz":
            todos = [ci["alias"] for f in self.matriz["matriz"].values() for c in f.values() for ci in c["citas"]]                 if self.matriz else alias
            s = "Análisis propio sobre fuentes oficiales: " + ", ".join(
                dict.fromkeys(self.ETQ[x].split(",")[0] for x in todos if x in self.ETQ))
        else:
            s = "Fuente: " + " · ".join(self.ETQ[x] for x in alias if x in self.ETQ)
        d = ImageDraw.Draw(img)
        ls = envolver(d, s, fnt(SEG, 24), W - 112 - 260)[:1]
        if ls:
            texto(img, (56, H - 40), ls[0], fnt(SEG, 24), GRIS, vis(t, b["ini"] - 0.2, (self.TL["total"] + 1) if b["id"] == self.TL["beats"][-1]["id"] else b["fin"] + 0.4) * 0.95, anchor="lm")

    def subtitulos(self, img, t, beat):
        for gr in self.grupos:
            if gr[0]["beat"] != beat:
                continue
            if gr[0]["ini"] - 0.05 <= t <= gr[-1]["fin"] + 0.25:
                f = fnt(SEGB, 50)
                d = ImageDraw.Draw(img)
                pal = [p["w"].strip() for p in gr]
                anchos = [d.textlength(w + " ", font=f) for w in pal]
                x = (W - sum(anchos)) / 2
                for p, w, an in zip(gr, pal, anchos):
                    activo = p["ini"] - 0.03 <= t <= p["fin"] + 0.08
                    d.text((x, H - 128), w, font=f, fill=(AMAR if activo else BLANCO) + (255,), anchor="lm",
                           stroke_width=6, stroke_fill=(0, 0, 0, 230))
                    x += an
                return

    # ── tarjetas ──
    def t_dato(self, img, t, b, tj):
        t0 = self.t_pal(b["id"], tj.get("palabra"), tj.get("n", 1))
        a = vis(t, t0, b["fin"] + 0.3)
        if a <= 0:
            return
        esc = 0.86 + 0.14 * ease((t - t0) / 0.35)
        x0, x1 = (90, 830) if tj.get("lado", "izq") == "izq" else (W - 830, W - 90)
        caja(img, (x0, 300, x1, 650), a, AMAR)
        cx = (x0 + x1) / 2
        texto(img, (cx, 430), tj["cifra"], fnt(SEGB, int(150 * esc)), AMAR, a)
        d = ImageDraw.Draw(img)
        for i, s in enumerate(envolver(d, tj.get("texto", ""), fnt(SEG, 36), x1 - x0 - 90)[:3]):
            texto(img, (cx, 540 + 46 * i), s, fnt(SEG, 36), BLANCO, a)

    def t_matriz(self, img, t, b, tj):
        if not self.matriz:
            return
        M, conds = self.matriz["matriz"], list(self.matriz["condiciones"].keys())
        casos = list(M.keys())
        ultimo = b["id"] == self.TL["beats"][-1]["id"]
        a0 = vis(t, b["ini"] - 0.45, self.TL["total"] + 1 if ultimo else b["fin"] + 0.6, fi=0.45)
        texto(img, (W / 2, 150), "EL PATRÓN · 5 casos × 3 preguntas", fnt(SEGB, 40), BLANCO, a0)
        x0, wc, y0, hf = 80, 330, 250, 118
        wcol = (W - 2 * x0 - wc) / 3
        for j, k in enumerate(conds):
            texto(img, (x0 + wc + wcol * (j + 0.5), y0 - 32), self.matriz["condiciones"][k], fnt(SEGB, 28), CIAN, a0)
        filas, pals = tj.get("filas", []), tj.get("palabras", [])
        d = ImageDraw.Draw(img)
        for i, caso in enumerate(casos):
            y = y0 + i * hf
            nombre, _, anio = caso.rpartition(" ")
            texto(img, (x0 + 12, y + hf / 2 - 16), nombre, fnt(SEGB, 30), BLANCO, a0, anchor="lm")
            texto(img, (x0 + 12, y + hf / 2 + 20), anio, fnt(SEG, 24), GRIS, a0, anchor="lm")
            if i in filas:
                k_ = filas.index(i)
                ti = self.t_pal(b["id"], pals[k_] if k_ < len(pals) else None, desfase=0 if k_ < len(pals) else 0.8 * k_)
            elif filas and i < min(filas):
                ti = b["ini"] - 1.0            # filas ya llenadas en el beat anterior
            else:
                ti = None
            for j, k in enumerate(conds):
                cx0 = x0 + wc + wcol * j + 8
                caj = (cx0, y + 8, cx0 + wcol - 16, y + hf - 8)
                caja(img, caj, a0 * 0.55, (60, 70, 90), radio=14, relleno=(16, 20, 32), alfa=170, sombra=False)
                if ti is None:
                    continue
                a = a0 * ease((t - ti - 0.3 * j) / 0.35)
                if a <= 0:
                    continue
                cel = M[caso][k]
                inf = "inferencia" in cel
                col = AMAR if inf else VERDE
                caja(img, caj, a, col, radio=14, relleno=(18, 30, 30) if not inf else (36, 30, 14), alfa=200, sombra=False)
                texto(img, (caj[0] + 18, caj[1] + 26), "SÍ" + (" *" if inf else ""), fnt(SEGB, 28), col, a, anchor="lm", sombra=False)
                for n, s in enumerate(envolver(d, cel["texto"], fnt(SEG, 21), caj[2] - caj[0] - 30)[:2]):
                    texto(img, (caj[0] + 18, caj[1] + 60 + 26 * n), s, fnt(SEG, 21), BLANCO, a, anchor="lm", sombra=False)
        texto(img, (x0 + 12, y0 + 5 * hf + 26), "* lectura nuestra (inferencia marcada) · celda → cita en MATRIZ_PATRON.md",
              fnt(SEG, 24), AMAR, a0 * 0.9, anchor="lm", sombra=False)

    def cuadro(self, t: float, avance: float) -> Image.Image:
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        b = self.beat_en(t)
        a = vis(t, 0.05, self.TL["total"] + 10, fi=0.4)
        self.fijos(img, a, avance, b)
        tj = self.GB[b["id"]].get("tarjeta") or {}
        if tj.get("tipo") == "dato":
            self.t_dato(img, t, b, tj)
        elif tj.get("tipo") == "matriz":
            self.t_matriz(img, t, b, tj)
        self.fuente(img, t, b)
        self.subtitulos(img, t, b["id"])
        return img
