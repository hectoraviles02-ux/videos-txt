# -*- coding: utf-8 -*-
"""Narracion con Kokoro, escena por escena.

POR QUE REEMPLAZA A LA VOZ CLONADA

MEDIDO el 2026-08-25 con las mismas seis frases, contra `voz_clonada.py`
(VoxCPM2):

    ritmo          Kokoro 120-125 ppm     VoxCPM2 105 ppm   (referente: 127)
    fidelidad      exacta al texto        ALTERA el texto con CFG=4.5
    generacion     1.6 s                  160 s

Los tres renglones apuntan al mismo lado, pero el que decide es el segundo.
Un TTS que cambia las palabras rompe la regla del proyecto: el subtitulo se
saca transcribiendo el audio, asi que si la voz dice otra cosa, el subtitulo
la repite y no queda rastro de que el guion decia otra cosa.

POR QUE ESCENA POR ESCENA Y NO DE CORRIDO

`NarrationInput.duraciones_por_escena` existe para esto. Sintetizando el
texto entero no hay forma de saber cuanto duro cada frase, y los cortes de
imagen se reparten a partes iguales: la imagen cambia a mitad de una oracion.
Yendo frase por frase, cada corte cae donde termina la suya.

EL ACENTO IMPORTA, Y NO SE VE

El fonetizador del espanol decide la silaba tonica por la tilde escrita. Sin
ella, "dejo" se pronuncia DE-jo -presente- en vez de de-JO -pasado-: otra
palabra. Como los subtitulos salen de transcribir el AUDIO, el error no
aparece en pantalla y solo se oye. Por eso este proveedor pasa el texto por
`synthesis.validar_acentos` antes de sintetizar y AVISA -no corrige, no
bloquea-, siguiendo el criterio de la compuerta de hechos del proyecto.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from adapters.base import NarrationInput

logger = logging.getLogger(__name__)

# La voz que eligio el operador el 2026-08-25 comparando contra el referente.
VOZ = "em_alex"

# Kokoro entrega a 24000 Hz. No es un valor a gusto: escribir el WAV con otra
# frecuencia lo vuelve gutural. Ya paso con VoxCPM2, que genera a 48000 y se
# escribio a 16000: el audio salio 3x mas lento y 3x mas grave.
SAMPLE_RATE = 24000

# Silencio despues de cada frase. Sin el, la ultima silaba queda pegada al
# corte y la frase siguiente arranca encima.
COLA_SEG = 0.35


class KokoroTTSProvider:
    """Un WAV por escena, concatenados, con la duracion de cada una."""

    def __init__(self, voz: str = VOZ, velocidad: float = 1.0,
                 avisar_acentos: bool = True):
        self._voz = voz
        self._velocidad = float(velocidad)
        self._avisar_acentos = avisar_acentos
        self._pipe = None

    @property
    def name(self) -> str:
        return "kokoro-%s" % self._voz

    # ── carga perezosa ───────────────────────────────────────────────

    def _cargar(self):
        if self._pipe is None:
            from kokoro import KPipeline
            # lang_code "e" es espanol.
            self._pipe = KPipeline(lang_code="e")
            logger.info("kokoro: pipeline cargado (voz %s)", self._voz)
        return self._pipe

    # ── una frase ────────────────────────────────────────────────────

    def _audio_de(self, texto: str):
        import numpy as np
        pipe = self._cargar()
        trozos = list(pipe(texto, voice=self._voz, speed=self._velocidad))
        if not trozos:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate([np.asarray(t.audio, dtype=np.float32)
                               for t in trozos])

    def synthesize(self, text: str, output_path: Path) -> Path:
        import soundfile as sf
        output_path = Path(output_path).with_suffix(".wav")
        audio = self._audio_de(text)
        if audio.size == 0:
            raise RuntimeError("kokoro no devolvio audio para el texto dado")
        sf.write(str(output_path), audio, SAMPLE_RATE)
        logger.info("kokoro: %d caracteres -> %.2f s", len(text),
                    audio.size / SAMPLE_RATE)
        return output_path

    # ── storyboard completo ──────────────────────────────────────────

    def generate(self, storyboard: Any, output_dir: str) -> NarrationInput:
        import hashlib

        import numpy as np
        import soundfile as sf

        escenas = list(getattr(storyboard, "scenes", []) or [])
        textos = [(getattr(s, "narration", "") or "").strip() for s in escenas]

        if self._avisar_acentos:
            self._revisar_acentos("\n".join(t for t in textos if t))

        piezas = []
        duraciones = []
        cola = np.zeros(int(SAMPLE_RATE * COLA_SEG), dtype=np.float32)

        for i, texto in enumerate(textos, 1):
            if not texto:
                # Una escena sin narracion no se salta: se le da su silencio,
                # o los cortes de imagen se corren una escena.
                duraciones.append(COLA_SEG)
                piezas.append(cola)
                continue
            audio = self._audio_de(texto)
            dur = audio.size / SAMPLE_RATE + COLA_SEG
            duraciones.append(dur)
            piezas.append(np.concatenate([audio, cola]))
            logger.info("kokoro: escena %d/%d  %.2f s", i, len(textos), dur)

        if not piezas:
            raise RuntimeError("el storyboard no tiene narracion que sintetizar")

        completo = np.concatenate(piezas)
        os.makedirs(output_dir, exist_ok=True)
        destino = os.path.join(output_dir, "narration.wav")
        sf.write(destino, completo, SAMPLE_RATE)

        total = completo.size / SAMPLE_RATE
        texto_todo = " ".join(t for t in textos if t)
        logger.info("kokoro: %d escenas, %.1f s, %.0f palabras/minuto",
                    len(escenas), total,
                    len(texto_todo.split()) / (total / 60) if total else 0)

        return NarrationInput(
            audio_path=destino,
            duration_seconds=total,
            source="tts",
            tts_engine=self.name,
            text_hash=hashlib.sha256(texto_todo.encode("utf-8")).hexdigest(),
            duraciones_por_escena=duraciones,
        )

    # ── acentos ──────────────────────────────────────────────────────

    @staticmethod
    def _revisar_acentos(texto: str) -> None:
        """Avisa, no corrige ni detiene. Ver el encabezado del modulo."""
        try:
            from synthesis.validar_acentos import revisar
        except ImportError:
            return
        try:
            r = revisar(texto)
        except Exception as e:
            logger.warning("kokoro: no se pudo revisar la acentuacion (%s)", e)
            return
        if r.errores:
            logger.warning(
                "kokoro: %d palabras mal acentuadas en la narracion; la voz "
                "va a pronunciar otra silaba. NO se corrigen solas:",
                len(r.errores))
            for a in r.errores[:20]:
                logger.warning("  %s", a)
        else:
            logger.info("kokoro: acentuacion sin errores (%d palabras)",
                        r.total_palabras)
