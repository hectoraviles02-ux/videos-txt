# -*- coding: utf-8 -*-
"""Compuerta para los publicadores que NO pasan por `distribucion.publicar_pieza`.

Etapa 1 (21-oct-2026): los scripts manuales, los botones viejos de Telegram y el panel de
TikTok llaman a la API de la plataforma por su cuenta. Antes de subir, cada uno llama a
`exigir`: si en `content.db` no hay una aprobacion VIGENTE de operador para ESTE archivo
(por su SHA-256), ESTE texto, ESTE canal y ESTE horario, se lanza `PublicacionBloqueada`
y no se sube nada.

No lee el reloj (regla de la fabrica): quien llama pasa `en`.
"""
from __future__ import annotations

from pathlib import Path

from content_factory import db


class PublicacionBloqueada(RuntimeError):
    """No hay aprobacion vigente de operador que cubra esta subida."""


def exigir(ruta_mp4, texto: str, canal: str, *, en: str, programada_para: str | None = None,
           ruta_bd: Path | str | None = None) -> int:
    """Devuelve el `pieza_id` cuya aprobacion cubre la subida, o lanza `PublicacionBloqueada`."""
    ruta = Path(ruta_mp4)
    if not ruta.is_file():
        raise PublicacionBloqueada("no existe el archivo a subir: %s" % ruta)
    sha = db.sha256_archivo(ruta)
    if ruta_bd is None:
        conn = db.conectar(db.RUTA_PRODUCCION, produccion=True)
    else:
        conn = db.conectar(ruta_bd)
    try:
        db.crear_esquema(conn)
        piezas = [int(f[0]) for f in conn.execute(
            "SELECT pieza_id FROM aprobaciones WHERE sha256_mp4 = ? AND estado = 'VIGENTE' "
            "ORDER BY pieza_id", (sha,))]
        motivos = []
        for pid in piezas:
            v = db.verificar_aprobacion(conn, pid, ruta_mp4=ruta, texto=texto, canal=canal,
                                        en=en, programada_para=programada_para)
            if v["ok"]:
                return pid
            motivos.append(v["motivo"])
        db.registrar_evento(conn, en, "subida BLOQUEADA por la compuerta (%s, sha256=%s): %s"
                            % (canal, sha, "; ".join(motivos) or "sin aprobacion vigente para este archivo"))
        raise PublicacionBloqueada(
            "BLOQUEADO: %s no tiene aprobacion vigente de operador para %s (sha256 %s...). %s"
            % (ruta.name, canal, sha[:12], "; ".join(motivos)))
    finally:
        conn.close()
