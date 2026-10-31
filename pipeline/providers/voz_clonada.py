# -*- coding: utf-8 -*-
"""Narracion con una voz clonada, generada localmente.

Mismo contrato que `EdgeTTSAdapter`: recibe el storyboard y devuelve un
`NarrationInput`. La diferencia es de donde sale la voz — aqui de un clip de
referencia y no de un catalogo de voces.

MEDIDO el 2026-08-22 con VoxCPM2 sobre una RTX 2000 Ada (8.6 GB):

- VRAM pico 6.4 GB. Cabe, pero no deja lugar para otro modelo en paralelo.
- Carga inicial ~45 s; despues, ~4.6 s de computo por segundo de audio.
  Una narracion de minuto y medio tarda unos siete minutos.
- La calidad la fija la referencia, no su duracion. Con audio limpio el clon
  quedo a 1.4-1.7% del tono original; con audio de llamada telefonica, a
  9-12%. Mas minutos de material malo no arreglan eso.

Se genera POR ESCENA y no de corrido: un texto largo hace que el modelo
pierda el hilo prosodico, y ademas asi un fallo cuesta una escena y no la
narracion entera.
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import wave
from typing import Any

from adapters.base import NarrationInput
from adapters.ffmpeg_tools import resolve_ffmpeg

logger = logging.getLogger(__name__)

MODELO = "openbmb/VoxCPM2"

# Parametros de generacion.
#
# CFG gobierna cuanto se pega el modelo a la referencia. Estaba en 2.0 -el
# valor que documenta el modelo- y con el la narracion salia plana.
#
# MEDIDO el 2026-08-24 con la desviacion tipica de F0 en semitonos, que es la
# metrica de expresividad (<2 monotono, 2-3.5 lectura neutra, 3.5-6 narracion
# expresiva). Mismo texto, misma referencia, solo cambia cfg:
#
#     cfg 1.3   2.58 st          cfg 3.0   3.46 st
#     cfg 2.0   2.88 st          cfg 4.5   3.70 st   <- expresivo
#                                 cfg 6.0   4.67 st
#
# La referencia del usuario mide 4.54 st: su voz SI tiene registro, el
# clonador era el que lo perdia -entregaba 2.47, el 46% menos-.
#
# Se toma 4.5 y no 6.0 aunque 6.0 mida mas: su pico llega a 0.987 y una frase
# mas fuerte satura. A 4.5 el pico queda en 0.943, con margen. No se recorta
# nada en ninguno de los dos -0.00% de muestras al tope-, pero el margen es
# lo que separa una medicion de una apuesta.
CFG = 4.5

# Pasos de difusion. MEDIDO el 2026-08-24, mismo texto y misma referencia:
#
#     10 pasos   3.49 st   28s
#     20 pasos   4.22 st   32s   <- optimo
#     32 pasos   2.46 st   49s
#
# No es monotono: a 32 la expresividad se DERRUMBA por debajo de la de 10.
# Mas pasos no es mejor, y por eso el valor sale de la medicion y no de
# suponer que refinar mas refina mas.
PASOS = 20

# Silencio entre escenas. Sin el, las frases se pegan y el documental suena
# apurado; con mas, se despega de los cortes de imagen.
PAUSA_ESCENA = 0.35


class VozClonadaAdapter:
    """Narra el storyboard con la voz de un clip de referencia.

    `referencia_texto` no es opcional por comodidad: el modelo necesita saber
    QUE dice el clip para separar la voz del contenido. Sin eso, el clonado
    degrada de forma notoria.
    """

    def __init__(self, referencia_wav: str, referencia_texto: str,
                 modelo: str = MODELO, ffmpeg_path: str | None = None):
        if not os.path.isfile(referencia_wav):
            raise FileNotFoundError("no existe el audio de referencia: %s"
                                    % referencia_wav)
        if not (referencia_texto or "").strip():
            raise ValueError("hace falta el texto que dice la referencia")
        self._ref = referencia_wav
        self._ref_txt = referencia_texto.strip()
        self._modelo = modelo
        self._ffmpeg_path = ffmpeg_path
        self._pipe = None

    @property
    def name(self) -> str:
        return "voz-clonada-voxcpm2"

    def _cargar(self):
        if self._pipe is None:
            from voxcpm import VoxCPM
            logger.info("voz_clonada: cargando %s", self._modelo)
            # El denoiser venia apagado y nunca se habia probado. MEDIDO el
            # 2026-08-24: encenderlo no solo limpia el ruido, sube la
            # expresividad de 2.28 a 3.69 st. El clon copia el ruido del clip
            # igual que copia la voz, y sobre una referencia sucia imita peor.
            self._pipe = VoxCPM.from_pretrained(self._modelo,
                                                load_denoiser=True)
        return self._pipe

    def liberar(self) -> None:
        """Suelta el modelo y la VRAM. Idempotente: se puede llamar dos
        veces, y si se vuelve a narrar `_cargar` lo trae de nuevo."""
        if self._pipe is None:
            return
        self._pipe = None
        try:
            import gc

            import torch
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                libre = torch.cuda.mem_get_info()[0] / 1024 ** 2
                logger.info("voz_clonada: modelo liberado, %.0f MiB "
                            "libres en la tarjeta", libre)
        except ImportError:
            pass

    def generate(self, storyboard: Any, output_dir: str) -> NarrationInput:
        import numpy as np
        import soundfile as sf

        os.makedirs(output_dir, exist_ok=True)
        escenas = list(getattr(storyboard, "scenes", []) or [])
        textos = [(s.narration or "").strip() for s in escenas]
        if not any(textos):
            raise RuntimeError("el storyboard no trae narracion")

        pipe = self._cargar()
        sr = pipe.tts_model.sample_rate
        silencio = np.zeros(int(PAUSA_ESCENA * sr), dtype=np.float32)

        piezas = []
        duraciones: list[float] = []
        for i, texto in enumerate(textos, 1):
            if not texto:
                continue
            try:
                wav = pipe.generate(text=texto, prompt_wav_path=self._ref,
                                    prompt_text=self._ref_txt,
                                    cfg_value=CFG, inference_timesteps=PASOS,
                                    # load_denoiser solo CARGA el modelo; sin
                                    # esta bandera no se aplica a nada.
                                    denoise=True)
            except Exception as e:
                # Una escena caida no debe tumbar la narracion entera; se deja
                # su hueco en silencio y se avisa.
                logger.warning("voz_clonada: escena %d fallo (%s); va en "
                               "silencio", i, e)
                wav = np.zeros(int(2.0 * sr), dtype=np.float32)
            piezas.append(np.asarray(wav, dtype=np.float32))
            piezas.append(silencio)
            # Cuanto duro DE VERDAD esta escena, con su pausa. Es lo que deja
            # que el corte de imagen y el subtitulo caigan donde termina la
            # frase, en vez de repartir el total a partes iguales.
            duraciones.append(len(wav) / sr + PAUSA_ESCENA)
            logger.info("voz_clonada: escena %d/%d lista (%.1fs)",
                        i, len(textos), len(wav) / sr)

        if not piezas:
            raise RuntimeError("no se genero audio")

        crudo = os.path.join(output_dir, "_voz_cruda.wav")
        sf.write(crudo, np.concatenate(piezas), sr)

        wav_path = os.path.join(output_dir, "narration.wav")
        ffmpeg = resolve_ffmpeg(self._ffmpeg_path)
        try:
            subprocess.run(
                [ffmpeg, "-y", "-i", crudo,
                 # el pipeline espera 44.1 kHz mono PCM
                 "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1",
                 # el clonado deja el nivel disparejo entre escenas
                 "-af", "loudnorm=I=-18:TP=-2:LRA=11",
                 wav_path],
                capture_output=True, text=True, timeout=600, check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError("ffmpeg no pudo normalizar la narracion: %s"
                               % (e.stderr or "")[-300:])
        finally:
            if os.path.exists(crudo):
                os.remove(crudo)

        duracion = _duracion_wav(wav_path)
        if duracion <= 0:
            raise RuntimeError("la narracion salio vacia")

        # loudnorm no cambia la duracion, pero el remuestreo puede correrla
        # unas decimas; se ajusta la ultima para que sumen exacto.
        if duraciones:
            deriva = duracion - sum(duraciones)
            duraciones[-1] = max(0.5, duraciones[-1] + deriva)

        # La narracion ya esta en disco: el modelo no hace falta mas, y
        # lo que viene despues (AnimateDiff) necesita la tarjeta entera.
        # MEDIDO el 2026-08-29: sin esto el generador de video encontraba
        # la GPU al 97% y tardaba 19 min por clip en vez de 28 s.
        self.liberar()

        texto_completo = " ".join(textos)
        return NarrationInput(
            audio_path=wav_path,
            duration_seconds=duracion,
            source="tts",
            tts_engine=self.name,
            text_hash=hashlib.sha256(
                texto_completo.encode("utf-8")).hexdigest()[:16],
            duraciones_por_escena=duraciones or None,
        )


def _duracion_wav(ruta: str) -> float:
    try:
        with wave.open(ruta, "rb") as w:
            fr = w.getframerate()
            return w.getnframes() / fr if fr else 0.0
    except (wave.Error, OSError):
        return 0.0
