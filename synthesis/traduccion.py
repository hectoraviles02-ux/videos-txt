# -*- coding: utf-8 -*-
"""Traduce el guion de un video a otro idioma, conservando los datos.

La voz clonada del usuario habla varios idiomas, asi que el mismo documental
puede salir en otro sin volver a grabar nada. PROBADO el 2026-08-22 con
VoxCPM2, midiendo el tono contra la voz real del usuario (115.9 Hz):

    espanol      109.7 Hz   error 5.4%
    ingles       119.1 Hz   error 2.7%
    portugues    110.6 Hz   error 4.6%

El ingles quedo mas cerca que el propio espanol. La voz aguanta el cambio de
idioma; lo que hay que cuidar es el TEXTO.

**Lo que se traduce y lo que no.** La narracion se traduce; las cifras, las
fechas, los nombres propios y los identificadores NO se tocan. Un documental
que dice "6,500 millones" en espanol tiene que decir "6.5 billion" en ingles
y no "seis mil quinientos" mal convertido: un error de traduccion en una
cifra es indistinguible de un dato inventado, y la regla del proyecto no
distingue entre las dos cosas.

`search_keywords` tampoco se traduce: son terminos de busqueda en un catalogo
de metraje que indexa en ingles. Traducirlos al idioma destino romperia la
seleccion de imagenes que tanto costo afinar.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
MODELO = "qwen2.5:14b-instruct-q4_K_M"

# Escenas por pedido. Un guion entero de una vez sale truncado: MEDIDO el
# 2026-08-22 en este mismo proyecto, pedir 26 turnos de corrido devolvio 11,
# y en bloques de 9 devolvio 27. El limite es de la ventana, no del modelo.
ESCENAS_POR_PEDIDO = 5

IDIOMAS = {
    "en": "ingles",
    "pt": "portugues de Brasil",
    "fr": "frances",
    "it": "italiano",
    "de": "aleman",
    "es": "espanol de Mexico",
}

# Cualquier secuencia de digitos, con sus separadores. Se usa para comprobar
# que la traduccion no perdio ni invento numeros.
_CIFRAS = re.compile(r"\d[\d.,]*")


@dataclass
class Traduccion:
    idioma: str
    escenas: list[dict] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


class TraduccionFallida(RuntimeError):
    """No se pudo traducir. Se dice; no se entrega un guion a medias."""


def _pedir(prompt: str, num_predict: int = 1600) -> dict:
    r = requests.post(
        OLLAMA_URL,
        json={"model": MODELO, "prompt": prompt, "stream": False,
              "format": "json",
              "options": {"temperature": 0.2, "top_p": 0.9,
                          "num_ctx": 8192, "num_predict": num_predict}},
        timeout=900,
    )
    r.raise_for_status()
    return json.loads(r.json()["response"])


def cifras_de(texto: str) -> list[str]:
    """Los numeros del texto, normalizados para poder compararlos.

    Se quitan los separadores porque el ingles y el espanol los usan al
    reves: 6,500 y 6.500 son el mismo numero.
    """
    return [re.sub(r"[.,]", "", c) for c in _CIFRAS.findall(texto)]


def revisar_cifras(original: str, traducido: str) -> list[str]:
    """Compara los numeros de las dos versiones.

    Un numero que aparece en el original y no en la traduccion es un dato
    perdido; uno que aparece solo en la traduccion es un dato inventado. Los
    dos son igual de graves y por eso se reportan.
    """
    a, b = cifras_de(original), cifras_de(traducido)
    faltan = [c for c in a if c not in b]
    sobran = [c for c in b if c not in a]
    avisos = []
    if faltan:
        avisos.append("cifras perdidas: %s" % ", ".join(faltan))
    if sobran:
        avisos.append("cifras que no estaban: %s" % ", ".join(sobran))
    return avisos


def traducir_escenas(escenas: list[dict], idioma: str) -> Traduccion:
    """Traduce la narracion de cada escena. El resto de la escena no se toca."""
    destino = IDIOMAS.get(idioma)
    if not destino:
        raise TraduccionFallida(
            "idioma no soportado: %r. Disponibles: %s"
            % (idioma, ", ".join(sorted(IDIOMAS))))

    t = Traduccion(idioma=idioma)
    for i in range(0, len(escenas), ESCENAS_POR_PEDIDO):
        bloque = escenas[i:i + ESCENAS_POR_PEDIDO]
        lista = "\n".join("%d. %s" % (j + 1, e.get("narration", ""))
                          for j, e in enumerate(bloque))
        prompt = (
            "Traduce al %s estas frases de la narracion de un documental.\n\n"
            "REGLAS:\n"
            "- Las CIFRAS, fechas, nombres propios e identificadores se "
            "conservan tal cual. No conviertas unidades ni redondees.\n"
            "- Es para leerse EN VOZ ALTA: frases naturales del idioma, no "
            "una traduccion literal palabra por palabra.\n"
            "- Manten el registro de documental: afirmativo, sin preguntas "
            "retoricas.\n"
            "- Devuelve EXACTAMENTE %d frases, en el mismo orden.\n\n"
            "FRASES:\n%s\n\n"
            'Responde JSON: {"frases":["...","..."]}'
            % (destino, len(bloque), lista)
        )
        try:
            r = _pedir(prompt)
            frases = [f for f in (r.get("frases") or []) if isinstance(f, str)]
        except (requests.RequestException, ValueError, KeyError) as e:
            raise TraduccionFallida("el modelo no respondio: %s" % e) from e

        if len(frases) != len(bloque):
            # Se conserva el original antes que entregar escenas corridas de
            # lugar: un desfase alinea cada frase con la imagen equivocada.
            t.avisos.append(
                "el bloque %d devolvio %d frases de %d; queda sin traducir"
                % (i // ESCENAS_POR_PEDIDO + 1, len(frases), len(bloque)))
            t.escenas.extend(dict(e) for e in bloque)
            continue

        for e, frase in zip(bloque, frases):
            nueva = dict(e)
            original = e.get("narration", "")
            nueva["narration"] = frase.strip()
            # search_keywords NO se traduce: el catalogo de metraje indexa en
            # ingles y traducirlas romperia la seleccion de imagenes.
            for aviso in revisar_cifras(original, nueva["narration"]):
                t.avisos.append("%s — %s" % (e.get("id", "escena"), aviso))
            t.escenas.append(nueva)

    logger.info("traduccion: %d escenas al %s, %d avisos",
                len(t.escenas), destino, len(t.avisos))
    return t


def traducir_storyboard(storyboard: dict, idioma: str) -> dict:
    """Devuelve una copia del storyboard con la narracion en otro idioma."""
    escenas = storyboard.get("scenes") or []
    if not escenas:
        raise TraduccionFallida("el storyboard no trae escenas")
    t = traducir_escenas(escenas, idioma)
    salida = dict(storyboard)
    salida["scenes"] = t.escenas
    meta = dict(salida.get("metadata") or {})
    meta["idioma"] = idioma
    if t.avisos:
        meta["avisos_traduccion"] = t.avisos
    salida["metadata"] = meta
    return salida
