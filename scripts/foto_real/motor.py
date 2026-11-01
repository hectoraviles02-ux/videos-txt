# -*- coding: utf-8 -*-
"""Motor «foto real» (31-oct-2026): reels con fotos y fragmentos de video REALES, cortados al ritmo de la voz.

Todo sale de <pieza>/guion.json. Además de lo que ya pide la voz (fuentes, tramos con cita_fuente), trae:
  "planos": [ { "desde": ["t2", "palabra", n?] | 0.0,   # arranca en esa palabra de la voz (o en ese segundo)
                "medio": "<id en biblioteca_audiovisual/_manifiesto.json>",
                "foco": [0.5, 0.4],          # punto de interés (fracción del ancho/alto de la foto)
                "mov": "acercar|alejar|paneo_izq|paneo_der|golpe|temblor",
                "modo": "llenar|encajar|vs",  # vs: mitad arriba «medio», mitad abajo «medio_b»
                "medio_b": "...", "foco_b": [...],
                "clip_ini": 12.0,            # solo video: segundo del original donde arranca el fragmento
                "vel": 1.0,                  # solo video: velocidad
                "texto": "...", "estilo": "sello|banner|cifra|pregunta",
                "circulo": [0.62, 0.35, 0.12], # círculo rojo que se dibuja solo (x, y, radio) sobre la foto
                "nota": "FOTO DE ARCHIVO"     # rótulo fijo junto al crédito mientras dura el plano (archivo/ilustrativa)
              } , ... ]
  "cierre": {"pregunta": "...", "fuentes": "..."}
Cada plano dura hasta que empieza el siguiente. El crédito de la foto o video en pantalla sale del manifiesto
(atribucion_en_pantalla), así que no se puede usar un medio sin licencia registrada.

Uso: python scripts/foto_real/motor.py <carpeta_pieza> [--muestras]
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import unicodedata
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import medios  # noqa: E402

W, H, FPS = 1080, 1920, 30
FONTS = Path("C:/Windows/Fonts")
AMARILLO, ROJO, BLANCO, NEGRO = (255, 222, 0), (235, 30, 45), (255, 255, 255), (0, 0, 0)
_f = {}


def fuente(nombre, px):
    k = (nombre, int(px))
    if k not in _f:
        _f[k] = ImageFont.truetype(str(FONTS / nombre), int(px))
    return _f[k]


def norm(s):
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if c.isalnum() and unicodedata.category(c) != "Mn")


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease(x):
    return 1 - (1 - clamp(x)) ** 3


def rebote(x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    return 1 + 2.2 * (x - 1) ** 3 + 1.2 * (x - 1) ** 2


# ------------------------------------------------------------------ pieza y tiempos
class Pieza:
    def __init__(self, carpeta):
        self.dir = Path(carpeta)
        self.g = json.loads((self.dir / "guion.json").read_text(encoding="utf-8"))
        self.ti = json.loads((self.dir / "tiempos.json").read_text(encoding="utf-8"))
        self.tramos = {t["id"]: t for t in self.ti["tramos"]}
        self.voz_fin = self.ti["duracion"]
        self.dur = self.voz_fin + 2.2          # cola para la pregunta final

    def pal(self, tid, pref, n=1):
        k = 0
        for p in self.tramos[tid]["palabras"]:
            if norm(p["w"]).startswith(norm(pref)):
                k += 1
                if k == n:
                    return p["ini"]
        raise KeyError(f"palabra «{pref}» #{n} no está en {tid}")

    def momento(self, desde):
        if isinstance(desde, (int, float)):
            return float(desde)
        if len(desde) == 1:
            return self.tramos[desde[0]]["ini"]
        return self.pal(*desde)

    def palabras(self):
        return [p for t in self.ti["tramos"] for p in t["palabras"]]


# ------------------------------------------------------------------ medios
class Foto:
    def __init__(self, reg):
        self.reg = reg
        im = Image.open(reg["archivo_local"]).convert("RGB")
        im.thumbnail((2600, 2600), Image.LANCZOS)
        self.im = im
        self._fondo = None

    def fondo(self):
        if self._fondo is None:
            self._fondo = cubrir(self.im, (0.5, 0.5), 1.0).resize((W // 8, H // 8)).filter(
                ImageFilter.GaussianBlur(6)).resize((W, H), Image.BICUBIC)
            self._fondo = Image.blend(self._fondo, Image.new("RGB", (W, H), (10, 10, 14)), 0.45)
        return self._fondo

    def cuadro(self, t_rel):
        return self.im


class Clip:
    """Fragmento de video: se decodifica una sola vez (a 30 fps) el tramo que se usa."""

    def __init__(self, reg, ini, dur, vel=1.0):
        self.reg = reg
        src = reg["archivo_local"]
        info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                               "stream=width,height", "-of", "csv=p=0", src], capture_output=True, text=True).stdout
        w, h = [int(x) for x in info.strip().split(",")[:2]]
        esc = min(1.0, 1400 / max(w, h))
        self.w, self.h = int(w * esc) // 2 * 2, int(h * esc) // 2 * 2
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{ini:.3f}", "-t", f"{dur * vel + 0.5:.3f}", "-i", src,
                            "-vf", f"setpts=PTS/{vel},fps={FPS},scale={self.w}:{self.h}", "-f", "rawvideo",
                            "-pix_fmt", "rgb24", "-"], capture_output=True)
        n = len(r.stdout) // (self.w * self.h * 3)
        self.frames = np.frombuffer(r.stdout[: n * self.w * self.h * 3], np.uint8).reshape(n, self.h, self.w, 3)
        if n == 0:
            raise RuntimeError(f"no se pudo decodificar {src} desde {ini}s")
        self.im = Image.fromarray(self.frames[0])
        self._fondo = None

    def fondo(self):
        return Foto.fondo(self)

    def cuadro(self, t_rel):
        i = min(len(self.frames) - 1, max(0, int(t_rel * FPS)))
        return Image.fromarray(self.frames[i])


def cubrir(im, foco, z, salida=(W, H)):
    """Recorta de im la caja más grande con la proporción de salida, acercada z veces, centrada en foco."""
    ar = salida[0] / salida[1]
    iw, ih = im.size
    cw, ch = (iw, iw / ar) if iw / ih < ar else (ih * ar, ih)
    cw, ch = cw / z, ch / z
    cx = clamp(foco[0] * iw, cw / 2, iw - cw / 2)
    cy = clamp(foco[1] * ih, ch / 2, ih - ch / 2)
    return im.resize(salida, Image.BICUBIC, box=(cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2))


def zoom_mov(mov, x, dur):
    """Acercamiento y desplazamiento del foco según el movimiento; x es el avance 0..1 del plano."""
    if mov == "alejar":
        return 1.32 - 0.24 * ease(x), (0, 0)
    if mov == "paneo_izq":
        return 1.18, (0.10 - 0.20 * x, 0)
    if mov == "paneo_der":
        return 1.18, (-0.10 + 0.20 * x, 0)
    if mov == "golpe":
        tt = x * dur
        return 1.0 + 0.10 * x + 0.35 * max(0, 1 - tt / 0.22) ** 2, (0, 0)
    if mov == "temblor":
        tt = x * dur
        return 1.16, (0.012 * math.sin(tt * 53), 0.010 * math.cos(tt * 41))
    return 1.06 + 0.22 * ease(x), (0, 0)  # acercar


# ------------------------------------------------------------------ dibujo
def texto(d, xy, s, f, fill, anchor="mm", borde=0, color_borde=NEGRO):
    d.text(xy, s, font=f, fill=fill, anchor=anchor, stroke_width=borde, stroke_fill=color_borde)


def envolver(s, f, ancho):
    out, act = [], ""
    for w in s.split():
        p = (act + " " + w).strip()
        if f.getlength(p) <= ancho or not act:
            act = p
        else:
            out.append(act)
            act = w
    out.append(act)
    return out


def caja_texto(s, estilo, escala=1.0):
    """Etiqueta grande como imagen RGBA (sello rojo girado, banner amarillo, cifra gigante o pregunta)."""
    if estilo == "cifra":
        f = fuente("impact.ttf", 170 * escala)
        lineas = envolver(s, f, 980)
        alto = int(len(lineas) * f.size * 1.08) + 40
        im = Image.new("RGBA", (W, alto), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for i, ln in enumerate(lineas):
            texto(d, (W // 2, 20 + i * f.size * 1.08), ln, f, AMARILLO, "ma", borde=12)
        return im
    if estilo == "sello":
        f = fuente("impact.ttf", 120 * escala)
        while f.getlength(s) > 860 and f.size > 40:   # girado 9°, debe caber en el ancho
            f = fuente("impact.ttf", f.size - 6)
        tw = int(f.getlength(s)) + 70
        im = Image.new("RGBA", (tw + 40, int(f.size * 1.5) + 40), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((20, 20, tw + 20, im.height - 20), 18, outline=ROJO, width=12, fill=(255, 255, 255, 40))
        texto(d, (im.width // 2, im.height // 2), s, f, ROJO, "mm")
        return im.rotate(-9, expand=True, resample=Image.BICUBIC)
    if estilo == "pregunta":
        f = fuente("ariblk.ttf", 84 * escala)
        lineas = envolver(s, f, 920)
        alto = int(len(lineas) * f.size * 1.2) + 60
        im = Image.new("RGBA", (W, alto), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for i, ln in enumerate(lineas):
            texto(d, (W // 2, 30 + i * f.size * 1.2), ln, f, BLANCO, "ma", borde=10)
        return im
    f = fuente("ariblk.ttf", 66 * escala)   # banner
    lineas = envolver(s, f, 900)
    alto = int(len(lineas) * f.size * 1.2) + 50
    ancho = int(max(f.getlength(x) for x in lineas)) + 70
    im = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, ancho, alto), fill=AMARILLO)
    for i, ln in enumerate(lineas):
        texto(d, (ancho // 2, 25 + i * f.size * 1.2), ln, f, NEGRO, "ma")
    return im


_cajas = {}


def pegar_centrado(im, capa, cx, cy, s=1.0):
    if s <= 0.02:
        return
    if abs(s - 1) > 0.01:
        capa = capa.resize((max(1, int(capa.width * s)), max(1, int(capa.height * s))), Image.BICUBIC)
    im.paste(capa, (int(cx - capa.width / 2), int(cy - capa.height / 2)), capa)


# ------------------------------------------------------------------ armado
class Motor:
    def __init__(self, p: Pieza):
        self.p = p
        regs = {}
        planos = []
        crudos = p.g["planos"]
        for i, pl in enumerate(crudos):
            t0 = p.momento(pl["desde"])
            t1 = p.momento(crudos[i + 1]["desde"]) if i + 1 < len(crudos) else p.dur
            planos.append({**pl, "t0": t0, "t1": t1})
        for a, b in zip(planos, planos[1:]):
            if b["t0"] <= a["t0"]:
                raise ValueError(f"planos fuera de orden: {a['medio']} {a['t0']:.2f} ≥ {b['medio']} {b['t0']:.2f}")
        self.planos = planos
        for pl in planos:
            for k in ("medio", "medio_b"):
                if pl.get(k) and pl[k] not in regs:
                    regs[pl[k]] = medios.recurso(pl[k])
                    if regs[pl[k]]["derechos"] != "AUTORIZADO":
                        raise ValueError(f"{pl[k]} no está AUTORIZADO en la biblioteca")
        self.regs = regs
        self.fotos = {}
        for pl in planos:
            for k in ("medio", "medio_b"):
                rid = pl.get(k)
                if not rid:
                    continue
                if regs[rid]["tipo"] == "video":
                    pl["_clip_" + k] = Clip(regs[rid], pl.get("clip_ini", 0.0), pl["t1"] - pl["t0"], pl.get("vel", 1.0))
                elif rid not in self.fotos:
                    self.fotos[rid] = Foto(regs[rid])
        self.trozos = self._trozos()

    def _trozos(self, max_pal=3, max_letras=16):
        out, act = [], []
        for w in self.p.palabras():
            act.append(w)
            if len(act) >= max_pal or w["w"][-1] in ".,:;?!»" or sum(len(x["w"]) for x in act) > max_letras:
                out.append(act)
                act = []
        if act:
            out.append(act)
        return out

    def medio(self, pl, k):
        return pl.get("_clip_" + k) or self.fotos[pl[k]]

    def imagen_plano(self, pl, t):
        dur = pl["t1"] - pl["t0"]
        x = clamp((t - pl["t0"]) / max(dur, 0.01))
        z, (dx, dy) = zoom_mov(pl.get("mov", "acercar"), x, dur)
        modo = pl.get("modo", "llenar")
        if modo == "vs":
            out = Image.new("RGB", (W, H))
            for j, k in enumerate(("medio", "medio_b")):
                m = self.medio(pl, k)
                fo = pl.get("foco" if j == 0 else "foco_b", [0.5, 0.45])
                mitad = cubrir(m.cuadro(t - pl["t0"]), (fo[0] + dx, fo[1] + dy), z, (W, H // 2))
                out.paste(mitad, (0, j * H // 2))
            return out
        m = self.medio(pl, "medio")
        fo = pl.get("foco", [0.5, 0.45])
        fr = m.cuadro(t - pl["t0"])
        if modo == "encajar":
            out = m.fondo().copy()
            ancho = int(W * (0.98 + 0.06 * (z - 1)))
            alto = int(ancho * fr.height / fr.width)
            if alto > H * 0.62:
                alto = int(H * 0.62)
                ancho = int(alto * fr.width / fr.height)
            pegar = fr.resize((ancho, alto), Image.BICUBIC)
            out.paste(pegar, ((W - ancho) // 2 + int(dx * 300), int(H * 0.40 - alto / 2)))
            return out
        return cubrir(fr, (fo[0] + dx, fo[1] + dy), z)

    def cuadro(self, t):
        p = self.p
        i = max((k for k, pl in enumerate(self.planos) if pl["t0"] <= t), default=0)
        pl = self.planos[i]
        im = self.imagen_plano(pl, t)
        tr = t - pl["t0"]
        d = ImageDraw.Draw(im, "RGBA")
        # viñeta para que el texto se lea
        d.rectangle((0, 0, W, 260), fill=(0, 0, 0, 70))
        d.rectangle((0, H - 760, W, H), fill=(0, 0, 0, 60))
        # destello en el corte (alterna blanco y negro); más suave desde el 31-oct (revisión: «muy iluminado»)
        if i > 0 and tr < 0.12:
            a = int(110 * (1 - tr / 0.12))
            d.rectangle((0, 0, W, H), fill=(255, 255, 255, a) if i % 2 else (0, 0, 0, a))
        # círculo rojo que se dibuja solo
        if pl.get("circulo") and tr > 0.25:
            cx, cy, r = pl["circulo"]
            k = ease((tr - 0.25) / 0.45)
            R = r * W
            d.arc((cx * W - R, cy * H - R, cx * W + R, cy * H + R), -90, -90 + 360 * k, fill=ROJO, width=14)
        # texto del plano
        if pl.get("texto"):
            est = pl.get("estilo", "banner")
            clave = (pl["texto"], est)
            if clave not in _cajas:
                _cajas[clave] = caja_texto(pl["texto"], est)
            cy = {"cifra": 470, "sello": 520, "pregunta": 560, "banner": 360}[est]
            s = rebote(tr / 0.32)
            if est == "sello":
                s = 1 + 1.4 * max(0, 1 - tr / 0.16) if tr > 0 else 0
            pegar_centrado(im, _cajas[clave], W // 2, cy, s)
        d = ImageDraw.Draw(im, "RGBA")
        # subtítulo palabra por palabra
        if t < p.voz_fin + 0.2:
            for tz in self.trozos:
                if tz[0]["ini"] - 0.05 <= t < tz[-1]["fin"] + 0.12:
                    f = fuente("ariblk.ttf", 78)
                    pals = [w["w"].upper() for w in tz]
                    anchos = [f.getlength(w + " ") for w in pals]
                    esc = min(1.0, 980 / sum(anchos))
                    if esc < 1:
                        f = fuente("ariblk.ttf", int(78 * esc))
                        anchos = [f.getlength(w + " ") for w in pals]
                    x = (W - sum(anchos)) / 2
                    for w, a, wd in zip(pals, anchos, tz):
                        activa = wd["ini"] - 0.03 <= t
                        col = AMARILLO if (activa and t < wd["fin"] + 0.1) else BLANCO
                        dy = -10 * max(0, 1 - (t - wd["ini"]) / 0.12) if activa else 0
                        texto(d, (x, 1330 + dy), w, f, col, "lm", borde=9)
                        x += a
                    break
        # cierre: pregunta y fuentes
        c = p.g.get("cierre", {})
        if t >= p.voz_fin - 0.3 and c:
            a = clamp((t - p.voz_fin + 0.3) / 0.4)
            d.rectangle((0, 0, W, H), fill=(0, 0, 0, int(170 * a)))
            if "_pregunta" not in _cajas:
                _cajas["_pregunta"] = caja_texto(c["pregunta"], "pregunta")
            pegar_centrado(im, _cajas["_pregunta"], W // 2, 640, rebote(a))
            d = ImageDraw.Draw(im, "RGBA")
            f = fuente("arial.ttf", 34)
            y = 1060
            for ln in envolver("Fuentes: " + c["fuentes"], f, 940):
                texto(d, (W // 2, y), ln, f, (235, 235, 235, int(255 * a)), "ma")
                y += 44
            texto(d, (W // 2, y + 20), "Voz generada con IA · fotos reales con licencia libre", fuente("arial.ttf", 30),
                  (210, 210, 210, int(255 * a)), "ma")
        # crédito del medio (obligatorio por licencia)
        reg = self.regs[pl["medio"]]
        cred = reg.get("atribucion_en_pantalla") or ""
        if pl.get("medio_b"):
            cred += "  |  " + (self.regs[pl["medio_b"]].get("atribucion_en_pantalla") or "")
        # crédito más grande (31-oct: a 24 px no se leía en celular) y rótulo fijo del plano («nota»: FOTO DE
        # ARCHIVO / ILUSTRATIVA) mientras dura el plano, para que nunca se tome como evidencia del hecho
        f = fuente("arialbd.ttf", 30)
        lineas_cred = envolver(cred, f, 1010)[:2]
        for j, ln in enumerate(lineas_cred):
            texto(d, (W // 2, 1782 + j * 34), ln, f, (245, 245, 245), "mm", borde=3)
        if pl.get("nota"):
            fn = fuente("ariblk.ttf", 32)
            ancho = fn.getlength(pl["nota"]) + 36
            y0 = 1782 - 30 - 46
            d.rounded_rectangle((W / 2 - ancho / 2, y0, W / 2 + ancho / 2, y0 + 46), 10, fill=(0, 0, 0, 190))
            texto(d, (W // 2, y0 + 23), pl["nota"], fn, AMARILLO, "mm")
        # barra de avance y firma
        d.rectangle((0, 0, int(W * t / p.dur), 12), fill=AMARILLO)
        texto(d, (W // 2, 1880), "AVIL·ES  ·  @avil.es", fuente("bahnschrift.ttf", 30), (255, 255, 255, 220), borde=2)
        return im

    def efectos(self):
        ev = [(pl["t0"], "zum") for pl in self.planos[1:]]
        ev += [(pl["t0"] + 0.02, "sello") for pl in self.planos if pl.get("estilo") == "sello"]
        ev += [(pl["t0"] + 0.05, "ding") for pl in self.planos if pl.get("estilo") == "cifra"]
        ev += [(self.p.voz_fin - 0.3, "fanfarria")]
        return ev


# ------------------------------------------------------------------ sonido y salida
SR = 48000


def _tono(f0, f1, dur, vol, forma="sin"):
    n = int(SR * dur)
    tt = np.arange(n) / SR
    fase = 2 * np.pi * np.cumsum(np.linspace(f0, f1, n)) / SR
    s = np.sin(fase) if forma == "sin" else np.sign(np.sin(fase)) * 0.5
    return (s * np.minimum(1, tt / 0.004) * np.exp(-tt / (dur / 3)) * vol).astype(np.float32)


def efecto(tipo):
    if tipo == "zum":        # barrido de aire en cada corte
        n = int(SR * 0.22)
        r = np.random.default_rng(3).standard_normal(n).astype(np.float32)
        env = np.sin(np.linspace(0, np.pi, n)) ** 2
        k = np.ones(24, np.float32) / 24
        return (np.convolve(r, k, "same") * env * 0.35).astype(np.float32)
    if tipo == "sello":
        r = np.random.default_rng(1).standard_normal(int(SR * 0.18)).astype(np.float32)
        return (_tono(140, 60, 0.18, 0.6) + r * np.exp(-np.arange(len(r)) / (SR * 0.03)) * 0.25).astype(np.float32)
    if tipo == "ding":
        return _tono(1320, 1320, 0.5, 0.22) + _tono(1980, 1980, 0.5, 0.1)
    if tipo == "fanfarria":
        return np.concatenate([_tono(f, f, 0.12, 0.18, "cuadrada") for f in (523, 659, 784, 1046)])
    raise KeyError(tipo)


def pista_efectos(efectos, dur, ruta):
    buf = np.zeros(int(SR * (dur + 1)), np.float32)
    for t, tipo in efectos:
        s = efecto(tipo)
        i = int(max(0, t) * SR)
        buf[i:i + len(s)] += s[: max(0, len(buf) - i)]
    with wave.open(str(ruta), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(buf, -1, 1) * 32767).astype(np.int16).tobytes())


def render(m: Motor, salida: Path):
    p = m.p
    fx = p.dir / "efectos.wav"
    pista_efectos(m.efectos(), p.dur, fx)
    # tope de bits para que el MP4 quede en ~9 MB (Telegram y el bot no mandan 10 MB o más)
    tasa = f"{int((9.0e6 * 8 / p.dur - 140e3) / 1000)}k"
    ff = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
         "-i", "-", "-i", str(p.dir / "mezcla.wav"), "-i", str(fx),
         # limitador (31-oct-2026): los efectos sumados a la voz pasaban de 0 dBTP (+2.4 medido en ovnis);
         # 0.79 lineal ≈ −2 dBFS de muestra deja el pico verdadero bajo −1 dBTP
         "-filter_complex", "[1:a]apad[v];[2:a]volume=0.8[e];[v][e]amix=inputs=2:duration=longest:normalize=0,"
                            "alimiter=limit=0.63:attack=1:release=60:level=disabled[a]",
         "-map", "0:v", "-map", "[a]", "-t", f"{p.dur:.3f}",
         "-c:v", "libx264", "-preset", "slow", "-crf", "24", "-maxrate", os.environ.get("MAXRATE", tasa),
         "-bufsize", tasa, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
         str(salida)], stdin=subprocess.PIPE)
    total = int(math.ceil(p.dur * FPS))
    for i in range(total):
        ff.stdin.write(m.cuadro(i / FPS).convert("RGB").tobytes())
        if i % 300 == 0:
            print(f"cuadro {i}/{total}", flush=True)
    ff.stdin.close()
    return ff.wait()


def mosaico(m: Motor, salida: Path, n=18):
    ts = [m.p.dur * (i + 0.5) / n for i in range(n)]
    ims = [m.cuadro(t).convert("RGB").resize((270, 480)) for t in ts]
    out = Image.new("RGB", (6 * 270, math.ceil(n / 6) * 480))
    for i, im in enumerate(ims):
        out.paste(im, ((i % 6) * 270, (i // 6) * 480))
    out.save(salida, quality=85)


def main():
    carpeta = Path(sys.argv[1])
    m = Motor(Pieza(carpeta))
    if "--muestras" in sys.argv:
        mosaico(m, carpeta / "mosaico.jpg")
        print("mosaico:", carpeta / "mosaico.jpg")
        return 0
    return render(m, carpeta / "render_9x16.mp4")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
