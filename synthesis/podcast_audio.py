# -*- coding: utf-8 -*-
"""Voces y ensamblado de un episodio de podcast.

Aqui vive solo la parte de audio: quien suena como, las pausas y el montaje
final. El guion -de donde salen los turnos- es otra responsabilidad y vive
en `synthesis/podcast.py`.

MEDIDO el 2026-08-21 armando el primer episodio (3:29): una pausa constante
entre turnos suena a lista leida, por eso hay dos largos; y
`loudnorm=I=-16` deja nivel de podcast, verificado en el MP3 resultante
(-16.9 dB medio, -1.3 dB de pico).
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field

from adapters.ffmpeg_tools import resolve_ffmpeg, resolve_ffprobe

logger = logging.getLogger(__name__)

# Dos largos de silencio. Una pausa unica suena a lista.
PAUSA_TURNO = 0.32
PAUSA_BLOQUE = 0.75

# Nivel de podcast. -16 LUFS es el estandar para escucha en telefono.
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"


@dataclass
class Voz:
    """Como suena un anfitrion."""

    nombre: str
    voice: str
    rate: str = "+0%"
    pitch: str = "+0Hz"
    papel: str = ""


# Las dos unicas voces de es-MX que ofrece edge-tts. Jorge va un poco mas
# lento y grave -asi suena quien pregunta- y Dalia un poco mas rapida.
JORGE = Voz("JORGE", "es-MX-JorgeNeural", "-4%", "-2Hz",
            "conduce: pregunta, ordena el tema y cierra ideas")
DALIA = Voz("DALIA", "es-MX-DaliaNeural", "+4%", "+0Hz",
            "explica: sabe el detalle tecnico y da los ejemplos")


@dataclass
class Episodio:
    titulo: str
    turnos: list[dict[str, str]] = field(default_factory=list)
    audio_path: str | None = None
    duracion_seg: float = 0.0
    avisos: list[str] = field(default_factory=list)


def _silencio(destino: str, duracion: float, ffmpeg: str) -> None:
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
         "-t", "%.3f" % duracion, "-c:a", "libmp3lame", "-q:a", "4", destino],
        capture_output=True, timeout=120,
    )


def duracion_de(media: str, ffprobe_path: str | None = None) -> float:
    try:
        r = subprocess.run(
            [resolve_ffprobe(ffprobe_path), "-v", "error", "-show_entries",
             "format=duration", "-of", "default=nw=1:nk=1", media],
            capture_output=True, text=True, timeout=120,
        )
        return float((r.stdout or "0").strip() or 0)
    except (subprocess.SubprocessError, ValueError, OSError):
        return 0.0


def montar_episodio(turnos: list[dict[str, str]], directorio: str,
                    titulo: str = "Episodio",
                    anfitriones: tuple[Voz, Voz] = (JORGE, DALIA),
                    turnos_por_bloque: int = 9,
                    ffmpeg_path: str | None = None) -> Episodio:
    """Sintetiza cada turno con su voz y ensambla el episodio.

    `turnos_por_bloque` solo decide donde cae la pausa larga: es el respiro
    entre secciones, no una division del contenido.
    """
    import asyncio

    import edge_tts

    if not turnos:
        raise ValueError("no hay turnos que montar")

    os.makedirs(directorio, exist_ok=True)
    ffmpeg = resolve_ffmpeg(ffmpeg_path)
    por_nombre = {v.nombre: v for v in anfitriones}
    ep = Episodio(titulo=titulo, turnos=turnos)

    async def _sintetizar() -> None:
        for i, t in enumerate(turnos):
            v = por_nombre.get(t["quien"], anfitriones[0])
            c = edge_tts.Communicate(t["texto"], v.voice, rate=v.rate,
                                     pitch=v.pitch)
            await c.save(os.path.join(directorio, "t%03d.mp3" % i))

    asyncio.run(_sintetizar())

    _silencio(os.path.join(directorio, "p_turno.mp3"), PAUSA_TURNO, ffmpeg)
    _silencio(os.path.join(directorio, "p_bloque.mp3"), PAUSA_BLOQUE, ffmpeg)

    lista = os.path.join(directorio, "lista.txt")
    with open(lista, "w", encoding="utf-8") as f:
        for i in range(len(turnos)):
            f.write("file '%s'\n" % os.path.join(
                directorio, "t%03d.mp3" % i).replace("\\", "/"))
            if i == len(turnos) - 1:
                break
            pausa = ("p_bloque" if (i + 1) % turnos_por_bloque == 0
                     else "p_turno")
            f.write("file '%s'\n" % os.path.join(
                directorio, pausa + ".mp3").replace("\\", "/"))

    salida = os.path.join(directorio, "episodio.mp3")
    r = _concatenar(ffmpeg, lista, salida, con_loudnorm=True)
    if r.returncode != 0:
        # Sin normalizar antes que sin episodio.
        ep.avisos.append("loudnorm fallo; el episodio va sin normalizar")
        r = _concatenar(ffmpeg, lista, salida, con_loudnorm=False)
        if r.returncode != 0:
            raise RuntimeError("ffmpeg no pudo ensamblar: %s"
                               % (r.stderr or "")[-300:])

    ep.audio_path = salida
    ep.duracion_seg = duracion_de(salida)
    logger.info("podcast: %s | %d turnos | %d:%02d", salida, len(turnos),
                int(ep.duracion_seg) // 60, int(ep.duracion_seg) % 60)
    return ep


def _concatenar(ffmpeg: str, lista: str, salida: str,
                con_loudnorm: bool) -> subprocess.CompletedProcess:
    cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", lista]
    if con_loudnorm:
        cmd += ["-af", LOUDNORM]
    cmd += ["-c:a", "libmp3lame", "-q:a", "2", salida]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
