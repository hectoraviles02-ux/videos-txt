# -*- coding: utf-8 -*-
"""Capa 2D genérica de los Expedientes 3D (Pillow, RGBA transparente sobre los cuadros de Blender).

Todo sale del guion.json de la pieza; cada beat puede traer una «tarjeta»:
  {"tipo": "gancho", "titulo": "...", "sub": "..."}
  {"tipo": "dato", "cifra": "440 M USD", "texto": "...", "palabra": "perdió"}       ← aparece en esa palabra
  {"tipo": "lista", "items": ["...", "..."], "palabras": ["uno", "dos"]}
  {"tipo": "codigo", "lineas": ["...", "..."], "salida": ["...", "..."]}           ← terminal con tecleo
  {"tipo": "cierre", "cta": "EXPEDIENTE"}                                          ← «Comenta …» + firma
  {"tipo": "medidor", …}  ← mediciones en vivo estilo garrafón (ver Capa.medidor)
Fijo en todo el video: encabezado «EXPEDIENTE NN · TEMA» + barra de avance, logo AVIL·ES, subtítulos
karaoke (palabras de la voz verificada) y la FUENTE del dato en pantalla (regla: todo dato es trazable).
Sin cuadrícula (operador, 14-oct).

Se usa como módulo desde componer.py: Capa(dir).cuadro(t_fuente, avance_0a1) -> Image RGBA.
Prueba suelta: python capa_expediente3d.py <dir> 3.5 20.0 …  → <dir>/prueba/capa_<t>.png
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1920
FW = "C:/Windows/Fonts/"
SEG, SEGB, MONO = "segoeui.ttf", "segoeuib.ttf", "CascadiaCode.ttf"
BLANCO, AMAR, CIAN, ROJO, GRIS = (255, 255, 255), (255, 214, 64), (86, 214, 196), (255, 84, 70), (170, 178, 196)
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
        d.text((xy[0] + 3, xy[1] + 5), s, font=font, fill=(0, 0, 0, int(180 * a)), anchor=anchor)
        lay = lay.filter(ImageFilter.GaussianBlur(5))
        d = ImageDraw.Draw(lay)
    d.text(xy, s, font=font, fill=col + (int(255 * a),), anchor=anchor)
    img.alpha_composite(lay)


def tarjeta(img, caja, a, borde=CIAN):
    if a <= 0:
        return
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    x0, y0, x1, y1 = caja
    ImageDraw.Draw(lay).rounded_rectangle((x0 + 6, y0 + 14, x1 + 6, y1 + 14), 30, fill=(0, 0, 0, int(150 * a)))
    lay = lay.filter(ImageFilter.GaussianBlur(16))
    ImageDraw.Draw(lay).rounded_rectangle(caja, 30, fill=(12, 15, 24, int(215 * a)),
                                          outline=borde + (int(90 * a),), width=3)
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


class Capa:
    def __init__(self, d: Path):
        self.D = Path(d)
        self.TL = json.loads((self.D / "linea_tiempo.json").read_text(encoding="utf-8"))
        self.G = json.loads((self.D / "guion.json").read_text(encoding="utf-8"))
        self.B = {b["id"]: b for b in self.TL["beats"]}
        self.GB = {b["id"]: b for b in self.G["beats"]}
        # whisper parte cifras («1» + «.500»): se pegan a la palabra anterior para que el subtítulo diga «1.500»
        pal = []
        for p in self.TL["palabras"]:
            w = p["w"].strip()
            if pal and w[:1] in ".," and w[1:2].isdigit() and pal[-1]["w"].strip()[-1:].isdigit() and pal[-1]["beat"] == p["beat"]:
                pal[-1] = dict(pal[-1], w=pal[-1]["w"].rstrip() + w, fin=p["fin"])
            else:
                pal.append(p)
        self.PAL = pal
        self.ETQ = self.G.get("etiquetas_fuente", {})  # {"A": "Orden de la SEC, 16-oct-2013", …}
        # subtítulos: grupos de hasta 4 palabras, cortando en puntuación o cambio de beat
        self.grupos, g = [], []
        for i, p in enumerate(self.PAL):
            g.append(p)
            sig = self.PAL[i + 1] if i + 1 < len(self.PAL) else None
            if len(g) == 4 or p["w"].rstrip()[-1:] in ".,?!:;" or sig is None or sig["beat"] != p["beat"]:
                self.grupos.append(g)
                g = []

    # tiempo de la n-ésima palabra del beat (o inicio del beat + desfase)
    def t_pal(self, beat, palabra=None, n=1, desfase=0.0):
        if palabra:
            k = 0
            for p in self.PAL:
                if p["beat"] == beat and p["w"].strip("¿?¡!.,;:«»\"'()").lower() == palabra.lower():
                    k += 1
                    if k == n:
                        return p["ini"] + desfase
        return self.B[beat]["ini"] + desfase

    def beat_en(self, t):
        for b in self.TL["beats"]:          # el que contiene t gana (en la versión corta los tramos se pegan)
            if b["ini"] <= t <= b["fin"]:
                return b
        cerca = [b for b in self.TL["beats"] if b["ini"] - 0.4 <= t <= b["fin"] + 0.4]
        return min(cerca, key=lambda b: min(abs(t - b["ini"]), abs(t - b["fin"]))) if cerca else None

    # ── elementos fijos ──
    def logo(self, img, a):
        d = ImageDraw.Draw(img)
        f = fnt(MONO, 44)
        x, y = 48, 60
        for s, col in (("AVIL", CIAN), ("·", (140, 146, 160)), ("ES", (240, 218, 80))):
            d.text((x, y), s, font=f, fill=col + (int(255 * a),))
            x += d.textlength(s, font=f)

    def encabezado(self, img, a, avance):
        texto(img, (W - 48, 84), self.G.get("serie", "EXPEDIENTE"), fnt(SEGB, 34), ROJO, a, anchor="rm")
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((48, 138, W - 48, 146), 4, fill=(255, 255, 255, int(45 * a)))
        d.rounded_rectangle((48, 138, 48 + (W - 96) * max(0.0, min(1.0, avance)), 146), 4,
                            fill=ROJO + (int(230 * a),))

    def fuente(self, img, t, b):
        f = self.GB[b["id"]].get("fuente", "")
        etq = " · ".join(self.ETQ[x] for x in f.split(",") if x.strip() in self.ETQ)
        if etq:
            d = ImageDraw.Draw(img)
            ls = envolver(d, "Fuente: " + etq, fnt(SEG, 30), W - 140)[:2]
            if (self.G.get("capa") or {}).get("fondo_fuente"):  # opcional (22-oct): se leía mal sobre el planeta claro
                a = vis(t, b["ini"], b["fin"] + 0.3)
                anchos = max(d.textlength(x, font=fnt(SEG, 30)) for x in ls)
                y0 = 1500 - 19 * (len(ls) - 1) - 26
                fondo = Image.new("RGBA", img.size, (0, 0, 0, 0))
                ImageDraw.Draw(fondo).rounded_rectangle((W / 2 - anchos / 2 - 24, y0, W / 2 + anchos / 2 + 24,
                                                         y0 + 38 * len(ls) + 14), 14, fill=(8, 10, 16, int(190 * a)))
                img.alpha_composite(fondo)
            for i, l in enumerate(ls):
                texto(img, (W / 2, 1500 + 38 * i - 19 * (len(ls) - 1)), l, fnt(SEG, 30), GRIS,
                      vis(t, b["ini"], b["fin"] + 0.3) * 0.95)

    def subtitulos(self, img, t, beat=None):
        for gr in self.grupos:
            if beat and gr[0]["beat"] != beat:
                continue
            if gr[0]["ini"] - 0.05 <= t <= gr[-1]["fin"] + 0.25:
                f = fnt(SEGB, 64)
                d = ImageDraw.Draw(img)
                pal = [p["w"].strip() for p in gr]
                anchos = [d.textlength(w + " ", font=f) for w in pal]
                x = (W - sum(anchos)) / 2
                for p, w, an in zip(gr, pal, anchos):
                    activo = p["ini"] - 0.03 <= t <= p["fin"] + 0.08
                    d.text((x, 1610), w, font=f, fill=(AMAR if activo else BLANCO) + (255,), anchor="lm",
                           stroke_width=7, stroke_fill=(0, 0, 0, 230))
                    x += an
                return

    # ── tarjetas ──
    def tarjeta_beat(self, img, t, b):
        tj = self.GB[b["id"]].get("tarjeta")
        if not tj:
            return
        bid, fin = b["id"], b["fin"] + 0.25
        tipo = tj["tipo"]
        d = ImageDraw.Draw(img)
        if tipo == "gancho":
            a = vis(t, b["ini"] - 0.2, fin)
            fz = fnt(SEGB, 92)
            ls = envolver(d, tj["titulo"], fz, W - 160)
            alto = 110 * len(ls) + (80 if tj.get("sub") else 0) + 70
            y0 = 330
            tarjeta(img, (60, y0, W - 60, y0 + alto), a, ROJO)
            for i, s in enumerate(ls):
                texto(img, (W / 2, y0 + 80 + 110 * i), s, fz, BLANCO, a)
            if tj.get("sub"):
                texto(img, (W / 2, y0 + 70 + 110 * len(ls)), tj["sub"], fnt(SEG, 42), AMAR, a)
        elif tipo == "dato":
            t0 = self.t_pal(bid, tj.get("palabra"), tj.get("n", 1))
            a = vis(t, t0, fin)
            esc = 0.85 + 0.15 * ease((t - t0) / 0.35)
            tarjeta(img, (80, 330, W - 80, 760), a, AMAR)
            texto(img, (W / 2, 500), tj["cifra"], fnt(SEGB, int(150 * esc)), AMAR, a)
            for i, s in enumerate(envolver(d, tj.get("texto", ""), fnt(SEG, 46), W - 220)[:3]):
                texto(img, (W / 2, 625 + 56 * i), s, fnt(SEG, 46), BLANCO, a)
        elif tipo == "lista":
            items, pals = tj["items"], tj.get("palabras", [])
            a0 = vis(t, b["ini"], fin)
            tarjeta(img, (70, 300, W - 70, 330 + 120 * len(items) + 40), a0, CIAN)
            for i, s in enumerate(items):
                ti = self.t_pal(bid, pals[i] if i < len(pals) else None, desfase=0 if i < len(pals) else 0.8 * i)
                a = vis(t, ti, fin) * a0
                texto(img, (120, 390 + 120 * i), "•  " + s, fnt(SEGB, 50), BLANCO, a, anchor="lm")
        elif tipo == "codigo":
            lin, sal = tj["lineas"], tj.get("salida", [])
            a = vis(t, b["ini"], fin)
            y1 = 300 + 70 * (len(lin) + len(sal)) + 150
            tarjeta(img, (50, 280, W - 50, y1), a, CIAN)
            for k, col in enumerate(((255, 95, 87), (255, 189, 46), (40, 200, 64))):
                d.ellipse((90 + 40 * k, 310, 112 + 40 * k, 332), fill=col + (int(255 * a),))
            total = sum(len(s) for s in lin)
            dur = max(1.0, (b["fin"] - b["ini"]) * 0.55)
            n = round(total * ease((t - b["ini"]) / dur))
            y = 380
            for s in lin:
                vis_s = s[:max(0, n)]
                n -= len(s)
                texto(img, (95, y), vis_s, fnt(MONO, 40), (230, 232, 240), a, anchor="lm", sombra=False)
                y += 70
            if n >= 0:
                for s in sal:
                    texto(img, (95, y + 20), s, fnt(MONO, 40), (120, 230, 150), a, anchor="lm", sombra=False)
                    y += 70
        elif tipo == "medidor":
            self.medidor(img, t, b, tj, fin)
        elif tipo == "cierre":
            a = vis(t, b["ini"], self.TL["total"])
            tarjeta(img, (110, 640, W - 110, 1080), a, CIAN)
            texto(img, (W / 2, 740), "CASO CERRADO", fnt(SEGB, 84), ROJO, a)
            # cta_linea1/2 (22-oct-2026): cierre sin pedir comentarios (Meta trata «comenta X» como engagement bait).
            # Sin esas llaves queda igual que antes.
            texto(img, (W / 2, 860), tj.get("cta_linea1", "Comenta «%s»" % tj.get("cta", "EXPEDIENTE")), fnt(SEGB, 58), AMAR, a)
            texto(img, (W / 2, 935), tj.get("cta_linea2", "y te digo cuál sigue"), fnt(SEG, 44), BLANCO, a)
            texto(img, (W / 2, 1745), "¿Te sirvió?  Sígueme para más", fnt(MONO, 40), AMAR, a)
            texto(img, (W / 2, 1835), "@avil.es", fnt(MONO, 60), CIAN, a, sombra=False)

    def medidor(self, img, t, b, tj, fin):
        """Mediciones EN VIVO al estilo del garrafón (22-oct-2026, aprobado por operador en la muestra de Mars):
        {"tipo": "medidor", "titulo": "ALTURA AL PASAR POR MARTE", "unidad": "km", "max": 250,
         "pasos": [{"valor": 226, "etiqueta": "plan de la NASA", "palabra": "plan", "color": "cian"}, …],
         "limite": {"valor": 80, "etiqueta": "mínimo para sobrevivir", "palabra": "mínimo"},
         "cuentas": [{"texto": "plan = 226 km", "palabra": "plan", "color": "cian"}, …], "nota": "…"}
        El contador va del valor anterior al nuevo cuando la voz dice la palabra del paso; el altímetro se llena igual."""
        bid, d = b["id"], ImageDraw.Draw(img)
        a = vis(t, b["ini"] - 0.2, fin)
        if a <= 0:
            return
        col_de = {"cian": (38, 217, 255), "ambar": (255, 153, 26), "rojo": ROJO, "blanco": BLANCO}
        pasos = [dict(p, t0=self.t_pal(bid, p.get("palabra"), p.get("n", 1))) for p in tj.get("pasos", [])]
        valor, col, etq = None, col_de["cian"], ""
        for i, p in enumerate(pasos):
            if t >= p["t0"] - 0.2:
                previo = pasos[i - 1]["valor"] if i else 0
                valor = previo + (p["valor"] - previo) * ease((t - p["t0"] + 0.2) / 0.8)
                col, etq = col_de.get(p.get("color"), col_de["cian"]), p.get("etiqueta", "")
        unidad, vmax = tj.get("unidad", ""), float(tj.get("max", 100))
        tarjeta(img, (40, 175, W - 40, 775), a, (60, 70, 90))
        texto(img, (80, 220), tj.get("titulo", ""), fnt(MONO, 34), GRIS, a, anchor="lm", sombra=False)
        texto(img, (80, 340), ("—" if valor is None else "%d" % round(valor)) + " " + unidad, fnt(SEGB, 140), col, a,
              anchor="lm")
        texto(img, (86, 450), etq, fnt(SEG, 40), col, a, anchor="lm", sombra=False)
        lim = tj.get("limite")
        t_lim = self.t_pal(bid, lim.get("palabra"), lim.get("n", 1)) if lim else None
        a_lim = ease((t - t_lim) / 0.4) * a if lim else 0.0
        if a_lim > 0:
            texto(img, (86, 530), lim.get("etiqueta", ""), fnt(SEG, 38), BLANCO, a_lim, anchor="lm", sombra=False)
            texto(img, (86, 610), "%d %s" % (lim["valor"], unidad), fnt(SEGB, 90), ROJO, a_lim, anchor="lm", sombra=False)
        # altímetro (como el garrafón que se vacía)
        gx0, gx1, gy0, gy1 = 760, 860, 215, 735
        y_de = lambda v: gy1 - (gy1 - gy0) * max(0.0, min(1.0, v / vmax))  # noqa: E731
        d.rectangle((gx0, gy0, gx1, gy1), outline=(120, 130, 150, int(255 * a)), width=3)
        paso_eje = max(1, round(vmax / 5))
        for v in range(0, int(vmax) + 1, paso_eje):
            d.line((gx1, y_de(v), gx1 + 14, y_de(v)), fill=GRIS + (int(255 * a),), width=3)
            d.text((gx1 + 22, y_de(v)), "%d" % v, font=fnt(MONO, 26), fill=GRIS + (int(255 * a),), anchor="lm")
        if pasos and valor is not None:
            if pasos[0].get("color", "cian") == "cian":  # marca fija del primer valor (el plan) solo si es el plan
                d.line((gx0 - 30, y_de(pasos[0]["valor"]), gx0, y_de(pasos[0]["valor"])), fill=col_de["cian"] + (int(255 * a),), width=6)
            if valor > 1:
                d.rectangle((gx0 + 3, y_de(valor), gx1 - 3, gy1 - 3), fill=col + (int(140 * a),))
            d.line((gx0 - 4, y_de(valor), gx1 + 4, y_de(valor)), fill=col + (int(255 * a),), width=6)
        if a_lim > 0:
            zona = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(zona).rectangle((gx0 + 3, y_de(lim["valor"]), gx1 - 3, gy1 - 3), fill=(255, 40, 30, int(110 * a_lim)))
            img.alpha_composite(zona)
            ImageDraw.Draw(img).line((gx0 - 30, y_de(lim["valor"]), gx1, y_de(lim["valor"])), fill=ROJO + (int(255 * a_lim),), width=6)
        if tj.get("nota"):
            texto(img, (W / 2, 805), tj["nota"], fnt(SEG, 28), GRIS, a, sombra=False)
        # la cuenta (como la del garrafón): la última que ya se dijo
        vistas = [c for c in tj.get("cuentas", []) if t >= self.t_pal(bid, c.get("palabra"), c.get("n", 1)) - 0.2]
        if vistas:
            c = vistas[-1]
            tarjeta(img, (40, 1340, W - 40, 1450), a, (60, 70, 90))
            texto(img, (W / 2, 1395), c["texto"], fnt(MONO, 46), col_de.get(c.get("color"), BLANCO), a, sombra=False)

    def cuadro(self, t: float, avance: float, beat_id: str | None = None) -> Image.Image:
        """beat_id: en la versión corta el ensamblador dice a qué beat pertenece el cuadro (en los bordes del
        corte, t cae en la pausa entre frases y el beat «más cercano» puede ser uno que no está en el corte)."""
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        a = vis(t, 0.1, self.TL["total"] + 10)
        self.logo(img, a)
        self.encabezado(img, a, avance)
        b = self.B[beat_id] if beat_id else self.beat_en(t)
        if b:
            self.tarjeta_beat(img, t, b)
            if (self.GB[b["id"]].get("tarjeta") or {}).get("tipo") != "cierre":
                self.fuente(img, t, b)
        self.subtitulos(img, t, b["id"] if b else None)
        return img


if __name__ == "__main__":
    c = Capa(Path(sys.argv[1]))
    (c.D / "prueba").mkdir(exist_ok=True)
    for s in sys.argv[2:]:
        t = float(s)
        c.cuadro(t, t / c.TL["total"]).save(c.D / "prueba" / ("capa_%05.1f.png" % t))
