# -*- coding: utf-8 -*-
"""Reddit por RSS/Atom publico, con el limite medido de ~1 peticion por minuto.

El limite NO es una suposicion: el 2026-09-14 se midio contra la API real
`x-ratelimit-remaining: 0.0` y `x-ratelimit-reset: 55` tras una sola peticion,
y un segundo intento dentro de la ventana devolvio 429.

Consecuencias que este modulo asume y declara:

- **La espera va DESPUES de la peticion, no antes.** Asi la pausa la paga quien
  la provoco, y una corrida con una sola fuente de Reddit no duerme de balde.
  Con 11 comunidades el presupuesto es ~11 min por corrida (design section 5.1),
  que es restriccion operacional declarada y no detalle de implementacion.
- **Un 429 NO se reintenta dentro de la ventana.** Se anota `http_429` con el
  `reset` en el detalle y se pasa a la siguiente comunidad. Insistir solo alarga
  el bloqueo.
- **Si el crudo del dia ya existe, no se pide ni se duerme.** Reejecutar el dia
  es gratis, que es lo que hace posible el replay sin red.
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET

from content_factory import raw
from content_factory.config import FuenteConfig
from content_factory.http import Respuesta, entero_de_cabecera, solicitar
from content_factory.ingestion.base import EtagStore, ResultadoFuente, Senal, construir
from content_factory.ingestion.comun import parsear_o_resultado, resultado_http

EXT = "xml"

#: Segundos a esperar cuando la respuesta no trae `x-ratelimit-reset`. El valor
#: medido fue 55; se usa 60 por margen.
ESPERA_POR_DEFECTO = 60


def url_de(cfg: FuenteConfig) -> str:
    """`t=month` y no `t=week`: da 25 entradas con fecha, y sirve para rellenar
    los 30 dias de la prueba de determinismo sin esperar 30 noches."""
    sub = cfg.parametros["sub"]
    ventana = cfg.parametros.get("ventana", "month")
    return "https://www.reddit.com/r/%s/top/.rss?t=%s" % (sub, ventana)


def _get(url: str, *, etag: str | None = None) -> Respuesta:
    """Punto unico de red del modulo. Las pruebas lo sustituyen (RQ-08)."""
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
    """Atom de Reddit: `entry` con `title`, `link href`, `updated` y `author`.

    Se navega por nombre local de etiqueta y no por URI de espacio de nombres:
    fijar el URI de memoria haria que la prueba pasara contra la fixture y
    devolviera cero contra el feed real.
    """
    arbol = ET.fromstring(cuerpo)
    senales: list[Senal] = []
    for entrada in arbol.iter():
        if _local(entrada) != "entry":
            continue
        titulo = _texto(entrada, "title")
        if not titulo:
            continue  # RQ-41: sin texto no hay senal
        enlace = _hijo(entrada, "link")
        url = enlace.get("href") if enlace is not None else None
        autor = _hijo(entrada, "author")
        senales.append(
            construir(
                cfg.nombre,
                titulo,
                url=url,
                publicada_en=_texto(entrada, "updated") or None,
                fecha_corte=fecha_corte,
                extra={
                    "sub": cfg.parametros.get("sub", ""),
                    "autor": _texto(autor, "name") if autor is not None else "",
                    "id": _texto(entrada, "id"),
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
    """Una comunidad por llamada. La espera la paga esta llamada, no la siguiente."""
    archivo = cfg.nombre

    if raw.existe_crudo(fecha_corte, archivo, EXT, base_raw):
        ruta = raw.relativo(raw.ruta_crudo(fecha_corte, archivo, EXT, base_raw), base_raw)
        cuerpo = raw.leer_crudo(ruta, base_raw)
        return parsear_o_resultado(cuerpo, cfg, fecha_corte, parsear, ruta, "desde_raw")

    if desde_raw:
        return [], ResultadoFuente(
            cfg.nombre, "desactivada", 0, "sin crudo para %s y --desde-raw activo" % fecha_corte
        )

    resp = _get(url_de(cfg))

    # La pausa se paga SIEMPRE que hubo peticion, haya salido bien o mal: el
    # servidor cuenta el intento, no el exito.
    espera = entero_de_cabecera(resp, "x-ratelimit-reset", ESPERA_POR_DEFECTO)
    dormir(max(1, espera))

    if resp.status == 429:
        return [], ResultadoFuente(cfg.nombre, "http_429", 0, "reset=%s" % espera)
    if resp.status != 200 or resp.texto is None:
        return [], resultado_http(cfg.nombre, resp)

    ruta = raw.guardar_crudo(fecha_corte, archivo, EXT, resp.texto, base_raw)
    return parsear_o_resultado(resp.texto, cfg, fecha_corte, parsear, ruta)
