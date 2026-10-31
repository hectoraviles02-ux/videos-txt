# -*- coding: utf-8 -*-
"""Motor «diagrama» — armador genérico (23-oct-2026). Dibuja un plan.json (escenas con piezas: insignias, número,
cuadrícula, frase, flujo, línea de tiempo, lista) sincronizado con la voz (tiempos.json), en el estilo de la
derivada: fondo azul noche, paneles redondeados, piezas que se ven tenues antes de su momento y se encienden cuando
la voz las nombra. Generaliza laboratorio/diagrama_url_20261023/video_completo.py (video de PHP aprobado por el operador
para enviar con botón). Reutiliza scripts/pizarra/componentes.py SIN modificarlo.

Regla de texto en pantalla: todo texto con un dato está LITERAL en la narración verificada (lo garantiza
plan.validar antes de llegar aquí; aquí se vuelve a comprobar y se detiene si no).
Uso: render(carpeta) -> <carpeta>/<id>_9x16.mp4
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent / "pizarra"))
import componentes as C  # noqa: E402

W, H, FPS = C.W, C.H, C.FPS
A, B, TN, R, CI, LI, VI = (C.col(x) for x in ("ambar", "blanco", "tenue", "rosa", "cian", "lima", "violeta"))
CICLO = [R, CI, A, LI, VI]
P1, P2, P3 = (40, 175, W - 40, 395), (40, 420, W - 40, 1250), (40, 1275, W - 40, 1800)
ENCABEZADOS_LIBRES = {"EL MECANISMO", "¿QUÉ HACER?", "EN CORTO", "CRONOLOGÍA", "LOS DATOS"}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(s.replace("\n", " ").split())


def es_literal(texto: str, narracion: str) -> bool:
    t = norm(texto).strip("¿?¡!.:,; ")
    return not t or t in norm(narracion)


# ── texto que cabe ─────────────────────────────────────────────────────────
def ajustar(texto: str, ancho: int, tams=(34, 32, 30, 28, 27), lineas_max=2):
    """(tam, [líneas]) que caben en `ancho`; None si no cabe ni a 27 px en 2 líneas."""
    palabras = texto.replace("\n", " ").split()
    for tam in tams:
        f = C.fuente(tam)
        lineas, act = [], ""
        for p in palabras:
            prueba = (act + " " + p).strip()
            if f.getlength(prueba) <= ancho:
                act = prueba
            else:
                if act:
                    lineas.append(act)
                act = p
        if act:
            lineas.append(act)
        if len(lineas) <= lineas_max and all(f.getlength(x) <= ancho for x in lineas):
            return tam, lineas
    return None


class Lienzo:
    def __init__(self, narracion: str, extras: list[str] | None = None):
        """extras: textos verificados que NO son de la narración pero pueden mostrarse tal cual (citas literales de
        las fuentes, nombres de los medios, fechas de publicación) — los da la investigación del enlace."""
        self.narracion = narracion
        self.extras = " ‖ ".join(extras or [])
        self.fondo = C.fondo()

    def _lit(self, s):
        if s and s not in ENCABEZADOS_LIBRES and not es_literal(s, self.narracion) and                 not (self.extras and es_literal(s, self.extras)):
            raise SystemExit(f"texto en pantalla «{s}» no está en la narración verificada ni en las fuentes")

    def texto(self, im, s, x, y, color, alfa=1.0, ancho=960, tams=(34, 32, 30, 28, 27), centro=True, lineas_max=2,
              esp=0, verificar=True):
        if verificar:
            self._lit(s)
        aj = ajustar(s, ancho, tams, lineas_max)
        if aj is None:
            raise SystemExit(f"el texto «{s}» no cabe en {ancho} px")
        if alfa <= 0.003:
            return
        tam, lineas = aj
        alto = tam * 1.25
        for i, ln in enumerate(lineas):
            yy = y + (i - (len(lineas) - 1) / 2) * alto
            C.pegar(im, C.texto_img(ln, tam, color, esp), x, yy, alfa=alfa, centro=centro)

    def caja(self, im, x, y, w, h, s, color, alfa, fantasma=0.25, relleno=None, tams=(34, 32, 30, 28, 27),
             fuente=None):
        self._lit(s)
        if fuente:
            self._lit(fuente)
        aj = ajustar(s, w - 36, tams)
        if aj is None:
            raise SystemExit(f"el texto «{s}» no cabe en una caja de {w} px")
        if fantasma:
            color, alfa = C.mezcla(TN, color, alfa), fantasma + (1 - fantasma) * alfa
        if alfa <= 0.003:
            return
        tam, lineas = aj
        capa = Image.new("RGBA", (w + 8, h + 8), (0, 0, 0, 0))
        d = ImageDraw.Draw(capa)
        d.rounded_rectangle((4, 4, w + 3, h + 3), 18, fill=relleno or C.PANEL, outline=color, width=4)
        for i, ln in enumerate(lineas):
            s_img = C.texto_img(ln, tam, color)
            capa.alpha_composite(s_img, (int((w + 8 - s_img.width) / 2),
                                         int((h + 8 - len(lineas) * tam * 1.25) / 2 + i * tam * 1.25)))
        C.pegar(im, capa, x - 4, y - 4, alfa=alfa, escala=0.92 + 0.08 * min(1.0, alfa))
        if fuente:   # de dónde sale, bajo la caja
            self.texto(im, fuente.upper(), x + w / 2, y + h + 20, TN, alfa=alfa, ancho=w, tams=(27,), lineas_max=1,
                       verificar=False)


def flecha(d, x0, y0, x1, y1, k, color, grosor=6):
    if k <= 0:
        return
    xe, ye = x0 + (x1 - x0) * k, y0 + (y1 - y0) * k
    d.line((x0, y0, xe, ye), fill=color, width=grosor)
    if k > 0.85:
        a = math.atan2(ye - y0, xe - x0)
        d.polygon([(xe, ye), (xe - 22 * math.cos(a - 0.45), ye - 22 * math.sin(a - 0.45)),
                   (xe - 22 * math.cos(a + 0.45), ye - 22 * math.sin(a + 0.45))], fill=color)


# ── tiempos ────────────────────────────────────────────────────────────────
class Tiempos:
    def __init__(self, tiempos: dict):
        self.tramos = tiempos["tramos"]
        self.duracion = tiempos["duracion"]

    def frase(self, n):
        return self.tramos[n - 1]["ini"], self.tramos[n - 1]["fin"]

    def ancla(self, n, palabra=None):
        ini, fin = self.frase(n)
        if palabra:
            p0 = norm(palabra).split()[0] if norm(palabra).split() else ""
            for p in self.tramos[n - 1]["palabras"]:
                w = norm(p["w"]).strip("¿?¡!.,:;")
                if p0 and (w.startswith(p0[:6]) or p0.startswith(w) and len(w) >= 4):
                    return p["ini"]
        return ini + 0.25


# ── piezas ─────────────────────────────────────────────────────────────────
def dibujar_arriba(L, im, d, t, pz, T):
    tipo = pz["tipo"]
    if tipo == "insignias":
        items = pz["items"][:3]
        xs = {1: [W / 2], 2: [330, 750], 3: [230, 540, 850]}[len(items)]
        for it, x, c in zip(items, xs, (VI, B, LI)):
            L.caja(im, int(x - 145), 235, 290, 110, it["texto"], c, C.ts(t, T.ancla(it["frase"], it.get("palabra")), 0.45))
    elif tipo == "numero":
        t0 = T.ancla(pz["frase"], pz.get("palabra"))
        k = C.ts(t, t0, 0.45)
        L._lit(pz["valor"])
        if k > 0:
            C.pegar(im, C.texto_img(str(pz["valor"]), 96, A, 2), 250, 285, alfa=k, centro=True)
        L.texto(im, pz["texto"], 690, 290, A, alfa=k, ancho=620)
    elif tipo == "cuadricula":
        t0 = T.ancla(pz["frase"], pz.get("palabra"))
        n, de = int(pz["n"]), int(pz.get("de", 10))
        L._lit(str(n))
        paso = min(88, 880 // de)
        x0 = W / 2 - paso * de / 2 + 9
        for i in range(de):
            x = x0 + i * paso
            lit = i < n and t > t0 + i * 0.12
            d.rounded_rectangle((x, 240, x + paso - 18, 240 + paso - 18), 12, fill=VI if lit else C.PANEL,
                                outline=C.mezcla(C.PANEL, B, 0.5), width=3)
        L.texto(im, pz["texto"], W / 2, 360, VI, alfa=C.ts(t, t0 + 0.6, 0.5), ancho=960, tams=(30, 28, 27), lineas_max=1)
    elif tipo == "frase":
        L.texto(im, pz["texto"], W / 2, 290, A, alfa=C.ts(t, T.ancla(pz["frase"], pz.get("palabra")), 0.5), ancho=960,
                tams=(46, 42, 38, 34, 30, 28))


def dibujar_centro(L, im, d, t, pz, T):
    if pz.get("encabezado"):
        L.texto(im, pz["encabezado"].upper() if pz["encabezado"].upper() in ENCABEZADOS_LIBRES else pz["encabezado"],
                80, 450, TN, ancho=900, tams=(30, 28, 27), centro=False, lineas_max=1)
    tipo = pz["tipo"]
    if tipo == "flujo":
        pasos = pz["pasos"][:4]
        alto, paso_y = 130, {2: 260, 3: 220, 4: 185}.get(len(pasos), 185)
        y0 = 510 if len(pasos) > 2 else 600
        for i, p in enumerate(pasos):
            y = y0 + i * paso_y
            t0 = T.ancla(p["frase"], p.get("palabra"))
            c = R if i in (0, len(pasos) - 1) else CI
            ultimo = i == len(pasos) - 1
            brillo = 0.5 + 0.5 * abs(((t - t0) * 1.6) % 2 - 1) if ultimo and t > t0 else 0
            L.caja(im, 190, y, 700, alto, p["texto"], c, C.ts(t, t0, 0.5),
                   relleno=C.mezcla(C.PANEL, (60, 20, 40), brillo) if ultimo else None)
            if i:
                flecha(d, W / 2, y - paso_y + alto + 8, W / 2, y - 8, C.ts(t, t0 - 0.2, 0.45), c)
    elif tipo == "linea_tiempo":
        ev = pz["eventos"][:4]
        y = 840
        xs = [160 + i * (760 / max(1, len(ev) - 1)) for i in range(len(ev))] if len(ev) > 1 else [W / 2]
        t_ini = T.ancla(ev[0]["frase"], ev[0].get("palabra"))
        k = C.ts(t, t_ini - 0.3, 0.8)
        if k > 0:
            d.line((xs[0], y, xs[0] + (xs[-1] - xs[0]) * k, y), fill=TN, width=5)
        for i, (e, x) in enumerate(zip(ev, xs)):
            t0 = T.ancla(e["frase"], e.get("palabra"))
            ke = C.ts(t, t0, 0.45)
            c = CICLO[i % len(CICLO)]
            if ke > 0:
                d.ellipse((x - 18, y - 18, x + 18, y + 18), fill=C.mezcla(C.PANEL, c, ke))
            yy = y - 110 if i % 2 == 0 else y + 110
            L.texto(im, e["texto"], x, yy, c, alfa=ke, ancho=300, tams=(30, 28, 27))
    elif tipo == "lista":
        items = pz["items"][:4]
        for i, it in enumerate(items):
            t0 = T.ancla(it["frase"], it.get("palabra"))
            y = 510 + i * 175
            L.caja(im, 90, y, 900, 130, it["texto"], CICLO[(i + 1) % len(CICLO)], C.ts(t, t0, 0.5),
                   fuente=it.get("fuente"))
    elif tipo == "contraste":
        for lado, x, c in (("izquierda", 70, CI), ("derecha", 560, R)):
            p = pz[lado]
            t0 = T.ancla(p["frase"], p.get("palabra"))
            k = C.ts(t, t0, 0.5)
            kk = 0.25 + 0.75 * k
            d.rounded_rectangle((x, 500, x + 450, 1150), 22, fill=C.PANEL, outline=C.mezcla(TN, c, k), width=4)
            L.texto(im, p["titulo"], x + 225, 550, C.mezcla(TN, c, k), alfa=kk, ancho=400, tams=(36, 32, 30, 28),
                    lineas_max=2)
            L.texto(im, p["texto"], x + 225, 820, C.mezcla(TN, B, k), alfa=kk, ancho=400, tams=(34, 32, 30, 28, 27),
                    lineas_max=7)
            if p.get("fuente"):
                L.texto(im, p["fuente"].upper(), x + 225, 1110, TN, alfa=kk, ancho=420, tams=(27,), lineas_max=1)
        C.pegar(im, C.texto_img("VS", 36, A, 2), W / 2, 820, alfa=1.0, centro=True)
    elif tipo == "documento":
        dibujar_documento(L, im, d, t, pz, T, 500, 1180)
    elif tipo == "barras":
        bs = pz["barras"][:4]
        vmax = max(float(b["valor"]) for b in bs) or 1.0
        for i, b in enumerate(bs):
            t0 = T.ancla(b["frase"], b.get("palabra"))
            k = C.ts(t, t0, 0.7)
            y = 540 + i * 165
            c = CICLO[(i + 2) % len(CICLO)]
            L.texto(im, b["etiqueta"], 90, y, C.mezcla(TN, c, k), alfa=0.3 + 0.7 * k, ancho=880, tams=(30, 28, 27),
                    centro=False, lineas_max=1)
            largo = 760 * float(b["valor"]) / vmax
            d.rounded_rectangle((90, y + 40, 90 + max(8, largo * k), y + 100), 12, fill=C.mezcla(C.PANEL, c, max(0.25, k)))
            L._lit(b["texto"])
            if k > 0:
                C.pegar(im, C.texto_img(b["texto"], 40, c, 1), 90 + largo * k + 20, y + 52, alfa=k)
            if b.get("fuente"):
                L.texto(im, b["fuente"].upper(), 990, y + 120, TN, alfa=k, ancho=600, tams=(27,), lineas_max=1)


def dibujar_documento(L, im, d, t, pz, T, y0, y1):
    """Tarjeta de documento: medio · fecha arriba, y la cita LITERAL de la fuente (verificada) entre comillas."""
    t0 = T.ancla(pz["frase"], pz.get("palabra"))
    k = C.ts(t, t0, 0.5)
    kk = 0.25 + 0.75 * k
    d.rounded_rectangle((90, y0, 990, y1), 18, fill=(22, 30, 46), outline=C.mezcla(TN, B, k), width=3)
    d.rectangle((90, y0, 990, y0 + 70), fill=C.mezcla(C.PANEL, (40, 54, 78), k))
    cab = pz["medio"] + (" · " + pz["fecha"] if pz.get("fecha") else "")
    L._lit(pz["medio"])
    L.texto(im, cab.upper(), 120, y0 + 36, C.mezcla(TN, A, k), alfa=kk, ancho=840, tams=(30, 28, 27), centro=False,
            lineas_max=1, verificar=False)
    L._lit(pz["cita"])
    lineas = max(2, int((y1 - y0 - 110) / 48))
    L.texto(im, "«" + pz["cita"] + "»", W / 2, (y0 + 70 + y1) / 2, C.mezcla(TN, B, k), alfa=kk, ancho=820,
            tams=(34, 32, 30, 28, 27), lineas_max=lineas, verificar=False)


def dibujar_abajo(L, im, d, t, pz, T):
    if pz.get("encabezado"):
        L.texto(im, pz["encabezado"], 80, 1305, A, ancho=900, tams=(34, 32, 30, 28, 27), centro=False, lineas_max=1)
    tipo = pz["tipo"]
    if tipo == "lista":
        for i, it in enumerate(pz["items"][:3]):
            t0 = T.ancla(it["frase"], it.get("palabra"))
            y = 1370 + i * 135
            c = (A, CI, LI)[i % 3]
            L.caja(im, 90, y, 900, 105, it["texto"], c, C.ts(t, t0, 0.5), fantasma=0, fuente=it.get("fuente"))
            if t > t0 + 0.7:
                C.pegar(im, C.texto_img("✓", 50, LI), 950, y + 57, alfa=C.ts(t, t0 + 0.7, 0.4), centro=True)
    elif tipo == "documento":
        dibujar_documento(L, im, d, t, pz, T, 1350, 1770)
    elif tipo == "frase":
        L.texto(im, pz["texto"], W / 2, 1530, A, alfa=C.ts(t, T.ancla(pz["frase"], pz.get("palabra")), 0.5), ancho=940,
                tams=(42, 38, 34, 30, 28), lineas_max=3)
    elif tipo == "numero":
        t0 = T.ancla(pz["frase"], pz.get("palabra"))
        k = C.ts(t, t0, 0.5)
        L._lit(pz["valor"])
        if k > 0:
            C.pegar(im, C.texto_img(str(pz["valor"]), 96, R, 2), W / 2, 1460, alfa=k, centro=True)
        L.texto(im, pz["texto"], W / 2, 1600, R, alfa=k, ancho=940)


def dibujar_escena(L, im, esc, t, T, leyenda):
    d = ImageDraw.Draw(im)
    L.texto(im, esc["titulo"], W / 2, 70, B, ancho=1000, tams=(44, 40, 36, 32), lineas_max=1, esp=2)
    if esc.get("subtitulo"):
        L.texto(im, esc["subtitulo"], W / 2, 128, R, ancho=1000, tams=(30, 28, 27), lineas_max=1, esp=1)
    for p, zona in ((P1, None), (P2, "centro"), (P3, "abajo")):
        if zona is None or esc.get(zona):          # sin pieza en esa zona no se dibuja un panel vacío
            C.panel(d, p, C.BORDE)
    L.texto(im, leyenda, 64, 190, TN, ancho=950, tams=(27,), centro=False, lineas_max=1, verificar=False)
    L.texto(im, "AVIL·ES · @avil.es", W / 2, 1855, TN, ancho=600, tams=(28,), verificar=False)
    for clave, f in (("arriba", dibujar_arriba), ("centro", dibujar_centro), ("abajo", dibujar_abajo)):
        if esc.get(clave):
            f(L, im, d, t, esc[clave], T)


def cortes(plan, T):
    out = []
    for i, esc in enumerate(plan["escenas"]):
        ns = [k + 1 for k, f in enumerate(plan["frases"]) if f["escena"] == i + 1]
        ini = T.frase(ns[0])[0]
        out.append((esc, 0.0 if i == 0 else ini - 0.45, T.frase(ns[-1])[1]))
    return out


def cuadro(L, plan, T, cs, t, leyenda):
    actual = [c for c in cs if c[1] <= t][-1]
    i = cs.index(actual)
    im = L.fondo.copy()
    dibujar_escena(L, im, actual[0], t, T, leyenda)
    if i + 1 < len(cs) and t > cs[i + 1][1] - 0.35:
        sig = L.fondo.copy()
        dibujar_escena(L, sig, cs[i + 1][0], t, T, leyenda)
        im = Image.blend(im, sig, C.ts(t, cs[i + 1][1] - 0.35, 0.35))
    return im.convert("RGB")


def ensayar(plan: dict, narracion: str, leyenda: str, extras: list[str] | None = None):
    """Dibuja el último cuadro de cada escena con tiempos ficticios: detecta textos no literales o que no caben
    ANTES de gastar la voz. Lanza SystemExit con el motivo."""
    n = len(plan["frases"])
    T = Tiempos({"duracion": n * 3.0 + 2, "tramos": [{"ini": k * 3.0, "fin": k * 3.0 + 2.5, "palabras": []}
                                                      for k in range(n)]})
    L = Lienzo(narracion, extras)
    for esc, ini, fin in cortes(plan, T):
        im = L.fondo.copy()
        dibujar_escena(L, im, esc, fin + 5, T, leyenda)


def render(carpeta: Path) -> Path:
    carpeta = Path(carpeta)
    plan = json.loads((carpeta / "plan.json").read_text(encoding="utf-8"))
    T = Tiempos(json.loads((carpeta / "tiempos.json").read_text(encoding="utf-8")))
    narracion = " ".join(f["texto"] for f in plan["frases"])
    leyenda = plan.get("leyenda", "ESQUEMA ILUSTRATIVO")
    L = Lienzo(narracion, plan.get("extras"))
    cs = cortes(plan, T)
    voz = carpeta / "voz_final.wav"
    entrada = ["-i", str(carpeta / "voz.wav")]
    med = subprocess.run(["ffmpeg", "-hide_banner", *entrada, "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
                          "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    m = json.loads(med[med.rindex("{"):med.rindex("}") + 1])
    filtro = ("loudnorm=I=-14:TP=-1.5:LRA=11:linear=true:measured_I={input_i}:measured_TP={input_tp}:"
              "measured_LRA={input_lra}:measured_thresh={input_thresh}:offset={target_offset}").format(**m)
    subprocess.run(["ffmpeg", "-v", "error", "-y", *entrada, "-af", filtro, "-ar", "48000", str(voz)], check=True)
    salida = carpeta / f"{plan.get('id', 'video')}_9x16.mp4"
    n = int(T.duracion * FPS)
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                          "-r", str(FPS), "-i", "-", "-i", str(voz), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                          "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-shortest", str(salida)], stdin=subprocess.PIPE)
    for f in range(n):
        p.stdin.write(cuadro(L, plan, T, cs, f / FPS, leyenda).tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError("ffmpeg no pudo escribir el video")
    return salida


if __name__ == "__main__":
    print(render(Path(sys.argv[1])))
