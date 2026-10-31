from __future__ import annotations

import logging

from telegram.ext import Application

from telegram_bot.config import validate_and_load
from telegram_bot.handlers import HANDLERS, error_handler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# Los clientes HTTP loguean la URL completa en INFO, y la URL de la Telegram Bot
# API lleva el token embebido. Se bajan a WARNING para no filtrar credenciales al
# log. NO afecta el logging de la aplicacion ni sus excepciones.
for _noisy in ("httpx", "httpcore", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


def main() -> None:
    config = validate_and_load()

    # El handshake TLS en algunas redes supera el connect_timeout de fabrica
    # de python-telegram-bot (5 s) y el bot muere en get_me() con
    # httpx.ConnectError. Medido: con 10 s falla, con 30 s conecta en 2 s.
    application = (
        Application.builder()
        .token(config.token)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .pool_timeout(30.0)
        .get_updates_connect_timeout(30.0)
        .get_updates_read_timeout(30.0)
        .build()
    )
    application.bot_data["config"] = config

    # Asistente (saludo + plática + router de intención + menú) en grupo de MAYOR
    # prioridad: decide primero y detiene la cadena cuando corresponde. handlers.py intacto.
    from telegram_bot.asistente import ASISTENTE_HANDLERS
    for handler in ASISTENTE_HANDLERS:
        application.add_handler(handler, group=-1)

    for handler in HANDLERS:
        application.add_handler(handler)

    # Revisión/publicación de reels (módulo separado; handlers.py queda intacto).
    from telegram_bot.reels_review import REELS_HANDLERS
    for handler in REELS_HANDLERS:
        application.add_handler(handler)

    # Etapa 4 (22-oct-2026): botón APROBAR por pieza (código de un solo uso) → cola → distribuidor.
    from telegram_bot.aprobacion_botones import APROBACION_HANDLERS
    for handler in APROBACION_HANDLERS:
        application.add_handler(handler)

    application.add_error_handler(error_handler)

    application.run_polling()


if __name__ == "__main__":
    main()
