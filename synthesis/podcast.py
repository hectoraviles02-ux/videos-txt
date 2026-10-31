# -*- coding: utf-8 -*-
"""Episodio de podcast a dos voces desde una fuente ya extraida.

La cadena tiene tres piezas y ninguna inventa contenido:

1. `puntos_desde_transcripcion` — el modelo local LEE los segmentos por
   tramos y devuelve puntos, cada uno con el timestamp del que salio. Ese
   timestamp es lo que permite volver a la fuente, que es la regla del
   proyecto: lo creativo es la forma, los hechos se rastrean.
2. `dialogo_desde_puntos` — escribe los turnos usando SOLO esos puntos.
3. el montaje del audio vive en `synthesis/podcast_audio.py`.

MEDIDO el 2026-08-21 armando el primer episodio (3:29) desde la charla de
Boris sobre Claude Code:

- Un pedido de 26 turnos de una sola vez devolvio 11. Pedido en bloques de
  9 devolvio 27. Por eso `dialogo_desde_puntos` va por bloques.
- Poner un tope bajo de palabras por turno hizo que el modelo escribiera
  TITULARES ("Compatible con VS Code, Xcode, JetBrains sin cambios en flujo
  de trabajo"), que nadie dice en voz alta. Por eso hay un minimo, no solo
  un maximo.
"""
from __future__ import annotations

import json
import logging
import re

import requests

from synthesis.podcast_audio import (
    DALIA,
    JORGE,
    Episodio,
    Voz,
    montar_episodio,
)

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
MODELO = "qwen2.5:14b-instruct-q4_K_M"

# Tramos con los que se lee la transcripcion. Mas grande no entra en la
# ventana junto con la respuesta; mas chico parte las ideas a la mitad.
PALABRAS_POR_TRAMO = 900
PUNTOS_POR_TRAMO = 6

# Turnos por pedido. Ver la nota de arriba sobre el pedido grande.
TURNOS_POR_BLOQUE = 9

# Rango de palabras por turno. El minimo NO es decorativo: sin el, el modelo
# devuelve titulares en vez de habla.
MIN_PALABRAS_TURNO = 18
MAX_PALABRAS_TURNO = 38

class OllamaNoResponde(RuntimeError):
    """El modelo local no esta disponible. El fallo se dice, no se disimula."""


def _pedir(prompt: str, num_predict: int = 1600,
           temperature: float = 0.8, timeout: int = 1200) -> dict:
    try:
        r = requests.post(
            OLLAMA_URL,
            json={"model": MODELO, "prompt": prompt, "stream": False,
                  "format": "json",
                  "options": {"temperature": temperature, "top_p": 0.9,
                              "repeat_penalty": 1.12, "num_ctx": 8192,
                              "num_predict": num_predict}},
            timeout=timeout,
        )
        r.raise_for_status()
        return json.loads(r.json()["response"])
    except requests.RequestException as e:
        raise OllamaNoResponde("Ollama no respondio: %s" % e) from e
    except (ValueError, KeyError) as e:
        raise OllamaNoResponde("Ollama devolvio algo que no es JSON: %s" % e) from e


def _mmss(segundos: float) -> str:
    return "%d:%02d" % (int(segundos) // 60, int(segundos) % 60)


def _correcciones(avisos: list[str]) -> list[tuple[re.Pattern, str]]:
    """Reglas de correccion que DECLARA la propia fuente.

    Los `avisos` del .json registran los errores conocidos de la
    transcripcion -por ejemplo que el ASR escribe 'Quadcode' donde se dice
    'Claude Code'-. Corregir eso es trazable: sale de la fuente, no de una
    suposicion. No se corrige nada que la fuente no haya declarado.
    """
    reglas = []
    for aviso in avisos or []:
        if "asr" not in aviso.lower():
            continue
        # La direccion la marca la palabra "como": lo que va ANTES es lo que
        # se dijo de verdad, lo que va DESPUES es como quedo transcrito.
        #   "El ASR transcribe 'Claude Code' como 'Quadcode'/'Quad'/'Cloud'"
        # Sin ese corte no se sabe cual de las citas es la buena: la primera
        # version tomaba la ultima y reemplazaba justo al reves.
        partes = re.split(r"\bcomo\b", aviso, maxsplit=1, flags=re.IGNORECASE)
        if len(partes) != 2:
            logger.debug("podcast: aviso sin direccion clara, se ignora: %r",
                         aviso[:80])
            continue
        correctas = re.findall(r"'([^']{2,40})'", partes[0])
        malas = re.findall(r"'([^']{2,40})'", partes[1])
        if not correctas or not malas:
            continue
        correcto = correctas[0]
        for malo in malas:
            if malo.lower() == correcto.lower():
                continue
            reglas.append(
                (re.compile(r"\b%s\b" % re.escape(malo), re.IGNORECASE),
                 correcto))
    return reglas


def _aplicar(texto: str, reglas: list[tuple[re.Pattern, str]]) -> str:
    for patron, reemplazo in reglas:
        texto = patron.sub(reemplazo, texto)
    return texto


def puntos_desde_transcripcion(datos: dict) -> list[dict[str, str]]:
    """El modelo lee los segmentos por tramos y devuelve puntos con timestamp.

    `datos` es el .json de `procesado/`, tal cual.
    """
    segmentos = datos.get("segmentos") or []
    if not segmentos:
        return []

    tramos: list[list[dict]] = []
    actual: list[dict] = []
    palabras = 0
    for s in segmentos:
        actual.append(s)
        palabras += len(str(s.get("texto", "")).split())
        if palabras >= PALABRAS_POR_TRAMO:
            tramos.append(actual)
            actual, palabras = [], 0
    if actual:
        tramos.append(actual)

    aviso_asr = ""
    for a in datos.get("avisos") or []:
        if "asr" in a.lower():
            aviso_asr = ("OJO con la transcripcion automatica: %s Corrige eso "
                         "al citar.\n" % a)
            break

    puntos: list[dict[str, str]] = []
    for i, tramo in enumerate(tramos, 1):
        texto = "\n".join(
            "[%s] %s" % (_mmss(s.get("inicio_seg", 0)), s.get("texto", ""))
            for s in tramo
        )
        prompt = (
            aviso_asr +
            "Si una frase esta claramente rota o no se entiende, IGNORALA: no "
            "adivines lo que queria decir.\n\n"
            "Saca los puntos CONCRETOS y utiles de este tramo: consejos, "
            "mecanismos, ejemplos, cifras. Nada de generalidades.\n"
            "Cada punto lleva el timestamp del que salio.\n\n"
            "TRAMO:\n" + texto + "\n\n"
            'Responde JSON: {"puntos":[{"t":"m:ss","punto":"..."}]} '
            "Maximo %d puntos. Si el tramo no dice nada concreto, devuelve "
            "lista vacia." % PUNTOS_POR_TRAMO
        )
        try:
            r = _pedir(prompt, num_predict=1200, temperature=0.3)
        except OllamaNoResponde as e:
            logger.warning("podcast: tramo %d sin puntos (%s)", i, e)
            continue
        for x in r.get("puntos") or []:
            if isinstance(x, dict) and x.get("punto"):
                puntos.append({"t": str(x.get("t", "")),
                               "punto": str(x["punto"]).strip()})
        logger.info("podcast: tramo %d/%d -> %d puntos", i, len(tramos),
                    len(r.get("puntos") or []))
    return puntos


def dialogo_desde_puntos(puntos: list[dict[str, str]],
                         anfitriones: tuple[Voz, Voz] = (JORGE, DALIA),
                         ) -> list[dict[str, str]]:
    """Escribe los turnos usando SOLO los puntos. Va por bloques a proposito."""
    if not puntos:
        return []
    a, b = anfitriones

    base = (
        "Guion de PODCAST en espanol de Mexico, dos anfitriones.\n"
        "%s %s\n%s %s\n\n"
        "REGLAS:\n"
        "- Solo puedes decir lo que este en los PUNTOS. Nada de adjetivos de "
        "folleto ('revolucionario', 'potente', 'impresionante') ni funciones "
        "inventadas.\n"
        "- ALTERNA: %s, %s, %s, %s. Nunca dos turnos seguidos del mismo.\n"
        "- Que se contesten: que uno retome lo que dijo el otro.\n"
        "- Entre %d y %d palabras por turno. Es HABLA, no titulares: nadie "
        "dice en voz alta 'Compatible con VS Code, Xcode, JetBrains sin "
        "cambios en flujo de trabajo'.\n"
        % (a.nombre, a.papel, b.nombre, b.papel,
           a.nombre, b.nombre, a.nombre, b.nombre,
           MIN_PALABRAS_TURNO, MAX_PALABRAS_TURNO)
    )

    n = max(1, len(puntos) // 3)
    bloques = [
        (puntos[:n], "Arranca EN SECO con algo concreto. PROHIBIDO "
                     "'bienvenidos', 'hoy hablaremos', 'en el episodio de hoy'."),
        (puntos[n:2 * n], "Va a la mitad del episodio. No presentes el tema "
                          "otra vez: ya se presento. Entra al detalle."),
        (puntos[2 * n:], "Es el final. Cierra con una idea util y concreta, "
                         "no con despedida generica."),
    ]

    turnos: list[dict[str, str]] = []
    for ps, instruccion in bloques:
        if not ps:
            continue
        lista = "\n".join("- [%s] %s" % (p["t"], p["punto"]) for p in ps)
        continuidad = ""
        if turnos:
            continuidad = (
                "\nEl turno anterior fue de %s y dijo: \"%s\"\nContinua desde "
                "ahi; empieza con el OTRO anfitrion.\n"
                % (turnos[-1]["quien"], turnos[-1]["texto"])
            )
        prompt = (
            base + "\n" + instruccion + "\n" + continuidad +
            "\nPUNTOS DE ESTE BLOQUE (unica fuente):\n" + lista + "\n\n"
            "Escribe EXACTAMENTE %d turnos.\n"
            'Responde JSON: {"turnos":[{"quien":"%s","texto":"..."}]}\n'
            % (TURNOS_POR_BLOQUE, a.nombre)
        )
        try:
            r = _pedir(prompt, num_predict=2000)
        except OllamaNoResponde as e:
            logger.warning("podcast: bloque sin turnos (%s)", e)
            continue
        for t in r.get("turnos") or []:
            if not (isinstance(t, dict) and t.get("texto")):
                continue
            quien = (b.nombre
                     if str(t.get("quien", "")).upper().startswith(b.nombre[0])
                     else a.nombre)
            turnos.append({"quien": quien, "texto": str(t["texto"]).strip()})
    return turnos


def revisar_dialogo(turnos: list[dict[str, str]]) -> list[str]:
    """Defectos OBSERVABLES, no opiniones. Se reportan, no se disimulan."""
    defectos = []
    seguidos = [i + 1 for i in range(1, len(turnos))
                if turnos[i]["quien"] == turnos[i - 1]["quien"]]
    if seguidos:
        defectos.append("turnos seguidos del mismo hablante: %s" % seguidos)
    cortos = [i + 1 for i, t in enumerate(turnos)
              if len(t["texto"].split()) < MIN_PALABRAS_TURNO]
    if cortos:
        defectos.append("turnos por debajo de %d palabras (suenan a titular): "
                        "%s" % (MIN_PALABRAS_TURNO, cortos))
    largos = [i + 1 for i, t in enumerate(turnos)
              if len(t["texto"].split()) > MAX_PALABRAS_TURNO]
    if largos:
        defectos.append("turnos por encima de %d palabras: %s"
                        % (MAX_PALABRAS_TURNO, largos))
    return defectos


def episodio_desde_procesado(json_path: str, directorio: str,
                             titulo: str | None = None) -> Episodio:
    """De un .json de `procesado/` a un episodio. La entrada de alto nivel."""
    with open(json_path, encoding="utf-8") as f:
        datos = json.load(f)

    reglas = _correcciones(datos.get("avisos") or [])
    puntos = puntos_desde_transcripcion(datos)
    for p in puntos:
        p["punto"] = _aplicar(p["punto"], reglas)

    turnos = dialogo_desde_puntos(puntos)
    for t in turnos:
        t["texto"] = _aplicar(t["texto"], reglas)

    ep = montar_episodio(turnos, directorio,
                         titulo=titulo or datos.get("titulo") or "Episodio",
                         turnos_por_bloque=TURNOS_POR_BLOQUE)
    ep.avisos.extend(revisar_dialogo(turnos))
    # De donde salio cada cosa: la regla del proyecto es poder volver a la fuente.
    ep.avisos.append("fuente: %s" % datos.get("source_url", json_path))
    ep.avisos.append("%d puntos con timestamp" % len(puntos))
    return ep
