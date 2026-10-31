# -*- coding: utf-8 -*-
"""Hechos que salen de una base de datos propia.

Las fuentes de `investigacion.py` traen lo que otros publicaron. Esta trae lo
que uno mismo midio, y eso cambia el peso de un documental: los datos de
banco los tiene cualquiera; los propios, no.

**La frontera con otros sistemas.** El CLAUDE.md de este proyecto prohibe
tocar codigo, datos o credenciales de otros repositorios, y esta pieza
podria parecer una excepcion. No lo es, porque este modulo no sabe de ningun
otro sistema: recibe una RUTA y unas CONSULTAS desde configuracion, y no
importa nada, no conoce esquemas ni asume tablas. Si manana la base cambia
de lugar o de dueno, aqui no se toca una linea.

**Solo lectura, y en serio.** Se abre con `mode=ro` sobre URI de sqlite, que
falla si el archivo no existe en vez de crearlo, y rechaza cualquier
escritura a nivel del motor. Cuando exista una replica de lectura al lado
-un archivo `.read.db`- se prefiere esa: leer 21 GB mientras el sistema dueno
escribe puede trabar a los dos.

**Lo que NO hace: interpretar.** Devuelve el numero que la consulta arrojo,
con la etiqueta que el operador escribio. Si la consulta esta mal planteada,
el hecho saldra mal y con cara de verdadero. Por eso las consultas viven en
un archivo que el dueno de los datos revisa, y no aqui.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Ninguna consulta puede tardar mas que esto. Una base de decenas de GB puede
# tener consultas que no terminan nunca, y un documental no vale colgar el bot.
TIMEOUT_S = 30

# Se rechaza cualquier cosa que no sea una lectura. `mode=ro` ya lo impide a
# nivel del motor; esto es la segunda cerradura, y ademas hace evidente en el
# codigo cual es la intencion.
_PROHIBIDO = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|"
    r"pragma|vacuum|reindex)\b", re.IGNORECASE)


@dataclass
class Consulta:
    """Una pregunta a los datos, con el molde de la frase que produce.

    `plantilla` usa los nombres de las columnas: "Se presentaron {presentadas}
    recetas". Asi el hecho lo redacta quien conoce los datos, no el modelo.
    """

    nombre: str
    sql: str
    plantilla: str
    fuente: str = "base propia"


@dataclass
class HechoLocal:
    texto: str
    fuente: str
    consulta: str

    def cita(self) -> str:
        return self.fuente

    def como_dict(self) -> dict:
        return {"h": self.texto, "institucion": self.fuente,
                "url": "", "fecha": ""}


@dataclass
class Lectura:
    hechos: list[HechoLocal] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


class BaseNoDisponible(RuntimeError):
    """No se pudo leer. Se dice; no se inventa el dato."""


class ConsultaInsegura(ValueError):
    """La consulta intenta escribir. Se rechaza antes de tocar la base."""


def ruta_de_lectura(ruta: str) -> str:
    """Prefiere la replica de solo lectura si existe al lado.

    Un archivo `X.read.db` junto a `X.db` es la copia que el sistema dueno
    mantiene para que otros lean sin estorbar. Usarla no es una optimizacion:
    es no meterse en el camino de quien escribe.
    """
    base, ext = os.path.splitext(ruta)
    replica = "%s.read%s" % (base, ext)
    if os.path.isfile(replica):
        logger.info("fuente_local: se usa la replica de lectura %s",
                    os.path.basename(replica))
        return replica
    return ruta


def _abrir(ruta: str) -> sqlite3.Connection:
    if not os.path.isfile(ruta):
        raise BaseNoDisponible("no existe la base: %s" % ruta)
    uri = "file:%s?mode=ro" % ruta.replace("\\", "/")
    try:
        con = sqlite3.connect(uri, uri=True, timeout=TIMEOUT_S)
    except sqlite3.Error as e:
        raise BaseNoDisponible("no se pudo abrir en solo lectura: %s" % e) from e
    con.row_factory = sqlite3.Row
    return con


def _revisar(sql: str) -> None:
    if _PROHIBIDO.search(sql or ""):
        raise ConsultaInsegura(
            "la consulta contiene una instruccion de escritura; aqui solo se "
            "lee: %s" % (sql or "")[:80])
    if ";" in (sql or "").strip().rstrip(";"):
        raise ConsultaInsegura("una consulta por vez, sin punto y coma interno")


def _formatear(valor) -> str:
    """Los numeros grandes se leen con separadores; los demas, tal cual."""
    if isinstance(valor, float):
        return "{:,.1f}".format(valor) if abs(valor) < 1000 \
            else "{:,.0f}".format(valor)
    if isinstance(valor, int):
        return "{:,}".format(valor)
    return "" if valor is None else str(valor)


def leer(ruta: str, consultas: list[Consulta]) -> Lectura:
    """Corre las consultas y arma un hecho por cada fila que devuelvan."""
    lec = Lectura()
    con = _abrir(ruta_de_lectura(ruta))
    try:
        for c in consultas:
            try:
                _revisar(c.sql)
            except ConsultaInsegura as e:
                lec.avisos.append("%s: %s" % (c.nombre, e))
                continue
            try:
                filas = con.execute(c.sql).fetchall()
            except sqlite3.Error as e:
                lec.avisos.append("%s fallo: %s" % (c.nombre, e))
                continue
            if not filas:
                lec.avisos.append("%s no devolvio filas" % c.nombre)
                continue
            for fila in filas:
                datos = {k: _formatear(fila[k]) for k in fila.keys()}
                try:
                    texto = c.plantilla.format(**datos)
                except (KeyError, IndexError) as e:
                    lec.avisos.append(
                        "%s: la plantilla pide %s y la consulta no lo devuelve"
                        % (c.nombre, e))
                    break
                lec.hechos.append(HechoLocal(texto.strip(), c.fuente, c.nombre))
            logger.info("fuente_local: %s -> %d filas", c.nombre, len(filas))
    finally:
        con.close()
    return lec


def cubre_el_tema(archivo: str, tema: str) -> bool:
    """Dice si esta base tiene algo que aportar sobre el tema pedido.

    La lista de temas vive en el JSON y no aqui: quien conoce los datos sabe
    de que hablan, y el codigo no tiene por que adivinarlo. Sin lista
    declarada se responde que no — es preferible a colar cifras de abasto en
    un documental de astronomia.
    """
    try:
        with open(archivo, encoding="utf-8") as f:
            temas = json.load(f).get("temas") or []
    except (OSError, ValueError) as e:
        logger.warning("fuente_local: no se pudo leer %s (%s)", archivo, e)
        return False
    if not temas:
        return False
    palabras = set(re.findall(r"[a-záéíóúñü]+", (tema or "").lower()))
    return any(t.lower() in palabras for t in temas)


def cargar_consultas(archivo: str) -> tuple[str, list[Consulta]]:
    """Lee el archivo de consultas. Devuelve (ruta_de_la_base, consultas).

    El formato es JSON y vive fuera del codigo a proposito: quien conoce los
    datos decide que se pregunta y como se redacta el hecho, sin tocar Python.
    """
    with open(archivo, encoding="utf-8") as f:
        cfg = json.load(f)
    ruta = cfg.get("base")
    if not ruta:
        raise BaseNoDisponible("el archivo de consultas no declara 'base'")
    consultas = []
    for c in cfg.get("consultas") or []:
        if not (c.get("sql") and c.get("plantilla")):
            continue
        consultas.append(Consulta(
            nombre=c.get("nombre") or "sin nombre",
            sql=c["sql"],
            plantilla=c["plantilla"],
            fuente=c.get("fuente") or cfg.get("fuente") or "base propia",
        ))
    if not consultas:
        raise BaseNoDisponible("el archivo no trae ninguna consulta valida")
    return ruta, consultas


def hechos_desde(archivo: str) -> Lectura:
    """De un archivo de consultas a una lista de hechos. Entrada de alto nivel."""
    ruta, consultas = cargar_consultas(archivo)
    return leer(ruta, consultas)
