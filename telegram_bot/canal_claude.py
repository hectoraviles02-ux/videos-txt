"""Canal de conversacion con Claude desde Telegram.

Comando /c <mensaje> — habla con Claude Code sobre este proyecto.

DISEÑO Y SUS LIMITES (leer antes de tocar):

1. Cada chat tiene su propio `--session-id` derivado del chat_id, asi que la
   conversacion MANTIENE MEMORIA entre mensajes. No arranca en blanco.

2. **Herramientas de SOLO LECTURA.** `repo_guard/ai_review/runner.py` invoca el
   CLI con `--dangerously-skip-permissions`; aca NO se hace. Un canal abierto a
   Telegram con permisos totales permitiria que cualquiera con acceso al chat
   escriba archivos o ejecute comandos en esta maquina. Se limita a leer,
   buscar y consultar la web.

3. Es una sesion DISTINTA de la que corre en la terminal. Comparte el proyecto
   y la memoria de engram, no el hilo de una conversacion abierta en otro lado.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import uuid
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parent.parent
TIMEOUT_SEG = 240
LIMITE_TELEGRAM = 4000  # el maximo real es 4096; se deja margen

# Solo lectura.
#
# OJO: `--allowedTools` NO restringe: es una lista de AUTO-APROBACION. Se probo
# empiricamente y con solo esa lista el canal seguia ejecutando `whoami` y
# `git rev-list`. La restriccion real la impone `--disallowedTools`.
HERRAMIENTAS = "Read,Grep,Glob,WebFetch,WebSearch"

# Denegadas explicitamente: nada que escriba, edite o ejecute en la maquina.
# Este canal esta expuesto a Telegram; cualquiera con acceso al chat hablaria
# con el.
PROHIBIDAS = "Bash Edit Write NotebookEdit MultiEdit Agent Task"

# Namespace fijo para derivar un session-id estable por chat.
_NS = uuid.UUID("6f1d2c34-5a6b-4c7d-8e9f-0a1b2c3d4e5f")

CONTEXTO = (
    "Estas respondiendo por Telegram a el operador, dueño del proyecto VIDEOS-TXT "
    "(C:\\VIDEOS-TXT): un extractor de contenido URL -> Markdown/JSON con bot de "
    "Telegram y pipeline de video. "
    "Reglas del proyecto que debes respetar: nada inventado (todo dato sale de la "
    "fuente via herramienta); si no podes verificar algo, decilo en vez de "
    "suponerlo; distingui siempre hecho de hipotesis. "
    "Tenes herramientas de SOLO LECTURA: podes leer y buscar en el repo y en la "
    "web, pero no escribir ni ejecutar. Si te piden algo que requiere escribir, "
    "explica que ese cambio se hace desde la sesion de terminal. "
    "Responde en español rioplatense, directo y sin relleno. "
    "Telegram corta en 4096 caracteres: se breve."
)


def _session_id(chat_id: int) -> str:
    """UUID estable por chat: mismo chat -> misma sesion -> memoria continua."""
    return str(uuid.uuid5(_NS, f"telegram-{chat_id}"))


def _correr(args: list[str], mensaje: str) -> subprocess.CompletedProcess:
    cmd = [
        "claude", "--print",
        *args,
        "--append-system-prompt", CONTEXTO,
        "--allowedTools", HERRAMIENTAS,
        "--disallowedTools", *PROHIBIDAS.split(),
        "-p", mensaje,
    ]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=TIMEOUT_SEG, cwd=str(RAIZ),
    )


def _preguntar(mensaje: str, chat_id: int) -> str:
    """Continua la sesion del chat; si aun no existe, la crea.

    `--session-id` SOLO sirve para CREAR: reusarlo devuelve
    "Session ID ... is already in use". Para continuar hay que usar `--resume`.
    Se intenta resume primero y se cae a session-id si la sesion no existe.
    """
    sid = _session_id(chat_id)
    try:
        r = _correr(["--resume", sid], mensaje)
        if r.returncode != 0:
            r = _correr(["--session-id", sid], mensaje)
    except subprocess.TimeoutExpired:
        return f"Se paso de {TIMEOUT_SEG}s sin responder. Proba una pregunta mas acotada."
    except FileNotFoundError:
        return "No encuentro el CLI `claude` en el PATH de este proceso."

    if r.returncode != 0:
        err = (r.stderr or "").strip().splitlines()
        return "El CLI fallo (codigo %d): %s" % (
            r.returncode, err[-1][:250] if err else "sin detalle")

    salida = (r.stdout or "").strip()
    return salida or "(respuesta vacia)"


def _trozos(texto: str) -> list[str]:
    """Parte respetando saltos de linea, para no cortar a mitad de palabra."""
    if len(texto) <= LIMITE_TELEGRAM:
        return [texto]
    partes, actual = [], ""
    for linea in texto.splitlines(keepends=True):
        if len(actual) + len(linea) > LIMITE_TELEGRAM:
            if actual:
                partes.append(actual)
            # una sola linea gigante: se corta duro
            while len(linea) > LIMITE_TELEGRAM:
                partes.append(linea[:LIMITE_TELEGRAM])
                linea = linea[LIMITE_TELEGRAM:]
            actual = linea
        else:
            actual += linea
    if actual:
        partes.append(actual)
    return partes


async def canal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat_id = update.effective_chat.id

    pregunta = " ".join(context.args).strip() if context.args else ""
    if not pregunta:
        await msg.reply_text(
            "Uso: /c <tu mensaje>\n\n"
            "Ejemplo: /c que pendientes quedaron del pipeline de video?\n\n"
            "Mantiene memoria de la conversacion. Solo lectura: puede consultar "
            "el repo y la web, no modificar archivos."
        )
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    logger.info("canal_claude: consulta de chat %s (%d chars)", chat_id, len(pregunta))

    respuesta = await asyncio.to_thread(_preguntar, pregunta, chat_id)

    for parte in _trozos(respuesta):
        await msg.reply_text(parte)
