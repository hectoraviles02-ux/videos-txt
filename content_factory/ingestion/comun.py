# -*- coding: utf-8 -*-
"""El esqueleto compartido de `recolectar`: red -> crudo -> parseo -> resultado.

Cuatro de las seis fuentes (Trends, Hacker News, RSS y, por repo, GitHub) hacen
exactamente la misma danza. Escribirla cuatro veces no es duplicacion inocente:
es cuatro oportunidades de que una de ellas olvide capturar una excepcion y
derribe la corrida nocturna (RQ-09).

**La regla que este archivo hace cumplir**: `recolectar` NUNCA levanta. Toda
falla -red, HTTP != 200, XML roto, JSON roto- sale como
`(senales_parciales, ResultadoFuente)` con el resultado que corresponde, y la
corrida sigue con la siguiente fuente.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable

from content_factory import raw
from content_factory.config import FuenteConfig
from content_factory.http import Respuesta
from content_factory.ingestion.base import ResultadoFuente, Senal


def resultado_http(fuente: str, resp: Respuesta) -> ResultadoFuente:
    """Traduce un status != 200 a la taxonomia auditable. 429 y 403 son distintos.

    Fundirlos en un generico `error_red` costaria el diagnostico: 429 es
    "esperaste poco", 403 es "te quedaste sin cuota", y la accion correctiva del
    operador no es la misma.
    """
    if resp.status == 0:
        return ResultadoFuente(fuente, "error_red", 0, resp.error or "excepcion de red")
    if resp.status == 429:
        return ResultadoFuente(fuente, "http_429", 0, "status=429")
    if resp.status == 403:
        return ResultadoFuente(fuente, "http_403", 0, "status=403")
    return ResultadoFuente(fuente, "error_red", 0, "status=%s" % resp.status)


def parsear_o_resultado(
    cuerpo: str,
    cfg: FuenteConfig,
    fecha_corte: date,
    parsear: Callable[[str, FuenteConfig, date], list[Senal]],
    raw_path: str | None,
    resultado_ok: str = "ok",
) -> tuple[list[Senal], ResultadoFuente]:
    """Parsea y clasifica el desenlace: `ok`, `vacia` o `parse_error`.

    Un crudo malformado es `parse_error` y no `error_red`: la red funciono
    perfectamente, lo que vino roto fue el documento. Confundirlos manda al
    operador a revisar su conexion cuando el problema esta del otro lado (H-2).
    """
    try:
        senales = parsear(cuerpo, cfg, fecha_corte)
    except Exception as e:  # ParseError, JSONDecodeError, KeyError de un esquema movido
        return [], ResultadoFuente(
            cfg.nombre, "parse_error", 0, "%s: %s" % (type(e).__name__, e), raw_path
        )
    if not senales:
        return [], ResultadoFuente(cfg.nombre, "vacia", 0, "0 items en el crudo", raw_path)
    return senales, ResultadoFuente(cfg.nombre, resultado_ok, len(senales), "", raw_path)


def recolectar_simple(
    cfg: FuenteConfig,
    fecha_corte: date,
    *,
    url: str,
    ext: str,
    get: Callable[..., Respuesta],
    parsear: Callable[[str, FuenteConfig, date], list[Senal]],
    desde_raw: bool = False,
    base_raw: Path | None = None,
    nombre_archivo: str | None = None,
) -> tuple[list[Senal], ResultadoFuente]:
    """Una URL, un crudo, un parseo. El caso comun de cuatro de las seis fuentes.

    `desde_raw=True` o crudo del dia ya presente => `desde_raw`, sin tocar la red.
    Ese camino es el que hace que reejecutar el dia sea gratis y que el `replay`
    pueda correr sin conexion (cierre 5 de RQ-37).
    """
    archivo = nombre_archivo or cfg.nombre

    if raw.existe_crudo(fecha_corte, archivo, ext, base_raw):
        raw_path = raw.relativo(raw.ruta_crudo(fecha_corte, archivo, ext, base_raw), base_raw)
        cuerpo = raw.leer_crudo(raw_path, base_raw)
        return parsear_o_resultado(cuerpo, cfg, fecha_corte, parsear, raw_path, "desde_raw")

    if desde_raw:
        # El replay exige el crudo. Que no este es un hecho que se declara, no
        # una excusa para salir a la red "solo esta vez".
        return [], ResultadoFuente(
            cfg.nombre, "desactivada", 0, "sin crudo para %s y --desde-raw activo" % fecha_corte
        )

    resp = get(url)
    if resp.status != 200 or resp.texto is None:
        return [], resultado_http(cfg.nombre, resp)

    raw_path = raw.guardar_crudo(fecha_corte, archivo, ext, resp.texto, base_raw)
    return parsear_o_resultado(resp.texto, cfg, fecha_corte, parsear, raw_path)
