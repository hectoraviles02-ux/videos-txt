# -*- coding: utf-8 -*-
"""Sitios por RSS 2.0 y Atom, con `xml.etree` de la biblioteca estandar.

`feedparser` NO se instala (RQ-53): agregar una dependencia para leer dos
formatos XML bien documentados no se justifica, y el repositorio es stdlib
primero.

Los dos formatos se atienden en el mismo parser porque difieren en tres nombres
de etiqueta y nada mas: `item` contra `entry`, `link` como texto contra `link`
con atributo `href`, y `pubDate` contra `updated`. Escribir dos parsers seria
duplicar la misma logica con dos oportunidades de que una se quede atras.
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


def _get(url: str, *, etag: str | None = None) -> Respuesta:
    return solicitar(url, etag=etag)


def _local(elemento: ET.Element) -> str:
    return elemento.tag.rsplit("}", 1)[-1]


def _hijo(elemento: ET.Element, *nombres: str) -> ET.Element | None:
    """Primer hijo cuyo nombre local este entre `nombres`. RSS y Atom a la vez."""
    for hijo in elemento:
        if _local(hijo) in nombres:
            return hijo
    return None


def _texto(elemento: ET.Element, *nombres: str) -> str:
    hijo = _hijo(elemento, *nombres)
    return (hijo.text or "").strip() if hijo is not None else ""


def _enlace(elemento: ET.Element) -> str | None:
    """RSS pone la URL como texto de `<link>`; Atom, en su atributo `href`."""
    hijo = _hijo(elemento, "link")
    if hijo is None:
        return None
    href = hijo.get("href")
    if href:
        return href.strip()
    return (hijo.text or "").strip() or None


def parsear(cuerpo: str, cfg: FuenteConfig, fecha_corte: date) -> list[Senal]:
    arbol = ET.fromstring(cuerpo)
    senales: list[Senal] = []
    for entrada in arbol.iter():
        if _local(entrada) not in ("item", "entry"):
            continue
        titulo = _texto(entrada, "title")
        if not titulo:
            continue  # RQ-41
        senales.append(
            construir(
                cfg.nombre,
                titulo,
                url=_enlace(entrada),
                publicada_en=_texto(entrada, "pubDate", "updated", "published") or None,
                fecha_corte=fecha_corte,
                extra={
                    "descripcion": _texto(entrada, "description", "summary")[:500],
                    "guid": _texto(entrada, "guid", "id"),
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
    from content_factory.ingestion.comun import recolectar_simple

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
