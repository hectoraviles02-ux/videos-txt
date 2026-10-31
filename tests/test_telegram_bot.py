"""Tests for telegram_bot package (config, handlers, bot).

Uses unittest.mock for python-telegram-bot and external dependencies.
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Mock python-telegram-bot at sys.modules level ─────────────────────
# This MUST happen before any telegram_bot module imports.
class _UpdateMock:
    """Real class so isinstance(update, Update) passes in error_handler."""
    pass


_tg_mod = MagicMock(name="telegram")
_tg_mod.Update = _UpdateMock
_tg_ext_mod = MagicMock(name="telegram.ext")

_filters = MagicMock(name="filters")
_filters.TEXT = MagicMock()
_filters.COMMAND = MagicMock()
_filters.COMMAND.__invert__ = MagicMock(return_value=MagicMock(name="not_command"))
_filters.TEXT.__and__ = MagicMock(return_value=MagicMock(name="text_and_not_cmd"))
_filters.Document = MagicMock(name="Document")
_filters.Document.ALL = MagicMock(name="Document.ALL")

_tg_ext_mod.CommandHandler = MagicMock(name="CommandHandler")
_tg_ext_mod.MessageHandler = MagicMock(name="MessageHandler")
_tg_ext_mod.ContextTypes = MagicMock(name="ContextTypes")
_tg_ext_mod.filters = _filters

sys.modules["telegram"] = _tg_mod
sys.modules["telegram.ext"] = _tg_ext_mod

# Now safe to import telegram_bot modules
from telegram_bot.config import TelegramBotConfig, validate_and_load
from telegram_bot.handlers import (
    authorization_filter,
    cancel_handler,
    document_handler,
    error_handler,
    help_handler,
    start_handler,
    url_handler,
)
from telegram_bot.bot import main as bot_main


# ── TB-01a: init exitoso (bot.main) ───────────────────────────────────


@patch("telegram_bot.bot.Application")
def test_tb_01a_init_success(MockApp):
    mock_builder = MagicMock()
    mock_app = MagicMock()
    mock_builder.token.return_value = mock_builder
    mock_builder.build.return_value = mock_app
    MockApp.builder.return_value = mock_builder

    cfg = TelegramBotConfig(token="test:token", allowed_chat_ids=[])

    with (
        patch("telegram_bot.bot.validate_and_load", return_value=cfg),
        patch.object(mock_app, "run_polling"),
    ):
        bot_main()

    mock_app.add_handler.assert_called()
    assert mock_app.add_handler.call_count >= 4
    mock_app.add_error_handler.assert_called_once()


# ── TB-01b: token faltante → ValueError ──────────────────────────────


def test_tb_01b_token_missing(tmp_path):
    config_path = tmp_path / "empty_token.yaml"
    config_path.write_text("bot_token: ''\nallowed_chat_ids: []\n", encoding="utf-8")

    with pytest.raises(ValueError, match="VIDEOS_TXT_TELEGRAM_BOT_TOKEN not set"):
        validate_and_load(str(config_path))


# ── TB-02a: YouTube URL → url_handler ────────────────────────────────


@pytest.mark.asyncio
async def test_tb_02a_youtube_url():
    update = MagicMock()
    update.message.text = "https://youtube.com/watch?v=abc123"
    update.effective_chat.id = 123

    context = MagicMock()
    context.application.bot_data = {
        "config": TelegramBotConfig(token="t", allowed_chat_ids=[])
    }
    context.user_data = {}
    update.message.reply_text = AsyncMock()

    with (
        patch("telegram_bot.handlers.extract_video"),
        patch("telegram_bot.handlers.Path.is_file", return_value=False),
    ):
        await url_handler(update, context)

    assert update.message.reply_text.called


# ── TB-02b: article URL → url_handler ────────────────────────────────


@pytest.mark.asyncio
async def test_tb_02b_article_url():
    update = MagicMock()
    update.message.text = "https://example.com/articulo"
    update.effective_chat.id = 123

    context = MagicMock()
    context.application.bot_data = {
        "config": TelegramBotConfig(token="t", allowed_chat_ids=[])
    }
    context.user_data = {}
    update.message.reply_text = AsyncMock()

    with (
        patch("telegram_bot.handlers.URL_REGEX") as mock_regex,
        patch("telegram_bot.handlers.extract_article", return_value={"estado": "ok", "metodo": "article", "json": None}),
        patch("telegram_bot.handlers.Path.is_file", return_value=False),
    ):
        mock_regex.findall.return_value = ["https://example.com/articulo"]
        await url_handler(update, context)

    assert update.message.reply_text.called


# ── TB-02c: plain text (no URL) → help message ───────────────────────


@pytest.mark.asyncio
async def test_tb_02c_plain_text_help():
    update = MagicMock()
    update.message.text = "Hola, ¿cómo estás?"
    update.effective_chat.id = 123

    context = MagicMock()
    context.application.bot_data = {
        "config": TelegramBotConfig(token="t", allowed_chat_ids=[])
    }
    context.user_data = {}
    update.message.reply_text = AsyncMock()

    with patch("telegram_bot.handlers.URL_REGEX") as mock_regex:
        mock_regex.findall.return_value = []
        await url_handler(update, context)

    args = update.message.reply_text.call_args[0][0]
    assert any(kw in args for kw in ["URL", "SRT", "/help"])


# ── TB-03a: SRT file → document_handler ──────────────────────────────


@pytest.mark.asyncio
async def test_tb_03a_srt_file():
    update = MagicMock()
    doc = MagicMock()
    doc.file_name = "subtitulos.srt"
    doc.file_id = "file_id_123"
    update.message.document = doc
    update.effective_chat.id = 123

    context = MagicMock()
    context.application.bot_data = {
        "config": TelegramBotConfig(token="t", allowed_chat_ids=[])
    }
    context.user_data = {}
    update.message.reply_text = AsyncMock()

    mock_srt = MagicMock(name="procesar_srt")
    mock_srt.parse_srt.return_value = [{"texto": "Hola mundo", "inicio_seg": 0.0}]

    with (
        patch("telegram_bot.handlers.tempfile.mkdtemp", return_value="/tmp/test_dir"),
        patch.dict("sys.modules", {"procesar_srt": mock_srt}),
    ):
        await document_handler(update, context)

    assert update.message.reply_text.called


# ── TB-03b: storyboard JSON → document_handler ───────────────────────


@pytest.mark.asyncio
async def test_tb_03b_storyboard_json():
    update = MagicMock()
    doc = MagicMock()
    doc.file_name = "storyboard.json"
    doc.file_id = "file_id_456"
    update.message.document = doc
    update.effective_chat.id = 123

    context = MagicMock()
    context.application.bot_data = {
        "config": TelegramBotConfig(token="t", allowed_chat_ids=[])
    }
    context.user_data = {}
    update.message.reply_text = AsyncMock()

    doc_content = {
        "storyboard_version": "1.0",
        "metadata": {
            "source_script": "test",
            "title": "Test",
            "total_duration_seconds": 30,
            "scene_count": 1,
            "generated_at": "2026-07-25T12:00:00Z",
        },
        "scenes": [
            {
                "id": "scene-001",
                "type": "narrative",
                "title": "Scene 1",
                "start_seconds": 0.0,
                "end_seconds": 30.0,
                "narration": "Test.",
                "visual": {
                    "components": [{"type": "solid_color", "color": "#000"}],
                },
            }
        ],
    }

    with (
        patch("telegram_bot.handlers.tempfile.mkdtemp", return_value="/tmp/test_dir"),
        patch("telegram_bot.handlers.json.loads", return_value=doc_content),
    ):
        await document_handler(update, context)

    assert update.message.reply_text.called


# ── TB-03c: invalid file (.mp4) → error message ──────────────────────


@pytest.mark.asyncio
async def test_tb_03c_invalid_file():
    update = MagicMock()
    doc = MagicMock()
    doc.file_name = "video.mp4"
    update.message.document = doc
    update.effective_chat.id = 123

    context = MagicMock()
    context.application.bot_data = {
        "config": TelegramBotConfig(token="t", allowed_chat_ids=[])
    }
    context.user_data = {}
    update.message.reply_text = AsyncMock()

    await document_handler(update, context)

    args = update.message.reply_text.call_args[0][0]
    assert "unsupported" in args.lower() or "Accepted" in args


# ── TB-04: /start, /help, /cancel ────────────────────────────────────


@pytest.mark.asyncio
async def test_tb_04_start_handler():
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.application.bot_data = {
        "config": TelegramBotConfig(token="t", allowed_chat_ids=[])
    }

    await start_handler(update, context)
    update.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_tb_04_help_handler():
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.application.bot_data = {
        "config": TelegramBotConfig(token="t", allowed_chat_ids=[])
    }

    await help_handler(update, context)
    update.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_tb_04_cancel_handler():
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.application.bot_data = {
        "config": TelegramBotConfig(token="t", allowed_chat_ids=[])
    }
    context.user_data = {}

    await cancel_handler(update, context)
    update.message.reply_text.assert_called_once()


# ── TB-07: authorization ─────────────────────────────────────────────


def test_tb_07_authorized_chat():
    config = TelegramBotConfig(token="t", allowed_chat_ids=[100, 200])

    update = MagicMock()
    update.effective_chat.id = 100
    assert authorization_filter(update, config) is True

    update = MagicMock()
    update.effective_chat.id = 200
    assert authorization_filter(update, config) is True


def test_tb_07_unauthorized_chat():
    config = TelegramBotConfig(token="t", allowed_chat_ids=[100, 200])

    update = MagicMock()
    update.effective_chat.id = 300
    assert authorization_filter(update, config) is False


def test_tb_07_empty_allowed_list_allows_all():
    config = TelegramBotConfig(token="t", allowed_chat_ids=[])

    update = MagicMock()
    update.effective_chat.id = 999
    assert authorization_filter(update, config) is True


# ── TB-08: error handler ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tb_08_error_handler():
    update = MagicMock()
    update.__class__ = _UpdateMock
    update.effective_chat.send_message = AsyncMock()
    context = MagicMock()
    context.error = RuntimeError("Something went wrong")

    await error_handler(update, context)

    update.effective_chat.send_message.assert_called_once()
