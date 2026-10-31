# -*- coding: utf-8 -*-
"""Revisa la acentuacion del texto que va a hablar el TTS.

POR QUE EXISTE

El pipeline escribe espanol en tres lugares y solo dos estaban cubiertos:

    guion de qwen2.5:14b      acentua bien   (verificado el 2026-08-25)
    transcripcion de Whisper  acentua bien   (verificado el 2026-08-25)
    texto escrito A MANO      nadie lo mira

El tercero es el que falla, y falla callado. El 2026-08-25 se sintetizo con
Kokoro la frase "La impresora del banco central dejo de imprimir". Sin la
tilde, el fonetizador del espanol acentua DE-jo -presente- en vez de de-JO
-pasado-: la voz dice una palabra distinta de la que el guion queria.

Y no se ve. Los subtitulos salen de transcribir el AUDIO con Whisper, que
escribio "DEJO" con tilde porque asi sono... no, porque asi lo interpreto.
El resultado es que la imagen se ve correcta y el error solo se oye.

QUE HACE Y QUE NO HACE

Compara cada palabra contra `datos/lexico_es/lexico_acentuacion_es.json`,
construido desde el diccionario libre RLA-ES (LGPL). Tres respuestas:

    ERROR      la palabra sin tilde no existe en espanol y la acentuada si;
               "informacion" solo puede ser "informacion" con tilde
    AMBIGUO    las dos existen y solo el contexto decide: dejo/dejo,
               esta/esta/esta, publico/publico/publico
    CORRECTA   no hay variante acentuada que confundir

NO CORRIGE SOLO, ni siquiera los ERROR. Un reemplazo ciego sobre un texto
con nombres propios, extranjerismos o terminos tecnicos corrompe mas de lo
que arregla. Esta pieza informa; decide una persona.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

RUTA_LEXICO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "datos", "lexico_es", "lexico_acentuacion_es.json")

# Palabras, con tildes y enies. El guion interior de "veintidos-mil" separa.
_PALABRA = re.compile(r"[^\W\d_]+", re.UNICODE)

_lexico_cache: dict | None = None


def _sin_tildes(p: str) -> str:
    """Quita tildes pero CONSERVA la enie: 'ano' y 'ano' no son lo mismo."""
    p = p.replace("ñ", "\x01").replace("Ñ", "\x02")
    p = "".join(c for c in unicodedata.normalize("NFD", p)
                if unicodedata.category(c) != "Mn")
    return p.replace("\x01", "ñ").replace("\x02", "Ñ")


def cargar_lexico(ruta: str | None = None) -> dict:
    """El indice, cacheado. 7.6 MB de JSON: se lee una vez por proceso."""
    global _lexico_cache
    if _lexico_cache is not None:
        return _lexico_cache
    ruta = ruta or RUTA_LEXICO
    if not os.path.isfile(ruta):
        logger.warning("lexico de acentuacion no encontrado en %s; "
                       "la revision queda desactivada", ruta)
        _lexico_cache = {}
        return _lexico_cache
    with open(ruta, encoding="utf-8") as f:
        _lexico_cache = json.load(f).get("indice", {})
    logger.info("lexico de acentuacion: %d claves", len(_lexico_cache))
    return _lexico_cache


@dataclass
class Aviso:
    palabra: str
    sugerencias: list[str]
    seguro: bool          # True = la forma sin tilde no existe en espanol
    contexto: str = ""

    def __str__(self) -> str:
        etiqueta = "ERROR  " if self.seguro else "AMBIGUO"
        return "%s %-16s -> %s   %s" % (
            etiqueta, self.palabra, ", ".join(self.sugerencias), self.contexto)


@dataclass
class Resultado:
    total_palabras: int = 0
    errores: list[Aviso] = field(default_factory=list)
    ambiguos: list[Aviso] = field(default_factory=list)

    @property
    def limpio(self) -> bool:
        """Sin errores seguros. Los ambiguos no invalidan: son normales."""
        return not self.errores

    def informe(self, mostrar_ambiguos: bool = False) -> str:
        lineas = ["%d palabras revisadas" % self.total_palabras,
                  "%d errores de acentuacion" % len(self.errores),
                  "%d ambiguas (las dos formas existen)" % len(self.ambiguos)]
        for a in self.errores:
            lineas.append("  " + str(a))
        if mostrar_ambiguos:
            for a in self.ambiguos:
                lineas.append("  " + str(a))
        return "\n".join(lineas)


def revisar(texto: str, lexico: dict | None = None,
            con_contexto: bool = True) -> Resultado:
    """Revisa un texto. No lo modifica."""
    lex = cargar_lexico() if lexico is None else lexico
    res = Resultado()
    if not lex:
        return res

    for m in _PALABRA.finditer(texto):
        palabra = m.group(0)
        res.total_palabras += 1

        # Una palabra que YA lleva tilde no se cuestiona: si esta en el
        # lexico es correcta, y si no lo esta puede ser un nombre propio.
        if any(unicodedata.combining(c) for c in unicodedata.normalize("NFD", palabra)):
            continue

        variantes = lex.get(_sin_tildes(palabra.lower()))
        if not variantes:
            continue

        acentuadas = [v for v in variantes if v != palabra.lower()]
        if not acentuadas:
            continue

        contexto = ""
        if con_contexto:
            i, j = max(0, m.start() - 28), min(len(texto), m.end() + 28)
            contexto = "...%s..." % texto[i:j].replace("\n", " ")

        # Si la forma tal como esta escrita NO figura en el lexico, la unica
        # lectura posible es la acentuada: error seguro.
        seguro = palabra.lower() not in variantes
        aviso = Aviso(palabra, acentuadas, seguro, contexto)
        (res.errores if seguro else res.ambiguos).append(aviso)

    return res


def revisar_storyboard(storyboard) -> Resultado:
    """Revisa la narracion completa de un storyboard antes de sintetizarla."""
    partes = []
    for s in getattr(storyboard, "scenes", []) or []:
        t = (getattr(s, "narration", None) or getattr(s, "text", None) or "")
        if t:
            partes.append(str(t))
    return revisar("\n".join(partes))


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        with open(sys.argv[1], encoding="utf-8") as f:
            entrada = f.read()
    else:
        entrada = " ".join(sys.argv[1:]) or sys.stdin.read()
    r = revisar(entrada)
    print(r.informe(mostrar_ambiguos="-a" in sys.argv))
    sys.exit(0 if r.limpio else 1)
