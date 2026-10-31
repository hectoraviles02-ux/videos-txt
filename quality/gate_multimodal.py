"""Quality Gate multimodal — decide si un video se ENTREGA o se RECHAZA.

Motivo de existir: el gate anterior daba `all_pass = true` y
`solid_frame_ratio = 0.0` sobre un video que tenia el 70% de la pantalla en
negro y fotos que no correspondian a la narracion. Las metricas pasaban; el
video era inservible. Un video no se entrega porque termino de renderizar: se
entrega porque paso calidad.

Dos capas:

  OBJETIVA   — OpenCV + ffprobe. Rapida, determinista, sin modelo. Detecta
               los defectos que se pueden medir: cobertura, movimiento,
               nitidez, variedad, audio, formato.

  SEMANTICA  — opcional, via CLI de claude leyendo frames. Evalua lo que
               ningun numero capta: si la imagen corresponde a lo que se
               narra y si la secuencia cuenta algo coherente.

UMBRALES: salen de mediciones reales del 2026-08-18, no de intuicion.
Ver DIMENSIONES para el origen de cada uno.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path

import cv2
import numpy as np

# ── Umbrales medidos empiricamente ───────────────────────────────────────
#
# | Video                        | movimiento | cobertura |
# |------------------------------|-----------|-----------|
# | metraje real (aceptable)     |   14.37   |   ~100%   |
# | Ken Burns + xfade (rechazado)|    1.99   |    30%    |
# | Ken Burns solo   (rechazado) |    1.72   |    30%    |
#
# Los umbrales se ubican entre ambos grupos, mas cerca del rechazado para no
# ser condescendiente.

MOV_MINIMO = 5.0        # debajo de esto es una presentacion de diapositivas
MOV_BUENO = 12.0        # a partir de aca hay movimiento comparable a metraje real
COBERTURA_MINIMA = 0.92  # el contenido debe ocupar >=92% del cuadro
NITIDEZ_MINIMA = 60.0   # varianza del laplaciano; debajo, imagen borrosa
VARIEDAD_MINIMA = 12.0  # diferencia media entre escenas distintas
MUESTREO = 5            # se analiza 1 de cada N frames

UMBRAL_ENTREGA = 70     # puntaje global necesario para entregar


@dataclass
class Dimension:
    nombre: str
    puntaje: float          # 0-100
    medida: float
    umbral: float
    aprueba: bool
    detalle: str
    bloqueante: bool = True  # si falla y es bloqueante, el video se rechaza


@dataclass
class Resultado:
    video: str
    puntaje_global: float = 0.0
    entrega: bool = False
    dimensiones: list[Dimension] = field(default_factory=list)
    defectos: list[str] = field(default_factory=list)
    sugerencias: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["dimensiones"] = [asdict(x) if not isinstance(x, dict) else x
                            for x in self.dimensiones]
        return d


def _escala(valor: float, piso: float, techo: float) -> float:
    """Mapea un valor a 0-100 entre piso y techo."""
    if techo <= piso:
        return 100.0 if valor >= techo else 0.0
    return float(np.clip((valor - piso) / (techo - piso) * 100, 0, 100))


def _probe(video: str) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", video],
        capture_output=True, text=True, timeout=120,
    )
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {}


# ── Mediciones ───────────────────────────────────────────────────────────

def _muestrear(video: str, cada: int = MUESTREO):
    """Devuelve (frames_gris_reducidos, total_frames, fps)."""
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    grises, color, n = [], [], 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        n += 1
        if n % cada:
            continue
        chico = cv2.resize(f, (320, 180))
        grises.append(cv2.cvtColor(chico, cv2.COLOR_BGR2GRAY))
        color.append(chico)
    cap.release()
    return grises, color, n, fps


def _dim_cobertura(grises: list) -> Dimension:
    """Que porcentaje del cuadro tiene contenido (no es borde negro).

    ESTA es la dimension que faltaba: el gate anterior no la tenia y por eso
    aprobo un video con 70% de pantalla negra. Se mide el area del rectangulo
    que encierra todo lo que no es negro.
    """
    cobs = []
    for g in grises:
        ys, xs = np.where(g >= 12)
        if len(xs) < 10:
            cobs.append(0.0)
            continue
        w = xs.max() - xs.min() + 1
        h = ys.max() - ys.min() + 1
        cobs.append((w * h) / (g.shape[1] * g.shape[0]))
    med = float(np.median(cobs)) if cobs else 0.0
    ok = med >= COBERTURA_MINIMA
    return Dimension(
        "cobertura_pantalla", _escala(med, 0.30, 1.0), round(med * 100, 1),
        COBERTURA_MINIMA * 100, ok,
        "El contenido ocupa %.0f%% del cuadro (minimo %.0f%%)"
        % (med * 100, COBERTURA_MINIMA * 100),
    )


def _dim_movimiento(grises: list) -> Dimension:
    difs = [float(np.abs(grises[i].astype(int) - grises[i - 1].astype(int)).mean())
            for i in range(1, len(grises))]
    if not difs:
        return Dimension("movimiento", 0, 0, MOV_MINIMO, False,
                         "No se pudo medir", True)
    med = float(np.mean(difs))
    quietos = float(np.mean(np.array(difs) < 2.0))
    ok = med >= MOV_MINIMO
    return Dimension(
        "movimiento", _escala(med, 1.0, MOV_BUENO), round(med, 2),
        MOV_MINIMO, ok,
        "Movimiento medio %.2f; %.0f%% de los frames casi identicos al anterior"
        % (med, quietos * 100),
    )


def _dim_nitidez(color: list) -> Dimension:
    vals = [cv2.Laplacian(cv2.cvtColor(c, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
            for c in color]
    med = float(np.median(vals)) if vals else 0.0
    ok = med >= NITIDEZ_MINIMA
    return Dimension(
        "nitidez", _escala(med, 10, 300), round(med, 1), NITIDEZ_MINIMA, ok,
        "Varianza del laplaciano %.1f (debajo de %.0f la imagen se ve borrosa)"
        % (med, NITIDEZ_MINIMA),
        bloqueante=False,
    )


def _dim_variedad(grises: list) -> Dimension:
    """Cuanto cambia el video a lo largo del tiempo (no entre frames vecinos).

    Detecta el caso de un video que repite el mismo plano toda su duracion.
    """
    if len(grises) < 4:
        return Dimension("variedad_visual", 0, 0, VARIEDAD_MINIMA, False,
                         "Muy pocos frames", False)
    idx = np.linspace(0, len(grises) - 1, min(9, len(grises))).astype(int)
    muestras = [grises[i] for i in idx]
    difs = [float(np.abs(muestras[i].astype(int) - muestras[j].astype(int)).mean())
            for i in range(len(muestras)) for j in range(i + 1, len(muestras))]
    med = float(np.mean(difs)) if difs else 0.0
    ok = med >= VARIEDAD_MINIMA
    return Dimension(
        "variedad_visual", _escala(med, 2, 40), round(med, 2),
        VARIEDAD_MINIMA, ok,
        "Diferencia media entre momentos distintos: %.2f" % med,
        bloqueante=False,
    )


def _dim_audio(info: dict) -> Dimension:
    streams = info.get("streams", [])
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio:
        return Dimension("audio", 0, 0, 1, False,
                         "El video NO tiene pista de audio")
    br = int(audio[0].get("bit_rate") or 0)
    return Dimension("audio", 100.0, br, 1, True,
                     "Pista de audio %s a %d bps" % (audio[0].get("codec_name"), br))


def _dim_formato(info: dict, vertical: bool) -> Dimension:
    streams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if not streams:
        return Dimension("formato", 0, 0, 0, False, "Sin pista de video")
    w = int(streams[0].get("width") or 0)
    h = int(streams[0].get("height") or 0)
    esperado = "vertical 9:16" if vertical else "horizontal 16:9"
    ok = (h > w) if vertical else (w >= h)
    return Dimension(
        "formato", 100.0 if ok else 0.0, w / h if h else 0, 0, ok,
        "%dx%d — se esperaba %s" % (w, h, esperado),
    )


# ── Capa semantica (opcional) ────────────────────────────────────────────

def evaluar_semantica(video: str, narracion: str, salida_frames: Path,
                      timeout: int = 240) -> dict | None:
    """Extrae frames y le pide al CLI de claude que juzgue correspondencia.

    Devuelve None si el CLI no esta disponible o falla: la capa objetiva
    sigue valiendo por si sola.
    """
    salida_frames.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(video)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    rutas = []
    for i in range(6):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i * (total - 1) / 5))
        ok, f = cap.read()
        if ok:
            p = salida_frames / ("frame_%d.png" % i)
            cv2.imwrite(str(p), cv2.resize(f, (960, 540)))
            rutas.append(str(p))
    cap.release()
    if not rutas:
        return None

    prompt = (
        "Evalua un video generado automaticamente. Lee estas imagenes:\n"
        + "\n".join(rutas)
        + "\n\nEsto es lo que dice la narracion:\n" + narracion[:1500]
        + "\n\nResponde SOLO un JSON con esta forma exacta:\n"
        '{"correspondencia": <0-100>, "coherencia": <0-100>, '
        '"defectos": ["..."], "veredicto": "<una linea>"}\n\n'
        "correspondencia = cuanto tienen que ver las imagenes con lo que se narra.\n"
        "coherencia = si la secuencia cuenta algo con sentido.\n"
        "Se exigente: un video con fotos genericas que no ilustran el tema "
        "merece menos de 40."
    )
    try:
        r = subprocess.run(
            ["claude", "--print", "--allowedTools", "Read",
             "--disallowedTools", "Bash", "Edit", "Write", "-p", prompt],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if r.returncode != 0:
        return None
    txt = (r.stdout or "").strip()
    i, j = txt.find("{"), txt.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(txt[i:j + 1])
    except json.JSONDecodeError:
        return None


# ── Entrada principal ────────────────────────────────────────────────────

def evaluar(video: str, narracion: str = "", vertical: bool = False,
            semantica: bool = False, dir_frames: str | None = None) -> Resultado:
    res = Resultado(video=video)
    if not Path(video).is_file():
        res.defectos.append("El archivo no existe")
        return res

    info = _probe(video)
    grises, color, _, _ = _muestrear(video)
    if not grises:
        res.defectos.append("No se pudieron leer frames")
        return res

    res.dimensiones = [
        _dim_cobertura(grises),
        _dim_movimiento(grises),
        _dim_nitidez(color),
        _dim_variedad(grises),
        _dim_audio(info),
        _dim_formato(info, vertical),
    ]

    if semantica and narracion:
        sem = evaluar_semantica(
            video, narracion,
            Path(dir_frames or Path(video).with_suffix("")) / "_gate_frames")
        if sem:
            for clave, nombre in (("correspondencia", "correspondencia_imagen_narracion"),
                                  ("coherencia", "coherencia_narrativa")):
                val = float(sem.get(clave, 0))
                res.dimensiones.append(Dimension(
                    nombre, val, val, 60, val >= 60,
                    sem.get("veredicto", "")[:160]))
            res.defectos.extend(sem.get("defectos", []))

    res.puntaje_global = round(
        float(np.mean([d.puntaje for d in res.dimensiones])), 1)

    fallan_bloqueantes = [d for d in res.dimensiones if not d.aprueba and d.bloqueante]
    res.entrega = res.puntaje_global >= UMBRAL_ENTREGA and not fallan_bloqueantes

    for d in res.dimensiones:
        if not d.aprueba:
            res.defectos.append("%s: %s" % (d.nombre, d.detalle))

    _sugerir(res)
    return res


def _sugerir(res: Resultado) -> None:
    por_nombre = {d.nombre: d for d in res.dimensiones}
    if not por_nombre.get("cobertura_pantalla", Dimension("", 100, 0, 0, True, "")).aprueba:
        res.sugerencias.append(
            "Escalar la imagen para LLENAR el cuadro y recortar el sobrante "
            "(cover, no contain). PIL.thumbnail() solo achica: nunca agranda.")
    if not por_nombre.get("movimiento", Dimension("", 100, 0, 0, True, "")).aprueba:
        res.sugerencias.append(
            "Usar clips de video real en vez de fotos con zoom. Las transiciones "
            "no crean movimiento: solo tapan el corte.")
    if not por_nombre.get("nitidez", Dimension("", 100, 0, 0, True, "")).aprueba:
        res.sugerencias.append("Descartar el clip borroso y pedir otro a la fuente.")
    if not por_nombre.get("audio", Dimension("", 100, 0, 0, True, "")).aprueba:
        res.sugerencias.append("El video salio sin narracion: revisar el TTS.")
    if not por_nombre.get("formato", Dimension("", 100, 0, 0, True, "")).aprueba:
        res.sugerencias.append(
            "Formato equivocado para el destino. TikTok/Reels/Shorts piden 9:16.")


def informe(res: Resultado) -> str:
    lineas = ["QUALITY GATE — %s" % Path(res.video).name,
              "=" * 62,
              "PUNTAJE %.1f/100   ->   %s"
              % (res.puntaje_global, "ENTREGAR" if res.entrega else "RECHAZAR"),
              ""]
    for d in res.dimensiones:
        lineas.append("  %-34s %5.1f  %s  %s"
                      % (d.nombre, d.puntaje, "OK " if d.aprueba else "FALLA",
                         "" if d.bloqueante else "(no bloquea)"))
        lineas.append("      %s" % d.detalle)
    if res.sugerencias:
        lineas += ["", "QUE HACER:"]
        lineas += ["  - %s" % s for s in res.sugerencias]
    return "\n".join(lineas)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Quality Gate multimodal de video")
    ap.add_argument("video")
    ap.add_argument("--vertical", action="store_true", help="esperar 9:16")
    ap.add_argument("--semantica", action="store_true",
                    help="evaluar correspondencia imagen/narracion via CLI de claude")
    ap.add_argument("--narracion", default="", help="texto narrado, para la capa semantica")
    ap.add_argument("--json", help="guardar el resultado en este archivo")
    a = ap.parse_args()

    r = evaluar(a.video, a.narracion, a.vertical, a.semantica)
    print(informe(r))
    if a.json:
        Path(a.json).write_text(
            json.dumps(r.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    raise SystemExit(0 if r.entrega else 1)
