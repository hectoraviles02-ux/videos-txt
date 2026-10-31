# -*- coding: utf-8 -*-
"""Auxiliar de PRUEBAS: simula el boton APROBAR de Telegram (Etapa 1, v6).

Desde v6 una aprobacion solo nace de un codigo de un solo uso emitido por la fabrica y
devuelto por el usuario destinatario. Las pruebas que necesitan una pieza aprobada pasan
por aqui, igual que lo hara el manejador real de Telegram (Etapa 4).
"""
from content_factory import db

USUARIO = "1001"
CHAT = "1001"


def aprobar_como_telegram(conn, pieza_id, *, ruta_mp4, texto, canales, en="2026-10-21T10:00:00-06:00",
                          programada_para=None, usuario=USUARIO, mensaje="501"):
    codigo = db.emitir_codigo(conn, pieza_id, ruta_mp4=ruta_mp4, destinatario_user_id=usuario, emitido_en=en)
    return db.aprobar_pieza(
        conn, pieza_id, ruta_mp4=ruta_mp4, texto=texto, canales=canales, otorgada_en=en,
        programada_para=programada_para,
        comprobante={"origen": "telegram", "codigo": codigo, "user_id": usuario, "chat_id": CHAT,
                     "message_id": mensaje})
