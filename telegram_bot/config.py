"""Telegram bot configuration loader.

Reads ``config/telegram_bot.yaml``, resolves ``${ENV_VAR}`` patterns,
validates required values and returns a typed ``TelegramBotConfig``.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def _resolve_env_vars(value: str, environ: dict[str, str] | None = None) -> str:
    """Replace ``${VAR}`` placeholders with values from *environ* (default: ``os.environ``)."""
    if environ is None:
        environ = dict(os.environ)

    def _replacer(m: re.Match[str]) -> str:
        return environ.get(m.group(1), m.group(0))

    return _ENV_VAR_RE.sub(_replacer, value)


def _resolve_value(value: Any, environ: dict[str, str] | None = None) -> Any:
    """Recursively resolve env-var patterns in strings."""
    if isinstance(value, str):
        return _resolve_env_vars(value, environ=environ)
    if isinstance(value, dict):
        return {k: _resolve_value(v, environ=environ) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_value(v, environ=environ) for v in value]
    return value


@dataclass
class TelegramBotConfig:
    token: str
    allowed_chat_ids: list[int]

    # Ollama
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:14b"

    # Stable Diffusion
    sd_model_id: str = "SG161222/Realistic_Vision_V5.1_noVAE"
    sd_device: str = "cuda"
    sd_params: dict[str, Any] = field(default_factory=lambda: {
        "width": 1024,
        "height": 576,
        "num_inference_steps": 25,
        "guidance_scale": 7.5,
        "negative_prompt": "blurry, low quality, distorted, ugly, deformed",
        "quality_prefix": "masterpiece, best quality, highly detailed, ",
        "low_vram": True,
    })

    # Pipeline
    max_duration_seconds: int = 600
    temp_dir: str = "runs"
    adapter: str = "ffmpeg"  # "ffmpeg" | "sd"
    language: str = "es"
    tts_engine: str = "edge"  # "gtts" | "edge"
    tts_voice: str = "es-MX-JorgeNeural"
    # Voz clonada: ruta al clip de referencia y AL TEXTO que dice ese clip.
    # Los dos hacen falta; con el audio solo, el clonado degrada.
    voz_referencia: str | None = None
    voz_referencia_texto: str | None = None
    # Buscar el tema en fuentes oficiales antes de escribir el guion.
    investigar: bool = False
    # Archivo de consultas a una base propia. Sus hechos se SUMAN a los de
    # las fuentes publicas cuando el tema es de los que esa base cubre.
    fuente_local: str | None = None
    # Modo rapido del generador de clips por IA (adapter "ia"). Apagado por
    # defecto: produccion corre en DDIM 20 pasos, que es lo que estaba antes
    # de que este flag existiera. No hace nada con los otros adapters.
    video_rapido: bool = False


def load_config(path: str) -> TelegramBotConfig:
    """Parse a YAML config file into a ``TelegramBotConfig``.

    Resolves ``${ENV_VAR}`` placeholders in string values.
    """
    try:
        import yaml
    except ImportError:
        raise ImportError(
            "PyYAML is required to load telegram bot config. "
            "Install it with: pip install pyyaml"
        )

    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8"))

    # Resolve env vars across the whole tree
    raw = _resolve_value(raw)  # type: ignore[assignment]

    bot = raw.get("bot_token", "")
    allowed: list[int] = raw.get("allowed_chat_ids", [])

    ollama_cfg: dict[str, Any] = raw.get("ollama", {})
    sd_cfg: dict[str, Any] = raw.get("sd", {})
    pipeline_cfg: dict[str, Any] = raw.get("pipeline", {})

    sd_params: dict[str, Any] = {
        "width": sd_cfg.get("width", 1024),
        "height": sd_cfg.get("height", 576),
        "num_inference_steps": sd_cfg.get("num_inference_steps", 25),
        "guidance_scale": sd_cfg.get("guidance_scale", 7.5),
        "negative_prompt": sd_cfg.get(
            "negative_prompt", "blurry, low quality, distorted, ugly, deformed"
        ),
        "quality_prefix": sd_cfg.get(
            "quality_prefix", "masterpiece, best quality, highly detailed, "
        ),
        "low_vram": sd_cfg.get("low_vram", True),
    }

    return TelegramBotConfig(
        token=bot,
        allowed_chat_ids=allowed,
        ollama_url=ollama_cfg.get("url", "http://localhost:11434"),
        ollama_model=ollama_cfg.get("model", "qwen2.5:14b-instruct-q4_K_M"),
        sd_model_id=sd_cfg.get("model_id", "SG161222/Realistic_Vision_V5.1_noVAE"),
        sd_device=sd_cfg.get("device", "cuda"),
        sd_params=sd_params,
        max_duration_seconds=pipeline_cfg.get("max_duration_seconds", 600),
        temp_dir=pipeline_cfg.get("temp_dir", "runs"),
        adapter=pipeline_cfg.get("adapter", "ffmpeg"),
        language=pipeline_cfg.get("language", "es"),
        tts_engine=pipeline_cfg.get("tts_engine", "edge"),
        tts_voice=pipeline_cfg.get("tts_voice", "es-MX-JorgeNeural"),
        voz_referencia=pipeline_cfg.get("voz_referencia"),
        voz_referencia_texto=pipeline_cfg.get("voz_referencia_texto"),
        investigar=bool(pipeline_cfg.get("investigar", False)),
        fuente_local=pipeline_cfg.get("fuente_local"),
        video_rapido=bool(pipeline_cfg.get("video_rapido", False)),
    )


def _load_env() -> None:
    """Load ``.env`` from project root into ``os.environ`` (stdlib, no dotenv)."""
    root = Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    if not env_path.is_file():
        return
    for ln in env_path.read_text(encoding="utf-8-sig").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, _, v = ln.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and not os.environ.get(k):
            os.environ.setdefault(k, v)


def verify_token(token: str) -> bool:
    """Verify a bot token by calling Telegram's ``getMe`` API.

    Returns ``True`` if the token is valid, ``False`` otherwise.
    Logs details about the bot on success.
    """
    import urllib.request
    import json

    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("ok"):
            bot_info = data.get("result", {})
            logger.info(
                "Telegram bot verified: @%s (id=%s)",
                bot_info.get("username", "?"),
                bot_info.get("id", "?"),
            )
            return True
        logger.error("Telegram getMe returned not-ok: %s", data)
        return False
    except urllib.error.HTTPError as e:
        if e.code == 401:
            logger.error("Telegram bot token invalid (HTTP 401)")
        else:
            logger.error("Telegram API error (HTTP %s): %s", e.code, e)
        return False
    except Exception as e:
        logger.error("Failed to verify Telegram token: %s", e)
        return False


def validate_and_load(config_path: str | None = None) -> TelegramBotConfig:
    """Load env vars, parse config, and validate required fields.

    Args:
        config_path: Path to the YAML config file.
                     Defaults to ``config/telegram_bot.yaml``.

    Returns:
        A validated ``TelegramBotConfig``.

    Raises:
        ValueError: If the bot token is empty or unset.
    """
    _load_env()

    if config_path is None:
        config_path = str(
            Path(__file__).resolve().parent.parent / "config" / "telegram_bot.yaml"
        )

    cfg = load_config(config_path)

    if not cfg.token:
        raise ValueError(
            "VIDEOS_TXT_TELEGRAM_BOT_TOKEN not set in .env or config/telegram_bot.yaml"
        )

    if not verify_token(cfg.token):
        raise ValueError(
            f"Telegram bot token is invalid or the API is unreachable. "
            f"Check the token in .env (VIDEOS_TXT_TELEGRAM_BOT_TOKEN) "
            f"and verify the bot exists."
        )

    if not cfg.allowed_chat_ids:
        logger.warning("allowed_chat_ids is empty — any chat will be rejected")

    return cfg
