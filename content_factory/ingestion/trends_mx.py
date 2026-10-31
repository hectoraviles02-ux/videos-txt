# -*- coding: utf-8 -*-
"""Google Trends MX - RSS 2.0 con espacio de nombres `ht:`.

Particularidades que este modulo asume y declara:

- **10 items por dia** y **sin historico**: el feed solo trae el dia. La serie se
  acumula desde el dia de activacion. Ese hueco se declara como aviso y llega al
  informe (RQ-13); no se disimula rellenando dias hacia atras.
- **Sin URL propia**: el `<link>` de cada item apunta al buscador de tendencias,
  no al tema. Por eso `url = None` y el hash cae en la rama
  `fuente|titulo|fecha` de `hash_senal` (RQ-14).
- El contenido esta dominado por deportes y loterias. Eso NO se filtra aqui: la
  ingestion no juzga, clasifica el bloque 4.

El parseo va por **nombre local** de etiqueta, no por URI de espacio de nombres.
Fijar el URI de memoria haria que la prueba pasara contra una fixture propia y
devolviera cero items contra el feed real.
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET

from content_factory.config import FuenteConfig
from content_factory.http import Respuesta, solicitar
from content_factory.ingestion.base import EtagStore, ResultadoFuente, Senal, construir

EXT = "xml"

#: Aviso que viaja hasta el informe: esta fuente no tiene pasado (RQ-13).
AVISO_SIN_HISTORICO = "trends_mx sin historico previo al dia de activacion"


def _get(url: str, *, etag: str | None = None) -> Respuesta:
    """Punto unico de red del modulo (RQ-08). Las pruebas lo sustituyen."""
    return solicitar(url, etag=etag)


def _local(elemento: ET.Element) -> str:
    return elemento.tag.rsplit("}", 1)[-1]


def _hijo(elemento: ET.Element, nombre: str) -> ET.Element | None:
    for hijo in elemento:
        if _local(hijo) == nombre:
            return hijo
    return None


def _texto(elemento: ET.Element, nombre: str) -> str:
    hijo = _hijo(elemento, nombre)
    return (hijo.text or "").strip() if hijo is not None else ""


def parsear(cuerpo: str, cfg: FuenteConfig, fecha_corte: date) -> list[Senal]:
    """Pura: `str -> Senal[]`. Levanta `ET.ParseError` si el crudo no es XML."""
    arbol = ET.fromstring(cuerpo)
    senales: list[Senal] = []
    for item in arbol.iter():
        if _local(item) != "item":
            continue
        titulo = _texto(item, "title")
        if not titulo:
            continue  # RQ-41: sin texto no hay senal, y no se inventa uno
        noticias = []
        for hijo in item:
            if _local(hijo) != "news_item":
                continue
            noticias.append(
                {
                    "titulo": _texto(hijo, "news_item_title"),
                    "url": _texto(hijo, "news_item_url"),
                    "fuente": _texto(hijo, "news_item_source"),
                }
            )
        senales.append(
            construir(
                cfg.nombre,
                titulo,
                url=None,  # el <link> apunta al buscador, no al tema
                publicada_en=_texto(item, "pubDate") or None,
                fecha_corte=fecha_corte,
                extra={
                    "approx_traffic": _texto(item, "approx_traffic"),
                    "noticias": noticias,
                    "aviso": AVISO_SIN_HISTORICO,
                },
            )
        )
    return senales


def recolectar(
    cfg: FuenteConfig,
    fecha_corte: date,
    *,
    desde_raw: bool = False,
    dormir: Callable[[float], None] = time.sleep,
    base_raw: Path | None = None,
    etags: EtagStore | None = None,
) -> tuple[list[Senal], ResultadoFuente]:
    from content_factory.ingestion import recolectar_simple

    return recolectar_simple(
        cfg,
        fecha_corte,
        url=cfg.parametros["url"],
        ext=EXT,
        get=_get,
        parsear=parsear,
        desde_raw=desde_raw,
        base_raw=base_raw,
    )
