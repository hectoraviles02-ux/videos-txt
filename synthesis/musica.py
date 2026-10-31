# -*- coding: utf-8 -*-
"""Musica de fondo sintetizada aqui mismo. Sin licencias, sin reclamos.

**Por que sintetizarla y no bajarla.** La pregunta era si el video es obra
propia. Con musica de banco la respuesta es "usable, pero no tuya": la
licencia te deja usarla, no te hace dueno, y Content ID reclama pistas con
licencia libre cuando un tercero las registro — los falsos positivos son
comunes con musica de produccion, donde los mismos loops se reutilizan en
muchos catalogos. Generada por este codigo, la respuesta es "tuya", sin
asterisco.

**Por que no un modelo generativo.** Se intento con ACE-Step 1.5, que tiene
licencia MIT y declara salida comercial. MEDIDO el 2026-08-22: 10.09 GB
descargados, y su codigo remoto apunta a una version de `transformers` que
no es ni la instalada (4.51.3) ni la ultima (5.15.1). Cuatro intentos de
carga, cuatro fallos, el ultimo con tensores en meta. Queda anotado por si
el repositorio se actualiza.

**Que produce esto.** No canciones: un lecho armonico. Para un documental lo
correcto es una base que sostenga la voz sin disputarle el primer plano, y
eso son acordes largos filtrados, sin melodia ni percusion. Una melodia
debajo de una narracion compite con ella; un acorde no.
"""
from __future__ import annotations

import logging
import math
import os
import subprocess
from dataclasses import dataclass

import numpy as np

from adapters.ffmpeg_tools import resolve_ffmpeg

logger = logging.getLogger(__name__)

SR = 44100

# Grados de la escala menor natural, en semitonos desde la tonica.
_MENOR = (0, 2, 3, 5, 7, 8, 10)

# La tonica: 55 Hz es un La grave. Toda la base se apoya ahi, bien debajo de
# la voz humana, que vive entre 85 y 255 Hz.
TONICA_HZ = 55.0

# Corte del filtro. La voz masculina tiene su fundamental cerca de 115 Hz y
# sus formantes hasta unos 3 kHz; la base se recorta en 800 Hz para no
# disputarle la inteligibilidad.
CORTE_HZ = 800.0

# Nivel de la base respecto a la narracion. El mezclador del pipeline usa
# amix con normalize=0, asi que este valor es el que manda.
NIVEL = 0.14


@dataclass
class Ambiente:
    """Un caracter de musica: que grados usa y a que ritmo respira."""

    nombre: str
    grados: tuple[int, ...]
    compas_seg: float
    brillo: float          # 0 = muy oscuro, 1 = mas abierto
    descripcion: str


AMBIENTES = {
    # Para temas de ciencia y descubrimiento: abierto, sin tension.
    "asombro": Ambiente("asombro", (0, 4, 5, 3), 8.0, 0.75,
                        "abierto, contemplativo"),
    # Para investigacion, fallas, tension procedimental.
    "tension": Ambiente("tension", (0, 5, 1, 6), 6.0, 0.35,
                        "grave, inquieto"),
    # Neutro, para explicativo y corporativo.
    "sobrio": Ambiente("sobrio", (0, 3, 4, 0), 10.0, 0.55,
                       "estable, discreto"),
}
AMBIENTE_POR_DEFECTO = "sobrio"


def _hz(grado: int, octava: int = 0) -> float:
    """Frecuencia de un grado de la escala menor sobre la tonica."""
    semitonos = _MENOR[grado % len(_MENOR)] + 12 * (octava + grado // len(_MENOR))
    return TONICA_HZ * (2.0 ** (semitonos / 12.0))


def _pasa_bajos(x: np.ndarray, corte: float, sr: int = SR) -> np.ndarray:
    """Filtro de un polo. Basta: aqui no hace falta resonancia, hace falta
    que la base no invada la banda donde se entiende la voz."""
    a = math.exp(-2.0 * math.pi * corte / sr)
    y = np.empty_like(x)
    prev = 0.0
    for i, v in enumerate(x):
        prev = (1 - a) * v + a * prev
        y[i] = prev
    return y


def _voz_de_acorde(freqs: list[float], n: int, brillo: float,
                   rng: np.random.Generator) -> np.ndarray:
    """Un acorde sostenido, con las cuerdas ligeramente desafinadas entre si.

    El desafine minimo -unas centesimas- es lo que separa un acorde de un
    tono de prueba: sin el suena a sintetizador barato, con el respira.
    """
    t = np.arange(n, dtype=np.float64) / SR
    salida = np.zeros(n, dtype=np.float64)
    for f in freqs:
        for k, peso in ((1, 1.0), (2, 0.28 * brillo), (3, 0.12 * brillo)):
            detune = 1.0 + rng.uniform(-0.0018, 0.0018)
            fase = rng.uniform(0, 2 * math.pi)
            salida += peso * np.sin(2 * math.pi * f * k * detune * t + fase)
    return salida / max(1.0, len(freqs) * 1.6)


def generar(duracion_seg: float, destino: str,
            ambiente: str = AMBIENTE_POR_DEFECTO,
            semilla: int = 7, ffmpeg_path: str | None = None) -> str:
    """Escribe un lecho musical de `duracion_seg` en `destino`.

    Determinista: la misma semilla y el mismo ambiente dan el mismo archivo.
    Eso permite rehacer un video identico, que es la regla del proyecto.
    """
    if duracion_seg <= 0:
        raise ValueError("la duracion tiene que ser positiva")
    amb = AMBIENTES.get(ambiente)
    if not amb:
        raise ValueError("ambiente desconocido: %r. Hay: %s"
                         % (ambiente, ", ".join(sorted(AMBIENTES))))

    rng = np.random.default_rng(semilla)
    n_total = int(duracion_seg * SR)
    mezcla = np.zeros(n_total, dtype=np.float64)

    n_compas = int(amb.compas_seg * SR)
    # Los acordes se solapan medio compas: sin cruce, cada cambio se oye como
    # un corte y delata que la musica es sintetica.
    solape = n_compas // 2
    paso = n_compas - solape

    i = 0
    idx = 0
    while i < n_total:
        grado = amb.grados[idx % len(amb.grados)]
        # Triada: fundamental, tercera y quinta del grado, mas la tonica
        # grave como ancla.
        freqs = [_hz(grado), _hz(grado + 2), _hz(grado + 4), _hz(0, -1)]
        largo = min(n_compas, n_total - i)
        voz = _voz_de_acorde(freqs, largo, amb.brillo, rng)
        # Envolvente suave en los dos extremos para que el cruce no chasquee.
        env = np.ones(largo)
        rampa = min(solape, largo // 2)
        if rampa > 0:
            subida = 0.5 * (1 - np.cos(np.linspace(0, math.pi, rampa)))
            env[:rampa] = subida
            env[-rampa:] = subida[::-1]
        mezcla[i:i + largo] += voz * env
        i += paso
        idx += 1

    # Un soplo de ruido muy filtrado: da textura y tapa la sensacion de bucle.
    aire = _pasa_bajos(rng.normal(0, 1, n_total), 220.0) * 0.05 * amb.brillo
    mezcla += aire

    mezcla = _pasa_bajos(mezcla, CORTE_HZ)

    # Entrada y salida largas. Un lecho que arranca de golpe se nota.
    fundido = min(int(3.0 * SR), n_total // 4)
    if fundido > 0:
        rampa = 0.5 * (1 - np.cos(np.linspace(0, math.pi, fundido)))
        mezcla[:fundido] *= rampa
        mezcla[-fundido:] *= rampa[::-1]

    pico = float(np.max(np.abs(mezcla))) or 1.0
    mezcla = (mezcla / pico) * NIVEL

    crudo = destino + ".raw.wav"
    _escribir_wav(crudo, mezcla)
    ffmpeg = resolve_ffmpeg(ffmpeg_path)
    r = subprocess.run(
        [ffmpeg, "-y", "-i", crudo, "-c:a", "libmp3lame", "-q:a", "4", destino],
        capture_output=True, text=True, timeout=600,
    )
    if os.path.exists(crudo):
        os.remove(crudo)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg no pudo escribir la musica: %s"
                           % (r.stderr or "")[-300:])
    logger.info("musica: %s | %s | %.1fs | %s",
                os.path.basename(destino), amb.nombre, duracion_seg,
                amb.descripcion)
    return destino


def _escribir_wav(ruta: str, x: np.ndarray) -> None:
    import wave
    datos = np.clip(x, -1.0, 1.0)
    pcm = (datos * 32767.0).astype("<i2")
    with wave.open(ruta, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def ambiente_para(tema: str) -> str:
    """Elige el caracter segun de que trata el video.

    Es una heuristica declarada, no una medicion: se puede pasar el ambiente
    a mano cuando no acierte.
    """
    t = (tema or "").lower()
    if any(k in t for k in ("falla", "error", "fallo", "caida", "crisis",
                            "riesgo", "fraude", "incidente", "ataque")):
        return "tension"
    if any(k in t for k in ("universo", "espacio", "agujero", "galaxia",
                            "cosmos", "descubrimiento", "ciencia", "futuro",
                            "estrella", "planeta")):
        return "asombro"
    return AMBIENTE_POR_DEFECTO
