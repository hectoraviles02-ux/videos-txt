# -*- coding: utf-8 -*-
"""Hacker News por la API de Algolia, la unica de las seis con historico real.

Por eso importa: `search_by_date` acepta filtros `created_at_i` por rango, asi
que los 30 dias de la prueba de determinismo se pueden rellenar hacia atras el
primer dia, en vez de esperar 30 noches. Trends no tiene pasado y Reddit solo
llega a un mes; esta es la fuente que hace viable el nivel B de aceptacion.

Es acervo compartido: no trae `pilar_origen`, asi que sus senales se clasifican
solo por lexico. Esa debilidad esta declarada, no disimulada.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from content_factory.config import FuenteConfig
from content_factory.http import Respuesta, solicitar
from content_factory.ingestion.base import EtagStore, ResultadoFuente, Senal, construir

EXT = "json"

#: Dias hacia atras que pide una corrida normal. Un dia de margen sobre el dia
#: de corte para no perder lo publicado de madrugada.
VENTANA_DIAS = 2


def _epoch(dia: date) -> int:
    return int(datetime(dia.year, dia.month, dia.day, tzinfo=timezone.utc).timestamp())


def url_de(cfg: FuenteConfig, fecha_corte: date, dias: int = VENTANA_DIAS) -> str:
    """URL con el rango de fechas EXPLICITO, derivado de `fecha_corte`.

    Que el rango salga de la fecha de corte y no del reloj es lo que permite
    rellenar dias pasados y lo que hace que un replay pida exactamente la misma
    ventana (cierre 1 de RQ-37).
    """
    base = cfg.parametros.get("url", "https://hn.algolia.com/api/v1/search_by_date")
    tags = cfg.parametros.get("tags", "story")
    hits = cfg.parametros.get("hits", "50")
    hasta = _epoch(fecha_corte + timedelta(days=1))
    desde = _epoch(fecha_corte - timedelta(days=dias - 1))
    return (
        "%s?tags=%s&hitsPerPage=%s&numericFilters=created_at_i>=%d,created_at_i<%d"
        % (base, tags, hits, desde, hasta)
    )


def _get(url: str, *, etag: str | None = None) -> Respuesta:
    return solicitar(url, etag=etag)


def parsear(cuerpo: str, cfg: FuenteConfig, fecha_corte: date) -> list[Senal]:
    """`hits` con `title`, `url`, `points`, `num_comments` y `created_at`.

    Una entrada de tipo Ask HN no trae `url`; se conserva igual, con `url=None`,
    y el hash cae en la rama por titulo. Descartarlas perderia justamente las
    preguntas, que suelen ser buen material de tema.
    """
    datos = json.loads(cuerpo)
    senales: list[Senal] = []
    for hit in datos.get("hits", []):
        titulo = (hit.get("title") or "").strip()
        if not titulo:
            continue
        senales.append(
            construir(
                cfg.nombre,
                titulo,
                url=hit.get("url") or None,
                publicada_en=hit.get("created_at") or None,
                fecha_corte=fecha_corte,
                extra={
                    "puntos": int(hit.get("points") or 0),
                    "comentarios": int(hit.get("num_comments") or 0),
                    "autor": hit.get("author") or "",
                    "object_id": hit.get("objectID") or "",
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
    dias: int = VENTANA_DIAS,
) -> tuple[list[Senal], ResultadoFuente]:
    from content_factory.ingestion.comun import recolectar_simple

    return recolectar_simple(
        cfg,
        fecha_corte,
        url=url_de(cfg, fecha_corte, dias),
        ext=EXT,
        get=_get,
        parsear=parsear,
        desde_raw=desde_raw,
        base_raw=base_raw,
    )
