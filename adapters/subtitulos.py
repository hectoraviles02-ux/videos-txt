"""Subtitulos quemados a partir de la narracion del storyboard.

El texto de los subtitulos es EL MISMO que habla el TTS: se toma
`scene.narration`, no la transcripcion de la fuente. Asi coinciden
palabra por palabra sin pasar el audio generado por un ASR, que
reintroduciria error de transcripcion en un texto que ya se tiene exacto.

Sobre la sincronia: el proveedor de TTS entrega UN solo archivo de audio
para todo el video, no un archivo por escena, asi que no hay marcas de
tiempo reales dentro de cada escena. Lo que si es exacto son los limites
de escena, que vienen del storyboard y son los que el video respeta. Por
eso el reparto dentro de una escena es PROPORCIONAL a la longitud del
texto: es una aproximacion declarada, no una alineacion medida. Si algun
dia el TTS entrega audio por escena, esa aproximacion se reemplaza por
duraciones reales sin tocar el resto.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

# Un renglon mas largo que esto obliga al ojo a barrer la pantalla y se
# vuelve ilegible en vertical. Dos renglones es el maximo antes de tapar
# la imagen.
MAX_CARACTERES_LINEA = 42
MAX_LINEAS = 2
# Un subtitulo que aparece y desaparece en menos de esto no se alcanza a
# leer; se le roba tiempo al siguiente.
DURACION_MINIMA = 0.8


@dataclass
class Cue:
    inicio: float
    fin: float
    texto: str  # puede traer un salto de linea

    @property
    def duracion(self) -> float:
        return max(0.0, self.fin - self.inicio)


def _partir_en_bloques(texto: str) -> list[str]:
    """Corta el texto en bloques de a lo sumo dos renglones legibles.

    Se corta primero por puntuacion -donde el hablante hace pausa- y solo
    dentro de una frase demasiado larga se corta por palabras.
    """
    texto = " ".join(str(texto or "").split())
    if not texto:
        return []

    frases = [f.strip() for f in re.split(r"(?<=[.!?…:;])\s+", texto) if f.strip()]
    tope = MAX_CARACTERES_LINEA * MAX_LINEAS

    bloques: list[str] = []
    for frase in frases:
        if len(frase) <= tope:
            bloques.append(frase)
            continue
        # Frase larga: se reparte por palabras sin pasar el tope.
        actual = ""
        for palabra in frase.split():
            if actual and len(actual) + 1 + len(palabra) > tope:
                bloques.append(actual)
                actual = palabra
            else:
                actual = f"{actual} {palabra}".strip()
        if actual:
            bloques.append(actual)
    return bloques


def _envolver(bloque: str) -> str:
    """Reparte el bloque en hasta dos renglones equilibrados."""
    if len(bloque) <= MAX_CARACTERES_LINEA:
        return bloque

    palabras = bloque.split()
    lineas: list[str] = []
    actual = ""
    for palabra in palabras:
        if actual and len(actual) + 1 + len(palabra) > MAX_CARACTERES_LINEA:
            lineas.append(actual)
            actual = palabra
        else:
            actual = f"{actual} {palabra}".strip()
    if actual:
        lineas.append(actual)
    # NO se recorta a MAX_LINEAS: cortar aqui perderia texto en silencio, que
    # es peor que un subtitulo de tres renglones. El limite se respeta al
    # partir en bloques; si aun asi sobra un renglon, se muestra completo.
    return "\n".join(lineas)


def construir_cues(storyboard: Any) -> list[Cue]:
    """Un grupo de cues por escena, dentro de los limites de esa escena."""
    cues: list[Cue] = []

    for scene in getattr(storyboard, "scenes", []):
        narracion = getattr(scene, "narration", "") or ""
        bloques = _partir_en_bloques(narracion)
        if not bloques:
            continue

        inicio = float(scene.start_seconds)
        fin = float(scene.end_seconds)
        disponible = max(0.0, fin - inicio)
        if disponible <= 0:
            continue

        total_chars = sum(len(b) for b in bloques) or 1
        t = inicio
        for i, bloque in enumerate(bloques):
            if i == len(bloques) - 1:
                # El ultimo cierra exactamente donde termina la escena para
                # que el redondeo no deje un hueco ni se pase.
                t_fin = fin
            else:
                t_fin = t + disponible * (len(bloque) / total_chars)
            if t_fin - t < DURACION_MINIMA:
                t_fin = min(fin, t + DURACION_MINIMA)
            if t_fin <= t:
                continue
            cues.append(Cue(inicio=t, fin=t_fin, texto=_envolver(bloque)))
            t = t_fin
            if t >= fin:
                break

    return cues


def _marca(segundos: float) -> str:
    """Formato SRT: HH:MM:SS,mmm"""
    if segundos < 0:
        segundos = 0.0
    total_ms = int(round(segundos * 1000))
    h, resto = divmod(total_ms, 3_600_000)
    m, resto = divmod(resto, 60_000)
    s, ms = divmod(resto, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def escribir_srt(cues: list[Cue], ruta: str) -> str:
    """Escribe el SRT en UTF-8. Devuelve la ruta."""
    os.makedirs(os.path.dirname(os.path.abspath(ruta)) or ".", exist_ok=True)
    partes = []
    for i, cue in enumerate(cues, start=1):
        partes.append(
            f"{i}\n{_marca(cue.inicio)} --> {_marca(cue.fin)}\n{cue.texto}\n"
        )
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("\n".join(partes))
    return ruta


def generar_srt(storyboard: Any, ruta: str) -> str | None:
    """Del storyboard al archivo .srt. None si no hay narracion que mostrar."""
    cues = construir_cues(storyboard)
    if not cues:
        return None
    return escribir_srt(cues, ruta)


def ruta_para_filtro(ruta: str) -> str:
    """Deja la ruta como la espera el filtro `subtitles` de ffmpeg.

    En Windows hay que escapar DOS veces: una para el parser de filtros de
    ffmpeg y otra para el de opciones. Sin esto, `C:\\ruta\\a.srt` se
    interpreta como si `C` fuera una opcion y el filtro falla con
    "Unable to parse option value".
    """
    p = os.path.abspath(ruta).replace("\\", "/")
    return p.replace(":", "\\:").replace("'", "\\'")


# Estilo legible sobre cualquier fondo: texto blanco con contorno negro
# grueso y sombra. Sin contorno, un subtitulo blanco desaparece sobre un
# cielo o una pared clara del metraje de stock.
ESTILO_POR_DEFECTO = (
    "FontName=Arial,FontSize=22,PrimaryColour=&H00FFFFFF,"
    "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,"
    "Alignment=2,MarginV=40,Bold=1"
)
