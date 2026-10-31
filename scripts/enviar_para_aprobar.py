# -*- coding: utf-8 -*-
"""Manda a el operador por Telegram cada video de una pieza con su botón APROBAR (Etapa 4, 22-oct-2026).

Registra los videos de <carpeta>/publicacion.json en la cola real (content.db) y, por cada uno, envía el MP4
tal cual y el texto completo que se publicaría, con [✅ APROBAR y publicar] y [🗑 No publicar].
NO aprueba ni publica nada: eso solo pasa cuando el operador aprieta el botón (R27). El bot debe estar corriendo
(scripts/arrancar_bot.cmd) para atender el botón.

Uso: python scripts/enviar_para_aprobar.py <carpeta_pieza> [formato ...]   (formato: largo_YT, corto_FB, ...)
"""
import os
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from content_factory import db  # noqa: E402
from review_channels.telegram_channel import _load_env  # noqa: E402  carga el .env sin imprimir el token

_load_env()
from telegram_bot import aprobacion_botones as ab  # noqa: E402

carpeta, formatos = Path(sys.argv[1]).resolve(), set(sys.argv[2:])
destinatario = os.environ.get("VIDEOS_TXT_TELEGRAM_CHAT_ID", "").strip()
if not destinatario:
    sys.exit("falta VIDEOS_TXT_TELEGRAM_CHAT_ID")
en = datetime.now().astimezone().isoformat()
conn = db.conectar(db.RUTA_PRODUCCION, produccion=True)
db.crear_esquema(conn)
try:
    for pid, video in ab.registrar(conn, carpeta, en=en):
        if formatos and video["formato"] not in formatos:
            continue
        r = ab.enviar_para_aprobar(conn, pid, destinatario=destinatario, en=en)
        print("pieza %d %-9s -> %s: %s" % (pid, video["formato"], video["canal"], "enviado" if r["ok"] else r))
finally:
    conn.close()
