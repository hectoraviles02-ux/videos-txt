# -*- coding: utf-8 -*-
"""Adaptador: conecta el publicador REAL de Facebook a `distribucion.publicar_pieza`.

Cambio SDD: content-factory-produccion-y-distribucion (E2, cableado del real).

El publicador real vive en la carpeta de evidencia
(`docs/operations/evidencia/publicacion_fb_20261005/publicar_reels_fb.py`), no es un
paquete; se carga por ruta con importlib. `publicar_uno(page_id, token, item)` devuelve
`(video_id, resp)` y hace la llamada REAL a la API de Reels.

Este modulo NO publica: solo CONSTRUYE el callable que `distribucion.publicar_pieza`
invocara. `publicar_uno` es inyectable para poder probar el cableado con un cliente
simulado, sin tocar la red. La prueba REAL (con token) queda pendiente por credencial.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
RUTA_PUBLICADOR = RAIZ / "docs/operations/evidencia/publicacion_fb_20261005/publicar_reels_fb.py"


def cargar_publicar_uno():
    """Carga por ruta la funcion real `publicar_uno`. No hace ninguna llamada de red."""
    spec = importlib.util.spec_from_file_location("publicar_reels_fb_adaptado", RUTA_PUBLICADOR)
    if spec is None or spec.loader is None:
        raise ImportError("no se pudo cargar el publicador en %s" % RUTA_PUBLICADOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.publicar_uno


def construir_publicador(*, mp4_path, caption, page_id, token, publicar_uno=None,
                         programada_para=None, ia=False):
    """Devuelve un `callable()` que publica ESE reel y devuelve {'video_id','url','resp'}.

    `publicar_uno` es inyectable (cliente simulado en pruebas). Por omision carga el
    real. El callable mapea la pieza a un `item` {id, file, caption} como lo espera el
    publicador existente, sin duplicar su logica.

    v5: el callable DECLARA `ruta_mp4`, `texto` y `programada_para` (ISO con zona).
    `distribucion.publicar_pieza` verifica la aprobacion de el operador contra ESOS valores,
    que son los mismos que el callable sube.
    """
    pu = publicar_uno if publicar_uno is not None else cargar_publicar_uno()
    extra = {}
    if programada_para:
        extra["programar_unix"] = datetime.fromisoformat(programada_para).timestamp()
    if ia:
        extra["ia"] = True

    def _publicar():
        item = {"id": Path(mp4_path).stem, "file": str(mp4_path), "caption": caption}
        vid, resp = pu(page_id, token, item, **extra)
        vid = str(vid) if vid is not None else ""
        return {"video_id": vid, "url": ("/reel/%s/" % vid if vid else None), "resp": resp}

    _publicar.ruta_mp4 = str(mp4_path)
    _publicar.texto = caption
    _publicar.programada_para = programada_para
    return _publicar
