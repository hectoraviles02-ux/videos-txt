"""Telegram channel for delivering review packages (FASE 8).

Flow: VIDEO_RENDERED -> TECHNICAL_PASS -> review package -> Telegram -> human review

Telegram is ONLY a delivery channel. It never writes EDITORIAL_APPROVED or REJECTED.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class VideoTooLargeError(Exception):
    """Raised when a video exceeds the Telegram file size limit and compression fails."""


def _load_env() -> None:
    """Load .env from project root into os.environ (stdlib, no dotenv)."""
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


_load_env()


# ── Configuration ───────────────────────────────────────────────────────

_ENV_TOKEN = "VIDEOS_TXT_TELEGRAM_BOT_TOKEN"
_ENV_CHAT_ID = "VIDEOS_TXT_TELEGRAM_CHAT_ID"
_ENV_ENABLED = "VIDEOS_TXT_TELEGRAM_ENABLED"

_TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
_MAX_TEXT_LEN = 4000
_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB (Telegram bot limit)
_TIMEOUT_TEXT = 20
_TIMEOUT_FILE = 300


def is_enabled() -> bool:
    return os.environ.get(_ENV_ENABLED, "").strip().lower() == "true"


def _token() -> str:
    tok = os.environ.get(_ENV_TOKEN, "").strip()
    if not tok:
        raise RuntimeError(f"{_ENV_TOKEN} not set")
    return tok


def _chat_id() -> str:
    cid = os.environ.get(_ENV_CHAT_ID, "").strip()
    if not cid:
        raise RuntimeError(f"{_ENV_CHAT_ID} not set")
    return cid


# ── Internal API helpers ────────────────────────────────────────────────


def _api_call(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    token = _token()
    url = _TELEGRAM_API.format(token=token, method=method)
    if params:
        data = urllib.parse.urlencode(params).encode("utf-8")
    else:
        data = None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=_TIMEOUT_TEXT) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── Compression ──────────────────────────────────────────────────────────


def _find_ffmpeg() -> str | None:
    """Locate ffmpeg in PATH or project-local bin/."""
    ffmpeg = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    local = Path(__file__).resolve().parent.parent / "bin" / "ffmpeg.exe"
    return str(local) if local.is_file() else None


def _compress_video(path: Path) -> str:
    """Compress video with ffmpeg -crf 28. Returns path to compressed file."""
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        raise VideoTooLargeError("ffmpeg not found — cannot compress video")

    tmp_path = path.with_suffix(".compressed.mp4")
    cmd = [
        ffmpeg, "-y",
        "-i", str(path),
        "-c:v", "libx264",
        "-crf", "28",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(tmp_path),
    ]
    logger.info("Compressing video: %s -> %s", path.name, tmp_path.name)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise VideoTooLargeError("Compression timed out after 300s")

    if result.returncode != 0 or not tmp_path.is_file():
        raise VideoTooLargeError(
            f"Compression failed (code {result.returncode}): {result.stderr[-300:]}"
        )
    return str(tmp_path)


def send_video_to_chat(
    chat_id: int,
    file_path: str,
    caption: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Send a video file directly to a Telegram chat using sendVideo API.

    Validates file, compresses if over 50MB limit, and delivers inline.
    Token can be passed explicitly or read from ``VIDEOS_TXT_TELEGRAM_BOT_TOKEN``.
    """
    import requests as _requests

    path = Path(file_path)
    if not path.is_file():
        return {"status": "error", "error": f"File not found: {file_path}"}

    _token = token or os.environ.get(_ENV_TOKEN, "").strip()
    if not _token:
        return {"status": "error", "error": f"No token provided and {_ENV_TOKEN} not set"}

    file_size = path.stat().st_size
    effective_path = file_path

    if file_size > _MAX_FILE_BYTES:
        logger.warning(
            "Video %s is %.1fMB (max %dMB), attempting compression...",
            path.name, file_size / (1024 * 1024), _MAX_FILE_BYTES / (1024 * 1024),
        )
        try:
            effective_path = _compress_video(path)
            compressed_size = Path(effective_path).stat().st_size
            if compressed_size > _MAX_FILE_BYTES:
                raise VideoTooLargeError(
                    f"Video still too large after compression: "
                    f"{compressed_size / (1024 * 1024):.1f}MB (max {_MAX_FILE_BYTES / (1024 * 1024):.0f}MB)"
                )
        except VideoTooLargeError:
            raise
        except Exception as e:
            raise VideoTooLargeError(
                f"Video too large ({file_size / (1024 * 1024):.1f}MB) and compression failed: {e}"
            )

    url = _TELEGRAM_API.format(token=_token, method="sendVideo")
    fh = Path(effective_path)
    files: dict[str, Any] = {"video": (fh.name, fh.open("rb"))}
    data: dict[str, Any] = {"chat_id": str(chat_id)}
    if caption:
        data["caption"] = caption[:_MAX_TEXT_LEN]

    try:
        resp = _requests.post(url, data=data, files=files, timeout=_TIMEOUT_FILE)
        resp.raise_for_status()
        result = resp.json()
        return {
            "status": "success",
            "message_id": result.get("result", {}).get("message_id"),
        }
    except Exception as e:
        return {
            "status": "error",
            "error": f"{type(e).__name__}: {str(e)[:200]}",
        }


# ── Sending primitives ──────────────────────────────────────────────────


def send_text(text: str) -> dict[str, Any]:
    resp = _api_call("sendMessage", {
        "chat_id": _chat_id(),
        "text": text[:_MAX_TEXT_LEN],
        "disable_web_page_preview": "true",
        "parse_mode": "HTML",
    })
    return {
        "message_id": resp.get("result", {}).get("message_id"),
        "ok": resp.get("ok", False),
    }


def send_document(file_path: str, caption: str | None = None) -> dict[str, Any]:
    import requests as _requests

    path = Path(file_path)
    if not path.is_file():
        return {
            "ok": False,
            "error": f"File not found: {file_path}",
        }
    file_size = path.stat().st_size
    if file_size > _MAX_FILE_BYTES:
        return {
            "ok": False,
            "error": f"File too large: {file_size} bytes (max {_MAX_FILE_BYTES})",
            "file": path.name,
            "size_bytes": file_size,
        }

    token = _token()
    url = _TELEGRAM_API.format(token=token, method="sendDocument")
    files: dict[str, Any] = {"document": (path.name, path.open("rb"))}
    data: dict[str, Any] = {"chat_id": _chat_id()}
    if caption:
        data["caption"] = caption[:_MAX_TEXT_LEN]

    try:
        resp = _requests.post(
            url, data=data, files=files, timeout=_TIMEOUT_FILE
        )
        resp.raise_for_status()
        result = resp.json()
        return {
            "ok": result.get("ok", False),
            "message_id": result.get("result", {}).get("message_id"),
            "file": path.name,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
            "file": path.name,
        }


def send_validation_report(report_path: str) -> dict[str, Any]:
    return send_document(report_path, caption="Validation Report")


# ── Review package (high-level) ─────────────────────────────────────────


def send_review_package(
    *,
    video_path: str,
    validation_report_path: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a complete review package to Telegram.

    Returns structured result with delivery status per file.
    """
    t0 = time.time()
    started_at = datetime.now(timezone.utc).isoformat()

    result: dict[str, Any] = {
        "provider": "telegram",
        "started_at": started_at,
        "sent_files": [],
        "message_ids": [],
        "warnings": [],
        "error": None,
    }

    if not is_enabled():
        return {
            **result,
            "status": "failed",
            "error": {"message": "Telegram channel not enabled", "type": "config"},
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }

    try:
        token_ok = bool(os.environ.get(_ENV_TOKEN, "").strip())
        chat_ok = bool(os.environ.get(_ENV_CHAT_ID, "").strip())
        if not token_ok or not chat_ok:
            return {
                **result,
                "status": "failed",
                "error": {
                    "message": f"Missing env vars: token={'set' if token_ok else 'MISSING'}, "
                               f"chat_id={'set' if chat_ok else 'MISSING'}",
                    "type": "config",
                },
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
    except Exception as e:
        return {
            **result,
            "status": "failed",
            "error": {"message": str(e), "type": "config"},
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }

    # 1. Send summary text
    summary_lines = ["<b>Review Package</b>"]
    if metadata:
        summary_lines.append(f"Title: {metadata.get('title', 'N/A')}")
        summary_lines.append(f"Duration: {metadata.get('total_duration_seconds', '?')}s")
        summary_lines.append(f"Scenes: {metadata.get('scene_count', '?')}")
        summary_lines.append(f"Video: {Path(video_path).name}")
    summary_lines.append("")
    summary_lines.append("Send your APPROVED or REJECTED decision via the review tool.")

    try:
        text_result = send_text("\n".join(summary_lines))
        if text_result.get("message_id"):
            result["message_ids"].append(text_result["message_id"])
    except Exception as e:
        result["warnings"].append(f"send_text failed: {type(e).__name__}: {str(e)[:100]}")

    # 2. Send video file
    try:
        doc_result = send_document(video_path, caption="Video output")
        if doc_result.get("ok"):
            result["sent_files"].append(Path(video_path).name)
            if doc_result.get("message_id"):
                result["message_ids"].append(doc_result["message_id"])
        else:
            err = doc_result.get("error", "unknown")
            if "too large" in str(err):
                result["warnings"].append(
                    f"Video too large for Telegram ({doc_result.get('size_bytes', '?')} bytes). "
                    f"Consider hosting externally or splitting."
                )
            else:
                result["warnings"].append(f"Video send failed: {err}")
    except Exception as e:
        result["warnings"].append(f"send_video exception: {type(e).__name__}: {str(e)[:100]}")

    # 3. Send validation report
    if validation_report_path and os.path.isfile(validation_report_path):
        try:
            vr_result = send_validation_report(validation_report_path)
            if vr_result.get("ok"):
                result["sent_files"].append(Path(validation_report_path).name)
                if vr_result.get("message_id"):
                    result["message_ids"].append(vr_result["message_id"])
            else:
                result["warnings"].append(
                    f"Validation report send failed: {vr_result.get('error', 'unknown')}"
                )
        except Exception as e:
            result["warnings"].append(
                f"validation report exception: {type(e).__name__}: {str(e)[:100]}"
            )

    # 4. Determine final status (count only actual delivery attempts)
    deliveries: list[tuple[str, bool]] = [("text", bool(result["message_ids"]))]
    if video_path:
        deliveries.append(("video", Path(video_path).name in result["sent_files"]))
    if validation_report_path:
        deliveries.append(
            ("validation", Path(validation_report_path).name in result["sent_files"])
        )

    total_attempts = len(deliveries)
    successes = sum(1 for _, ok in deliveries if ok)
    has_error = bool(result.get("error"))

    if has_error:
        result["status"] = "failed"
    elif successes == total_attempts:
        result["status"] = "success"
    elif successes > 0:
        result["status"] = "partial"
    else:
        result["status"] = "failed"
        if not result["error"]:
            result["error"] = {
                "message": "No deliveries succeeded",
                "type": "delivery",
            }

    result["duration_seconds"] = round(time.time() - t0, 2)
    result["finished_at"] = datetime.now(timezone.utc).isoformat()

    return result
