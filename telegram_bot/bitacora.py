"""Bitácora automática de solicitudes y errores del bot.

Cada solicitud que llega al bot (URL, HTML, PDF, audio, SRT, storyboard)
se registra en ``logs/bitacora.jsonl`` con su resultado (éxito, error,
duración). La bitácora es append-only y no se borra (R07).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

BITACORA_FILE = Path("logs/bitacora.jsonl")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_file() -> None:
    BITACORA_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not BITACORA_FILE.exists():
        BITACORA_FILE.write_text("", encoding="utf-8")


def registrar(
    tipo: str,
    entrada: str,
    resultado: str,
    *,
    detalle: str | None = None,
    duracion_seg: float | None = None,
    run_id: str | None = None,
) -> None:
    """Registra una entrada en la bitácora.

    Args:
        tipo: tipo de solicitud ("url", "html", "pdf", "audio", "srt", "storyboard", "comando").
        entrada: descripción corta de lo que se pidió (ej. título o nombre de archivo).
        resultado: "OK" | "ERROR" | "RECHAZADO" | "PENDIENTE".
        detalle: mensaje de error o detalle (opcional).
        duracion_seg: duración del procesamiento en segundos (opcional).
        run_id: id de corrida si aplica (opcional).
    """
    try:
        _ensure_file()
        entry = {
            "ts": _now(),
            "tipo": tipo,
            "entrada": entrada,
            "resultado": resultado,
            "detalle": detalle,
            "duracion_seg": duracion_seg,
            "run_id": run_id,
        }
        # Remover claves None para no ensuciar el JSONL
        entry = {k: v for k, v in entry.items() if v is not None}
        with BITACORA_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.info("Bitácora: %s %s -> %s", tipo, entrada, resultado)
    except Exception as e:
        # La bitácora nunca debe romper el flujo del bot
        logger.warning("No se pudo registrar en bitácora: %s", e)


def resumen_reciente(limite: int = 10) -> list[dict]:
    """Devuelve las últimas ``limite`` entradas de la bitácora."""
    if not BITACORA_FILE.exists():
        return []
    lineas = BITACORA_FILE.read_text(encoding="utf-8").splitlines()
    out = []
    for ln in lineas[-limite:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out
