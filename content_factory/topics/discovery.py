# -*- coding: utf-8 -*-
"""Senales -> temas: deduplicacion y consolidacion.

Dos senales distintas pueden ser el mismo tema: el lanzamiento de Ollama visto
por Hacker News, por el repositorio y por un RSS son tres senales y un tema. La
llave que las une es `hash_titulo`, sha256 completo del titulo normalizado.

**Por que el titulo y no la URL.** La URL ya deduplica a nivel de senal, en
`hash_senal`. Aqui se agrupa un escalon mas arriba: tres URL distintas que
hablan de lo mismo deben dar un solo tema, o el TOP 10 se llena de repeticiones
del mismo hecho contado por tres fuentes.

**Orden estable, siempre.** Las senales se recorren por `hash ASC` y los temas
salen ordenados por `hash_titulo ASC`. El orden en que respondieron las fuentes
NO puede influir en el resultado, que es el cierre 5 de RQ-37 y la base de la
prueba de determinismo.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date

from content_factory.ingestion.base import Senal, normalizar_titulo


@dataclass
class Tema:
    """Un tema del dia, con las senales que lo sostienen."""

    hash_titulo: str
    titulo: str
    creado_en: str
    senales: list[Senal] = field(default_factory=list)

    @property
    def fuentes(self) -> tuple[str, ...]:
        """Fuentes distintas que aportaron, en orden estable."""
        return tuple(sorted({s.fuente for s in self.senales}))

    @property
    def urls(self) -> tuple[str, ...]:
        """URLs distintas del tema, en orden estable y sin vacias."""
        return tuple(sorted({s.url for s in self.senales if s.url}))

    @property
    def n_senales(self) -> int:
        return len(self.senales)

    def como_dict(self) -> dict:
        return {
            "hash_titulo": self.hash_titulo,
            "titulo": self.titulo,
            "creado_en": self.creado_en,
            "n_senales": self.n_senales,
            "fuentes": list(self.fuentes),
            "urls": list(self.urls),
        }


def hash_titulo(titulo: str) -> str:
    """sha256 completo del titulo normalizado. Nunca recortado (RQ-14).

    La normalizacion vive en `ingestion.base` y se comparte con el hash de las
    senales sin URL: dos normalizaciones distintas para lo mismo acabarian
    divergiendo.
    """
    return hashlib.sha256(normalizar_titulo(titulo).encode("utf-8")).hexdigest()


def deduplicar(senales: list[Senal]) -> list[Senal]:
    """Quita senales repetidas por `hash` y devuelve en orden `hash ASC`.

    La misma nota vista dos veces, o el mismo enlace con distinto `utm_`, ya
    colapsan en `hash_senal` porque la preimagen usa `normalizar_url`. Aqui solo
    se retira la repeticion y se fija el orden.
    """
    unicas: dict[str, Senal] = {}
    for senal in senales:
        if senal.hash not in unicas:
            unicas[senal.hash] = senal
    return [unicas[h] for h in sorted(unicas)]


def consolidar(senales: list[Senal], fecha_corte: date | str) -> list[Tema]:
    """Agrupa senales en temas por titulo normalizado.

    El titulo que se conserva es el de la senal de menor `hash`, no el primero
    que llego: el orden de llegada depende de que fuente respondio antes, y eso
    haria que el mismo dia produjera titulos distintos en dos corridas.
    """
    limpias = deduplicar(senales)
    temas: dict[str, Tema] = {}
    for senal in limpias:
        clave = hash_titulo(senal.texto)
        if clave not in temas:
            temas[clave] = Tema(
                hash_titulo=clave,
                titulo=senal.texto,
                creado_en=str(fecha_corte),
                senales=[],
            )
        temas[clave].senales.append(senal)
    for tema in temas.values():
        tema.senales.sort(key=lambda s: s.hash)
    return [temas[k] for k in sorted(temas)]


def hash_entradas(senales: list[Senal]) -> str:
    """Huella de TODAS las senales de la corrida, para comparar dos replays.

    Si dos corridas tienen el mismo `hash_entradas` y distinto TOP 10, hay un
    defecto de determinismo y no una diferencia de datos. Es la primera pregunta
    que hay que poder responder cuando la prueba de replay falla.
    """
    digesto = hashlib.sha256()
    for h in sorted({s.hash for s in senales}):
        digesto.update(h.encode("utf-8"))
    return digesto.hexdigest()
