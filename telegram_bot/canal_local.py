"""Canal de conversacion con modelo LOCAL (Ollama) desde Telegram.

Comando /c <mensaje>.

POR QUE LOCAL Y NO EL CLI DE CLAUDE:
Gratis, privado (nada sale de esta maquina) y sin depender de la red.
El costo es real y hay que tenerlo presente: **este canal no tiene
herramientas**. No lee el repositorio, no busca en la web y no consulta
engram. Solo sabe lo que dice su prompt de sistema y lo que se hablo en la
conversacion. Si le preguntan algo que exige mirar un archivo, debe decir que
no puede en vez de inventarlo.

MODELO: se usa el MISMO que el pipeline (qwen2.5:14b). Con 8 GB de VRAM,
alternar entre dos modelos obliga a Ollama a descargar y recargar pesos en
cada cambio. Mismo modelo = cero recargas.

Medicion del 2026-08-18 sobre la misma tarea de extraccion:
  7b  ->  8.0s, 27.7 tok/s — perdio el sujeto del texto
  14b -> 37.2s, 10.4 tok/s — lo identifico correctamente
Por eso se prefiere el 14b aunque se desborde a CPU.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import requests
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parent.parent
HISTORIAL_DIR = RAIZ / "runs" / "_canal"
OLLAMA = "http://localhost:11434/api/chat"
MODELO = "qwen2.5:14b-instruct-q4_K_M"
TIMEOUT_SEG = 300
LIMITE_TELEGRAM = 4000
MAX_TURNOS = 12  # cuantos pares pregunta/respuesta se conservan por chat

SISTEMA = (
    "Sos el asistente del proyecto VIDEOS-TXT, que corre en la maquina de el operador. "
    "El proyecto recibe links y archivos por Telegram, extrae su contenido a texto, "
    "genera un guion y produce un video.\n\n"
    "REGLAS QUE NO SE NEGOCIAN:\n"
    "1. No inventes. Si no sabes algo o te piden un dato que exige mirar un archivo, "
    "deci claramente que no podes verificarlo desde aca.\n"
    "2. Distingui siempre un hecho de una suposicion.\n"
    "3. NO tenes herramientas: no podes leer el repositorio, ni buscar en internet, "
    "ni ejecutar nada. Si hace falta eso, deci que se resuelve desde la terminal.\n\n"
    "Responde en español rioplatense, directo, sin relleno y breve: Telegram corta "
    "en 4096 caracteres."
)


def _archivo(chat_id: int) -> Path:
    HISTORIAL_DIR.mkdir(parents=True, exist_ok=True)
    return HISTORIAL_DIR / f"chat_{chat_id}.json"


def _cargar(chat_id: int) -> list[dict]:
    p = _archivo(chat_id)
    if not p.is_file():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _guardar(chat_id: int, historial: list[dict]) -> None:
    recorte = historial[-(MAX_TURNOS * 2):]
    try:
        _archivo(chat_id).write_text(
            json.dumps(recorte, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        logger.warning("canal_local: no se pudo guardar historial: %s", e)


def _preguntar(mensaje: str, chat_id: int) -> str:
    historial = _cargar(chat_id)
    mensajes = [{"role": "system", "content": SISTEMA}] + historial + [
        {"role": "user", "content": mensaje}]
    try:
        r = requests.post(
            OLLAMA,
            json={"model": MODELO, "messages": mensajes, "stream": False},
            timeout=TIMEOUT_SEG,
        )
    except requests.exceptions.ConnectionError:
        return ("No puedo hablar con Ollama en localhost:11434. "
                "Verifica que este corriendo.")
    except requests.exceptions.Timeout:
        return f"Se paso de {TIMEOUT_SEG}s sin responder. Proba algo mas corto."

    if r.status_code != 200:
        return "Ollama respondio %d: %s" % (r.status_code, r.text[:180])

    try:
        respuesta = r.json().get("message", {}).get("content", "").strip()
    except json.JSONDecodeError:
        return "Ollama devolvio algo que no pude interpretar."

    if not respuesta:
        return "(respuesta vacia)"

    historial += [{"role": "user", "content": mensaje},
                  {"role": "assistant", "content": respuesta}]
    _guardar(chat_id, historial)
    return respuesta


def _trozos(texto: str) -> list[str]:
    if len(texto) <= LIMITE_TELEGRAM:
        return [texto]
    partes, actual = [], ""
    for linea in texto.splitlines(keepends=True):
        if len(actual) + len(linea) > LIMITE_TELEGRAM:
            if actual:
                partes.append(actual)
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
    texto = " ".join(context.args).strip() if context.args else ""

    if texto.lower() in ("reset", "olvida", "borrar"):
        _archivo(chat_id).unlink(missing_ok=True)
        await msg.reply_text("Listo, olvide la conversacion anterior.")
        return

    if not texto:
        await msg.reply_text(
            "Uso: /c <tu mensaje>\n\n"
            "Ejemplo: /c explicame que es un storyboard\n\n"
            f"Corre con {MODELO} en esta maquina: gratis y privado.\n"
            "NO lee el repo ni busca en internet: solo conversa.\n"
            "Para borrar la memoria de este chat: /c reset"
        )
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    logger.info("canal_local: chat %s, %d chars", chat_id, len(texto))

    respuesta = await asyncio.to_thread(_preguntar, texto, chat_id)
    for parte in _trozos(respuesta):
        await msg.reply_text(parte)
