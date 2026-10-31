# -*- coding: utf-8 -*-
"""Ingestion: seis fuentes, un contrato.

Reexporta el esqueleto compartido para que cada modulo de fuente escriba
`from content_factory.ingestion import recolectar_simple` en vez de alcanzar
`ingestion.comun` por dentro. Sin imports pesados: `requests` y `yt_dlp` se
cargan perezosamente donde se usan.
"""

from __future__ import annotations

from content_factory.ingestion.base import (
    CATEGORIA_CON_RESULTADOS,
    CATEGORIA_FALLIDA,
    CATEGORIA_NO_CONSULTADA,
    CATEGORIA_SIN_RESULTADOS,
    EtagStore,
    ResultadoFuente,
    Senal,
    SenalInvalida,
    categoria_auditable,
    construir,
    estado_corrida,
    hash_senal,
    normalizar_titulo,
)
from content_factory.ingestion.comun import (
    parsear_o_resultado,
    recolectar_simple,
    resultado_http,
)

__all__ = [
    "CATEGORIA_CON_RESULTADOS", "CATEGORIA_FALLIDA", "CATEGORIA_NO_CONSULTADA",
    "CATEGORIA_SIN_RESULTADOS", "EtagStore", "ResultadoFuente", "Senal", "SenalInvalida",
    "categoria_auditable", "construir", "estado_corrida", "hash_senal", "normalizar_titulo",
    "parsear_o_resultado", "recolectar_simple", "resultado_http", "recolectar",
]

#: Mapa tipo de fuente -> modulo que la lee. Agregar una fuente de un tipo nuevo
#: es agregar una fila aqui y un modulo; no se toca ningun otro archivo (RQ-02).
MODULOS = {
    "trends_mx": "content_factory.ingestion.trends_mx",
    "reddit": "content_factory.ingestion.reddit",
    "hackernews": "content_factory.ingestion.hackernews",
    "github_releases": "content_factory.ingestion.github_releases",
    "rss": "content_factory.ingestion.rss",
    "youtube": "content_factory.ingestion.youtube",
}


def recolectar(cfg, fecha_corte, **kw):
    """Despacha al modulo del tipo de fuente. Nunca levanta por un tipo raro.

    Un tipo desconocido no puede llegar aqui si la configuracion se cargo con
    `config.cargar_fuentes`, que ya lo valida; pero si llegara, se declara como
    fuente no consultada en vez de tumbar la corrida (RQ-09).
    """
    import importlib

    ruta = MODULOS.get(cfg.tipo)
    if ruta is None:
        return [], ResultadoFuente(
            cfg.nombre, "desactivada", 0, "tipo de fuente sin modulo: %s" % cfg.tipo
        )
    return importlib.import_module(ruta).recolectar(cfg, fecha_corte, **kw)
