# -*- coding: utf-8 -*-
"""YouTube por busqueda, con yt-dlp como modulo y sin fecha fiable.

Dos hechos medidos el 2026-09-14 que corrigen lo que el plan daba por supuesto:

- **`ytsearchdate` NO existe.** El plan lo asumia. El filtro por fecha se hace
  del lado de la URL, con el parametro `sp` de YouTube ("este mes"), y se
  declara en `config/fuentes.yaml`.
- **`upload_date` viene `NA` en modo plano.** Obtener la fecha real cuesta una
  peticion por video, 5 a 10 segundos cada una, y no vale ese precio en una
  corrida nocturna. Por eso `publicada_en = None` y la ventana temporal la da
  `detectada_en`; el hueco viaja en `extra` y llega al informe, no se disimula.

yt-dlp se importa **dentro** de la funcion: cargarlo al importar el paquete
costaria segundos en cada corrida de pytest (RQ-24).
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import Callable

from content_factory.config import FuenteConfig
from content_factory.ingestion.base import EtagStore, ResultadoFuente, Senal, construir

EXT = "txt"

#: Campo por campo, separados por tabulador: id, titulo, fecha, canal, vistas.
#: Formato plano y estable; un JSON completo por video pesaria cien veces mas
#: para la misma informacion util.
PLANTILLA = "%(id)s\t%(title)s\t%(upload_date)s\t%(channel)s\t%(view_count)s"

#: Aviso que viaja al informe: esta fuente no aporta fecha de publicacion.
AVISO_SIN_FECHA = "youtube en modo plano no devuelve upload_date; se usa detectada_en"


def url_de(cfg: FuenteConfig) -> str:
    """URL de busqueda con el filtro `sp` declarado en la configuracion."""
    consulta = cfg.parametros["consulta"]
    sp = cfg.parametros.get("sp", "")
    from urllib.parse import quote_plus

    base = "https://www.youtube.com/results?search_query=%s" % quote_plus(consulta)
    return "%s&sp=%s" % (base, sp) if sp else base


def _buscar(cfg: FuenteConfig) -> tuple[str | None, str | None]:
    """Devuelve `(texto_tabulado, error)`. Punto unico de salida al exterior.

    No usa `http.solicitar` porque yt-dlp no es HTTP plano: negocia formatos y
    descifra la pagina. Las pruebas sustituyen ESTA funcion, igual que sustituyen
    `solicitar` en las demas fuentes.
    """
    import subprocess
    import sys

    limite = cfg.parametros.get("limite", "10")
    orden = ["%s" % sys.executable, "-m", "yt_dlp", "--flat-playlist", "--no-warnings",
             "--playlist-end", str(limite), "--print", PLANTILLA, url_de(cfg)]
    try:
        r = subprocess.run(orden, capture_output=True, text=True, timeout=180)
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)
    if r.returncode != 0:
        return None, "yt-dlp salio con %s: %s" % (r.returncode, (r.stderr or "")[:200])
    return r.stdout, None


def parsear(cuerpo: str, cfg: FuenteConfig, fecha_corte: date) -> list[Senal]:
    """Texto tabulado -> senales. Una linea corta se salta, no rompe la corrida."""
    senales: list[Senal] = []
    for linea in cuerpo.splitlines():
        if not linea.strip():
            continue
        partes = linea.split("\t")
        if len(partes) < 2:
            continue
        video, titulo = partes[0].strip(), partes[1].strip()
        if not titulo:
            continue  # RQ-41
        vistas = partes[4].strip() if len(partes) > 4 else ""
        senales.append(
            construir(
                cfg.nombre,
                titulo,
                url="https://www.youtube.com/watch?v=%s" % video if video else None,
                publicada_en=None,  # 'NA' en modo plano; se declara, no se inventa
                fecha_corte=fecha_corte,
                extra={
                    "canal": partes[3].strip() if len(partes) > 3 else "",
                    "vistas": int(vistas) if vistas.isdigit() else 0,
                    "consulta": cfg.parametros.get("consulta", ""),
                    "aviso": AVISO_SIN_FECHA,
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
    from content_factory import raw
    from content_factory.ingestion.comun import parsear_o_resultado

    archivo = cfg.nombre

    if raw.existe_crudo(fecha_corte, archivo, EXT, base_raw):
        ruta = raw.relativo(raw.ruta_crudo(fecha_corte, archivo, EXT, base_raw), base_raw)
        cuerpo = raw.leer_crudo(ruta, base_raw)
        return parsear_o_resultado(cuerpo, cfg, fecha_corte, parsear, ruta, "desde_raw")

    if desde_raw:
        return [], ResultadoFuente(
            cfg.nombre, "desactivada", 0, "sin crudo para %s y --desde-raw activo" % fecha_corte
        )

    cuerpo, error = _buscar(cfg)
    if error is not None:
        return [], ResultadoFuente(cfg.nombre, "error_red", 0, error)

    ruta = raw.guardar_crudo(fecha_corte, archivo, EXT, cuerpo or "", base_raw)
    return parsear_o_resultado(cuerpo or "", cfg, fecha_corte, parsear, ruta)
