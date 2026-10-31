# -*- coding: utf-8 -*-
"""Lanzamientos de GitHub, con la cuota de 60 peticiones por hora como restriccion.

El limite se midio el 2026-09-14 leyendo la cabecera `X-RateLimit-Limit: 60` sin
credencial. De ahi salen las dos reglas de este modulo:

- **`ETag` persistido en `cache_http`.** Un `If-None-Match` que devuelve 304 NO
  consume cuota. Guardar el ETag en memoria no ahorraria nada, porque cada
  corrida nocturna es un proceso nuevo; por eso vive en la base (decision D-09).
- **Corte por cuota baja, no espera.** Si quedan menos de `MINIMO_CUOTA`
  peticiones, los repositorios restantes salen como `omitida_por_cuota` y la
  corrida sigue. Esperar a que se reponga la cuota bloquearia la ventana
  nocturna una hora entera por una fuente secundaria.

`omitida_por_cuota` es categoria **no consultada**, no fallida: no se pregunto,
y eso no vuelve incompleta la corrida. Confundirlas haria que doce repositorios
sin novedades marcaran doce fallas inexistentes.
"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Callable

from content_factory import raw
from content_factory.config import FuenteConfig
from content_factory.http import Respuesta, entero_de_cabecera, solicitar
from content_factory.ingestion.base import EtagStore, ResultadoFuente, Senal, construir
from content_factory.ingestion.comun import parsear_o_resultado, resultado_http

EXT = "json"

#: Por debajo de esto se corta la fuente. No es cero para dejar margen a que
#: otra parte del sistema necesite una peticion.
MINIMO_CUOTA = 5


def url_de(cfg: FuenteConfig) -> str:
    return "https://api.github.com/repos/%s/releases?per_page=5" % cfg.parametros["repo"]


def _get(url: str, *, etag: str | None = None) -> Respuesta:
    return solicitar(url, etag=etag)


def parsear(cuerpo: str, cfg: FuenteConfig, fecha_corte: date) -> list[Senal]:
    """Lista de lanzamientos. Los borradores se descartan: no son publicos aun.

    El texto de la senal es `repo tag: nombre`, no solo el tag: `v0.14.0` sin el
    repositorio no dice nada, y el clasificador trabaja sobre texto.
    """
    datos = json.loads(cuerpo)
    repo = cfg.parametros.get("repo", "")
    senales: list[Senal] = []
    for suelta in datos:
        if suelta.get("draft"):
            continue
        etiqueta = (suelta.get("tag_name") or "").strip()
        nombre = (suelta.get("name") or "").strip()
        if not etiqueta and not nombre:
            continue
        titulo = "%s %s" % (repo, etiqueta or nombre)
        if nombre and nombre != etiqueta:
            titulo = "%s: %s" % (titulo, nombre)
        senales.append(
            construir(
                cfg.nombre,
                titulo,
                url=suelta.get("html_url") or None,
                publicada_en=suelta.get("published_at") or suelta.get("created_at") or None,
                fecha_corte=fecha_corte,
                extra={
                    "repo": repo,
                    "tag": etiqueta,
                    "prerelease": bool(suelta.get("prerelease")),
                    "cuerpo": (suelta.get("body") or "")[:500],
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
    archivo = cfg.nombre
    url = url_de(cfg)

    if raw.existe_crudo(fecha_corte, archivo, EXT, base_raw):
        ruta = raw.relativo(raw.ruta_crudo(fecha_corte, archivo, EXT, base_raw), base_raw)
        cuerpo = raw.leer_crudo(ruta, base_raw)
        return parsear_o_resultado(cuerpo, cfg, fecha_corte, parsear, ruta, "desde_raw")

    if desde_raw:
        return [], ResultadoFuente(
            cfg.nombre, "desactivada", 0, "sin crudo para %s y --desde-raw activo" % fecha_corte
        )

    # Cuota leida de la respuesta ANTERIOR de esta misma corrida. Si ya va baja,
    # ni se pregunta: la corrida sigue y el informe lo declara.
    if etags is not None and etags.remaining is not None and etags.remaining < MINIMO_CUOTA:
        return [], ResultadoFuente(
            cfg.nombre, "omitida_por_cuota", 0,
            "X-RateLimit-Remaining=%s < %d" % (etags.remaining, MINIMO_CUOTA),
        )

    cacheado = etags.leer(url) if etags is not None else None
    resp = _get(url, etag=cacheado[0] if cacheado else None)

    if etags is not None:
        restante = entero_de_cabecera(resp, "x-ratelimit-remaining", -1)
        if restante >= 0:
            etags.remaining = restante

    # 304: el crudo guardado sigue vigente y no gasto cuota. Es resultado CON
    # senales, no una falla.
    if resp.status == 304 and cacheado is not None:
        cuerpo = raw.leer_crudo(cacheado[1], base_raw)
        return parsear_o_resultado(cuerpo, cfg, fecha_corte, parsear, cacheado[1], "http_304")

    if resp.status != 200 or resp.texto is None:
        return [], resultado_http(cfg.nombre, resp)

    ruta = raw.guardar_crudo(fecha_corte, archivo, EXT, resp.texto, base_raw)
    if etags is not None:
        etiqueta = resp.cabeceras.get("etag", "")
        if etiqueta:
            etags.guardar(url, etiqueta, ruta, str(fecha_corte))
    return parsear_o_resultado(resp.texto, cfg, fecha_corte, parsear, ruta)
