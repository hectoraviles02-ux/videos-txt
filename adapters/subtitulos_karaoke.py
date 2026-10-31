# -*- coding: utf-8 -*-
"""Subtitulos con la palabra que se esta diciendo resaltada.

Los subtitulos de `subtitulos.py` aparecen y desaparecen por bloques: sirven
para entender, no para acompanar. Este modulo hace lo otro — la frase
completa a la vista y la palabra exacta que suena, encendida.

MEDIDO el 2026-08-22 sobre un documental que funciona (el caso del Banco de
Bangladesh, 5:09): dos lineas visibles, palabra actual en amarillo sobre
blanco, y **dos cortes de imagen en cinco minutos**. Ese video no se sostiene
en el montaje: se sostiene en la voz y en el texto que la sigue. El
subtitulo no es un accesorio ahi, es la mitad del efecto.

**De donde salen los tiempos.** No se estiman repartiendo la escena entre sus
palabras: eso produce un resaltado que se despega de la voz a la tercera
frase. Se transcribe el audio YA GENERADO con Whisper, que devuelve el inicio
y el fin de cada palabra. Es una vuelta de mas, y es la unica forma de que
coincida.

Se escribe ASS y no SRT porque SRT no sabe pintar una palabra de otro color.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Palabras a la vista al mismo tiempo. Con menos, el ojo salta demasiado; con
# mas, la linea se hace larga y deja de leerse de un vistazo.
PALABRAS_POR_GRUPO = 5

# Colores en formato ASS: &HAABBGGRR, al reves de lo habitual.
BLANCO = "&H00FFFFFF"
AMARILLO = "&H0000D7FF"
NEGRO = "&H00000000"

# El texto vive en la franja inferior. En un video vertical con la imagen al
# centro, ahi hay barra negra: el subtitulo no tapa nada.
MARGEN_INFERIOR = 90


@dataclass
class Palabra:
    texto: str
    inicio: float
    fin: float


def palabras_de_audio(audio: str, idioma: str = "es",
                      modelo: str = "large-v3") -> list[Palabra]:
    """Transcribe el audio y devuelve cada palabra con su tiempo.

    Se transcribe el audio que YA generamos, no el guion: entre lo que el
    guion dice y lo que la voz pronuncia hay diferencias -pausas, enlaces- y
    el resaltado tiene que seguir a la voz.
    """
    try:
        import torch
        from faster_whisper import WhisperModel
    except ImportError as e:
        logger.warning("karaoke: falta faster-whisper (%s)", e)
        return []
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    m = WhisperModel(modelo, device=dev,
                     compute_type="float16" if dev == "cuda" else "int8")
    segs, _ = m.transcribe(audio, language=idioma, word_timestamps=True,
                           vad_filter=True, beam_size=5)
    palabras = []
    for s in segs:
        for p in (s.words or []):
            t = (p.word or "").strip()
            if t and p.end > p.start:
                palabras.append(Palabra(t, float(p.start), float(p.end)))
    logger.info("karaoke: %d palabras con tiempo", len(palabras))
    # Mismo motivo que en processors: este modelo pesa unos 3 GB y el
    # generador de video los necesita.
    del m
    try:
        import gc
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            logger.info("karaoke: modelo liberado, %.0f MiB libres",
                        torch.cuda.mem_get_info()[0] / 1024 ** 2)
    except Exception as e:
        logger.warning("karaoke: no se pudo liberar (%s)", e)
    return palabras


def _escapar(t: str) -> str:
    """Las llaves abren etiquetas en ASS; una llave literal romperia la linea."""
    return t.replace("\\", "").replace("{", "(").replace("}", ")")


def _marca(seg: float) -> str:
    seg = max(0.0, seg)
    h = int(seg // 3600)
    m = int((seg % 3600) // 60)
    s = seg % 60
    return "%d:%02d:%05.2f" % (h, m, s)


def construir_eventos(palabras: list[Palabra],
                      por_grupo: int = PALABRAS_POR_GRUPO) -> list[str]:
    """Un evento por palabra: el grupo entero visible, la actual encendida.

    Se emite el grupo completo en cada evento y no solo la palabra suelta
    porque leer palabra por palabra cansa: el ojo necesita el contexto de la
    frase para ir por delante de la voz.
    """
    eventos = []
    for i in range(0, len(palabras), por_grupo):
        grupo = palabras[i:i + por_grupo]
        for j, actual in enumerate(grupo):
            partes = []
            for k, p in enumerate(grupo):
                texto = _escapar(p.texto)
                if k == j:
                    partes.append("{\\c%s}%s{\\c%s}" % (AMARILLO, texto, BLANCO))
                else:
                    partes.append(texto)
            # El evento dura lo que dura la palabra; el siguiente arranca
            # donde este termina, sin huecos que apaguen el texto.
            fin = grupo[j + 1].inicio if j + 1 < len(grupo) else actual.fin
            fin = max(fin, actual.fin)
            eventos.append(
                "Dialogue: 0,%s,%s,Karaoke,,0,0,0,,%s"
                % (_marca(actual.inicio), _marca(fin), " ".join(partes)))
    return eventos


def escribir_ass(palabras: list[Palabra], ruta: str,
                 ancho: int = 1920, alto: int = 1080,
                 tamano: int | None = None) -> str | None:
    """Escribe el archivo ASS. Devuelve None si no hay palabras."""
    if not palabras:
        return None
    # El tamano se ata a la altura del cuadro: 22 puntos que se leen en 1080
    # quedan ilegibles en un vertical de 1920 de alto.
    #
    # alto/11, que en 1080 da 98. NO es una estimacion: es el Fontsize
    # exacto del .ass que produjo
    # videos/2026-08-25_la-impresora-y-el-robo-bangladesh-FINAL.mp4, el
    # video que el usuario senalo el 2026-08-29 como el resultado
    # esperado. Antes estaba en alto/20 = 54, la mitad, y el subtitulo se
    # perdia sobre la imagen. Aqui el subtitulo no es una ayuda: es la
    # mitad del efecto.
    tam = tamano or max(22, int(alto / 11))
    cabecera = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: %d\nPlayResY: %d\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
        "OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,"
        "ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,"
        "MarginL,MarginR,MarginV,Encoding\n"
        # BorderStyle 3 = caja negra opaca detras del texto, con Outline 8
        # de grosor. El contorno simple (BorderStyle 1, Outline 3) se comia
        # el texto sobre fondos claros; la caja lo separa siempre. Son los
        # valores del .ass del documental de referencia del 2026-08-25.
        "Style: Karaoke,Arial,%d,%s,%s,%s,%s,1,0,0,0,100,100,0,0,3,8,0,2,"
        "40,40,%d,1\n\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,"
        "Text\n"
        % (ancho, alto, tam, BLANCO, AMARILLO, NEGRO, NEGRO, MARGEN_INFERIOR)
    )
    os.makedirs(os.path.dirname(os.path.abspath(ruta)) or ".", exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(cabecera)
        f.write("\n".join(construir_eventos(palabras)))
        f.write("\n")
    logger.info("karaoke: %s con %d palabras", os.path.basename(ruta),
                len(palabras))
    return ruta


def generar(audio: str, ruta_ass: str, ancho: int = 1920, alto: int = 1080,
            idioma: str = "es") -> str | None:
    """Del audio narrado al archivo de subtitulos karaoke."""
    palabras = palabras_de_audio(audio, idioma=idioma)
    if not palabras:
        logger.warning("karaoke: sin palabras; el video ira sin subtitulos")
        return None
    return escribir_ass(palabras, ruta_ass, ancho=ancho, alto=alto)
