# -*- coding: utf-8 -*-
"""Angulo narrativo. Toda la parte DETERMINISTA, incluida la lectura de cache.

**Ninguna funcion de este modulo toca la red.** Ni Ollama, ni HTTP, ni nada. Las
dos ramas que existen hoy son deterministas por construccion:

1. `sin_llm=True` -> `plantilla()`, que es texto armado con reglas fijas.
2. Acierto en `cache_llm` por `hash_entrada` -> el texto guardado.
   Sin acierto, cae a `plantilla()`.

La llamada real a Ollama y la ESCRITURA de la cache llegan en el bloque 8. La
lectura vive aqui y no alla por una razon concreta: AC-2 compara un replay
`--sin-llm` contra un replay con la cache llena, y esa comparacion es la
compuerta del bloque 7. Sin la rama de cache, el bloque 7 no podria cerrar su
propia compuerta.

**El angulo no puede mover el ranking** (RQ-33, AC-2). `proponer` se ejecuta
DESPUES de que el TOP y su orden estan fijados, asi que por construccion no
influye en quien entra ni en que posicion. Es la propiedad que permite afirmar
que el modelo de lenguaje no decide nada del contenido publicado.

**`piezas.angulo` nunca es NULL** (D-07). Si no hay LLM, si la cache esta vacia
y si todo falla, sigue saliendo la plantilla. Un angulo pobre es peor que uno
bueno, pero un `NULL` rompe el informe entero.
"""

from __future__ import annotations

import hashlib
import sqlite3

from content_factory.config import Marca

#: Version del prompt. **Entra en la llave de cache**: si cambia la redaccion
#: del prompt, los angulos viejos dejan de servir y hay que pedirlos de nuevo.
#: Sin esto, un cambio de prompt devolveria texto de la version anterior sin que
#: nadie lo notara.
PROMPT_VERSION = "a1"

#: Como se abre el angulo segun la intencion detectada por el clasificador. Es
#: una tabla fija, no una generacion: dos corridas con el mismo tema dan la
#: misma frase.
APERTURA_POR_INTENT: dict[str, str] = {
    "tutorial": "Paso a paso, en una laptop normal",
    "experimento": "Lo probe y esto fue lo que salio",
    "critica": "Lo que no se dice de",
    "caso": "Como lo resolvi",
    "analisis": "Que significa en concreto",
    "noticia": "Que cambia con",
}
APERTURA_POR_DEFECTO = "Que hay detras de"

#: Cierre segun el formato del dia. Deriva de la cadencia, que a su vez deriva
#: de `fecha_corte`, nunca del reloj (D-06).
CIERRE_POR_FORMATO: dict[str, str] = {
    "corto": "en menos de tres minutos",
    "largo": "con el detalle completo",
    "podcast": "conversado a dos voces",
    "experimento": "con la medicion a la vista",
    "analisis": "sin adornos",
}
CIERRE_POR_DEFECTO = "explicado sin rodeos"


def plantilla(cand, marca: Marca, formato: str = "corto") -> str:
    """Angulo armado con reglas. Determinista y sin red.

    No es un texto brillante y no pretende serlo: es el piso que garantiza que
    `piezas.angulo` nunca sea NULL. El modelo de lenguaje, cuando llegue en el
    bloque 8, escribe algo mejor; si no llega, esto sale igual.

    `marca` entra por el contrato del diseno. Hoy no se consulta: el nombre de
    la marca sigue en PENDIENTE y meterlo en el angulo escribiria la palabra
    PENDIENTE en el informe. Se queda en la firma para cuando el operador lo
    defina.
    """
    apertura = APERTURA_POR_INTENT.get(cand.intent, APERTURA_POR_DEFECTO)
    cierre = CIERRE_POR_FORMATO.get(formato, CIERRE_POR_DEFECTO)
    titulo = " ".join(str(cand.titulo).split())
    return "%s: %s, %s." % (apertura, titulo, cierre)


def hash_entrada(modelo: str, prompt_version: str, texto: str) -> str:
    """Llave de `cache_llm`: `sha256(modelo|prompt_version|texto)`.

    Los tres componentes van separados por una barra vertical y en ese orden
    fijo. Incluir el modelo y la version del prompt es lo que impide servir un
    angulo escrito por otro modelo o con otro prompt como si fuera el de ahora.
    """
    crudo = "%s|%s|%s" % (modelo, prompt_version, texto)
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()


def leer_cache(conn: sqlite3.Connection, llave: str) -> str | None:
    """Busca el angulo guardado. Devuelve `None` si no hay, sin levantar."""
    fila = conn.execute(
        "SELECT salida_json FROM cache_llm WHERE hash_entrada = ?", (llave,)
    ).fetchone()
    if fila is None:
        return None
    salida = fila[0]
    return salida if salida else None


def proponer(
    cand,
    conn: sqlite3.Connection,
    marca: Marca,
    *,
    sin_llm: bool,
    formato: str = "corto",
    modelo: str = "",
    pedir=None,
) -> tuple[str, str]:
    """`(angulo, metodo)` con `metodo` en `plantilla | cache_llm`.

    `pedir` existe en la firma para el bloque 8 y **aqui se ignora a proposito**.
    Dejarlo declarado y sin usar es deliberado: en el replay se le pasa una
    funcion que LANZA, y que el replay pase demuestra que no se llamo. Si el
    parametro no existiera, esa demostracion habria que escribirla de otra forma
    mas floja.

    Con `sin_llm` no se consulta ni siquiera la cache: el replay `--sin-llm`
    tiene que dar el mismo resultado en una base recien creada, y una cache
    poblada lo cambiaria.
    """
    if sin_llm:
        return plantilla(cand, marca, formato), "plantilla"

    llave = hash_entrada(modelo, PROMPT_VERSION, str(cand.titulo))
    guardado = leer_cache(conn, llave)
    if guardado is not None:
        return guardado, "cache_llm"

    # Sin acierto de cache: la plantilla. No se llama a la red desde aqui.
    return plantilla(cand, marca, formato), "plantilla"
