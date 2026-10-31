"""Tests for review_channels/telegram_channel.py"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from review_channels.telegram_channel import (
    VideoTooLargeError,
    is_enabled,
    send_document,
    send_review_package,
    send_text,
    send_video_to_chat,
)


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_env():
    """Fixture que limpia vars de Telegram antes/después de cada test."""
    keys = [
        "VIDEOS_TXT_TELEGRAM_BOT_TOKEN",
        "VIDEOS_TXT_TELEGRAM_CHAT_ID",
        "VIDEOS_TXT_TELEGRAM_ENABLED",
    ]
    saved = {k: os.environ.pop(k, None) for k in keys}
    yield
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


@pytest.fixture
def env_setup():
    os.environ["VIDEOS_TXT_TELEGRAM_BOT_TOKEN"] = "test:token"
    os.environ["VIDEOS_TXT_TELEGRAM_CHAT_ID"] = "-100123456789"
    os.environ["VIDEOS_TXT_TELEGRAM_ENABLED"] = "true"


@pytest.fixture
def sample_video(tmp_path) -> str:
    p = tmp_path / "output.mp4"
    p.write_bytes(b"faked mp4 content " * 100)
    return str(p)


@pytest.fixture
def sample_validation(tmp_path) -> str:
    p = tmp_path / "validation_report.json"
    p.write_text(json.dumps({"all_pass": True, "codec_check": {"pass": True}}))
    return str(p)


@pytest.fixture
def sample_metadata() -> dict:
    return {
        "title": "Test Video",
        "total_duration_seconds": 120,
        "scene_count": 5,
        "resolution": "1920x1080",
    }


# ── is_enabled ──────────────────────────────────────────────────────────


def test_is_enabled_true():
    os.environ["VIDEOS_TXT_TELEGRAM_ENABLED"] = "true"
    assert is_enabled() is True


def test_is_enabled_false():
    os.environ["VIDEOS_TXT_TELEGRAM_ENABLED"] = "false"
    assert is_enabled() is False


def test_is_enabled_not_set():
    assert is_enabled() is False


def test_is_enabled_case_insensitive():
    os.environ["VIDEOS_TXT_TELEGRAM_ENABLED"] = "TRUE"
    assert is_enabled() is True


# ── send_text (mocked urllib) ───────────────────────────────────────────


@patch("review_channels.telegram_channel.urllib.request.urlopen")
def test_send_text_success(mock_urlopen, env_setup):
    fake_resp = MagicMock()
    fake_resp.read.return_value = json.dumps({
        "ok": True,
        "result": {"message_id": 42},
    }).encode("utf-8")
    fake_resp.__enter__.return_value = fake_resp
    mock_urlopen.return_value = fake_resp

    result = send_text("hello world")

    assert result["ok"] is True
    assert result["message_id"] == 42


@patch("review_channels.telegram_channel.urllib.request.urlopen")
def test_send_text_truncates(mock_urlopen, env_setup):
    fake_resp = MagicMock()
    fake_resp.read.return_value = json.dumps({
        "ok": True,
        "result": {"message_id": 1},
    }).encode("utf-8")
    fake_resp.__enter__.return_value = fake_resp
    mock_urlopen.return_value = fake_resp

    long_text = "x" * 5000
    result = send_text(long_text)

    assert result["ok"] is True
    call_args = mock_urlopen.call_args
    assert call_args is not None
    data = call_args[0][0].data
    assert data is not None
    assert len(data) < 4500


# ── send_document (mocked requests) ─────────────────────────────────────


@patch("requests.post")
def test_send_document_success(mock_post, env_setup, sample_video):
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"ok": True, "result": {"message_id": 99}},
        raise_for_status=lambda: None,
    )

    result = send_document(sample_video, caption="Test video")

    assert result["ok"] is True
    assert result["message_id"] == 99
    assert result["file"] == "output.mp4"


@patch("requests.post")
def test_send_document_file_not_found(mock_post, env_setup):
    result = send_document("/nonexistent/file.mp4")

    assert result["ok"] is False
    assert "File not found" in result.get("error", "")


@patch("requests.post")
def test_send_document_too_large(mock_post, env_setup, tmp_path):
    huge = tmp_path / "huge.mp4"
    huge.write_bytes(b"x" * (51 * 1024 * 1024))

    result = send_document(str(huge))

    assert result["ok"] is False
    assert "too large" in result.get("error", "").lower()


@patch("requests.post")
def test_send_document_api_error(mock_post, env_setup, sample_video):
    mock_post.side_effect = RuntimeError("connection reset")

    result = send_document(sample_video)

    assert result["ok"] is False
    assert "connection reset" in result.get("error", "").lower()


# ── send_review_package ─────────────────────────────────────────────────


@patch("review_channels.telegram_channel.send_text")
@patch("review_channels.telegram_channel.send_document")
def test_review_package_success(
    mock_send_doc, mock_send_text,
    env_setup, sample_video, sample_validation, sample_metadata,
):
    mock_send_text.return_value = {"ok": True, "message_id": 1}
    mock_send_doc.side_effect = [
        {"ok": True, "message_id": 2, "file": "output.mp4"},
        {"ok": True, "message_id": 3, "file": "validation_report.json"},
    ]

    result = send_review_package(
        video_path=sample_video,
        validation_report_path=sample_validation,
        metadata=sample_metadata,
    )

    assert result["status"] == "success"
    assert result["provider"] == "telegram"
    assert "output.mp4" in result["sent_files"]
    assert "validation_report.json" in result["sent_files"]
    assert len(result["message_ids"]) >= 2
    assert result["started_at"] is not None
    assert result["finished_at"] is not None


@patch("review_channels.telegram_channel.send_text")
@patch("review_channels.telegram_channel.send_document")
def test_review_package_partial_failure(
    mock_send_doc, mock_send_text,
    env_setup, sample_video, sample_validation, sample_metadata,
):
    mock_send_text.return_value = {"ok": True, "message_id": 1}
    mock_send_doc.side_effect = [
        {"ok": True, "message_id": 2, "file": "output.mp4"},  # video ok
        {"ok": False, "error": "API error"},                    # validation fails
    ]

    result = send_review_package(
        video_path=sample_video,
        validation_report_path=sample_validation,
        metadata=sample_metadata,
    )

    assert result["status"] == "partial"
    assert "output.mp4" in result["sent_files"]
    assert len(result["warnings"]) > 0


@patch("review_channels.telegram_channel.send_text")
@patch("review_channels.telegram_channel.send_document")
def test_review_package_disabled(
    mock_send_doc, mock_send_text,
    sample_video, sample_metadata,
):
    os.environ["VIDEOS_TXT_TELEGRAM_ENABLED"] = "false"

    result = send_review_package(
        video_path=sample_video,
        metadata=sample_metadata,
    )

    assert result["status"] == "failed"
    assert "not enabled" in result["error"]["message"].lower()


@patch("review_channels.telegram_channel.send_text")
@patch("review_channels.telegram_channel.send_document")
def test_review_package_missing_video(
    mock_send_doc, mock_send_text,
    env_setup, sample_metadata,
):
    mock_send_text.return_value = {"ok": True, "message_id": 1}
    mock_send_doc.return_value = {"ok": False, "error": "File not found"}

    result = send_review_package(
        video_path="/nonexistent/output.mp4",
        metadata=sample_metadata,
    )

    assert result["status"] == "partial"
    assert "File not found" in str(result["warnings"])


@patch("review_channels.telegram_channel.send_text")
@patch("review_channels.telegram_channel.send_document")
def test_review_package_all_fail(
    mock_send_doc, mock_send_text,
    env_setup, sample_video, sample_metadata,
):
    mock_send_text.side_effect = RuntimeError("network error")
    mock_send_doc.return_value = {"ok": False, "error": "API down"}

    result = send_review_package(
        video_path=sample_video,
        metadata=sample_metadata,
    )

    assert result["status"] == "failed"
    assert len(result["warnings"]) > 0


@patch("review_channels.telegram_channel.send_text")
@patch("review_channels.telegram_channel.send_document")
def test_review_package_without_validation_report(
    mock_send_doc, mock_send_text,
    env_setup, sample_video, sample_metadata,
):
    mock_send_text.return_value = {"ok": True, "message_id": 1}
    mock_send_doc.return_value = {"ok": True, "message_id": 2, "file": "output.mp4"}

    result = send_review_package(
        video_path=sample_video,
        metadata=sample_metadata,
    )

    assert result["status"] == "success"
    assert "output.mp4" in result["sent_files"]
    assert len(result["sent_files"]) == 1


@patch("review_channels.telegram_channel.send_text")
@patch("review_channels.telegram_channel.send_document")
def test_review_package_no_token(
    mock_send_doc, mock_send_text,
    sample_video, sample_metadata,
):
    os.environ["VIDEOS_TXT_TELEGRAM_ENABLED"] = "true"

    result = send_review_package(
        video_path=sample_video,
        metadata=sample_metadata,
    )

    assert result["status"] == "failed"
    assert "token" in result["error"]["message"].lower()


@patch("review_channels.telegram_channel.send_text")
@patch("review_channels.telegram_channel.send_document")
def test_review_package_has_timestamps(
    mock_send_doc, mock_send_text,
    env_setup, sample_video, sample_metadata,
):
    mock_send_text.return_value = {"ok": True, "message_id": 1}
    mock_send_doc.return_value = {"ok": True, "message_id": 2, "file": "output.mp4"}

    result = send_review_package(
        video_path=sample_video,
        metadata=sample_metadata,
    )

    assert result["started_at"] is not None
    assert result["finished_at"] is not None
    assert "T" in result["started_at"]
    assert isinstance(result.get("duration_seconds"), (int, float))
    assert isinstance(result.get("duration_seconds"), (int, float))


@patch("review_channels.telegram_channel.send_text")
@patch("review_channels.telegram_channel.send_document")
def test_review_package_no_env_crash(
    mock_send_doc, mock_send_text,
    sample_video, sample_metadata,
):
    os.environ["VIDEOS_TXT_TELEGRAM_ENABLED"] = "true"

    result = send_review_package(
        video_path=sample_video,
        metadata=sample_metadata,
    )

    assert result["status"] == "failed"
    assert isinstance(result["error"], dict)
    assert "message" in result["error"]
    assert result["finished_at"] is not None


# ── send_video_to_chat ─────────────────────────────────────────────────


@patch("requests.post")
def test_td_01a_send_video_to_chat_success(mock_post, env_setup, sample_video):
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"ok": True, "result": {"message_id": 42}},
    )

    result = send_video_to_chat(
        chat_id=-100123456789,
        file_path=sample_video,
        caption="Pipeline output",
        token="test:token",
    )

    assert result["status"] == "success"
    assert result["message_id"] == 42
    mock_post.assert_called_once()
    url = mock_post.call_args[0][0]
    assert "sendVideo" in url


def test_td_01b_send_video_to_chat_file_not_found(env_setup):
    result = send_video_to_chat(
        chat_id=-100123456789,
        file_path="/nonexistent/file.mp4",
        token="test:token",
    )

    assert result["status"] == "error"
    assert "File not found" in result.get("error", "")


@patch("requests.post")
def test_td_01c_send_video_to_chat_api_error(mock_post, env_setup, sample_video):
    mock_post.side_effect = RuntimeError("API connection failed")

    result = send_video_to_chat(
        chat_id=-100123456789,
        file_path=sample_video,
        token="test:token",
    )

    assert result["status"] == "error"
    assert "API connection failed" in result.get("error", "")


@patch("requests.post")
def test_td_04a_send_video_to_chat_under_50mb(mock_post, env_setup, tmp_path):
    small_file = tmp_path / "small.mp4"
    small_file.write_bytes(b"\x00" * (10 * 1024 * 1024))  # 10 MB

    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"ok": True, "result": {"message_id": 1}},
    )

    result = send_video_to_chat(
        chat_id=-100123456789,
        file_path=str(small_file),
        token="test:token",
    )

    assert result["status"] == "success"
    mock_post.assert_called_once()


@patch("review_channels.telegram_channel._find_ffmpeg")
@patch("requests.post")
def test_td_04b_send_video_to_chat_over_50mb(mock_post, mock_ffmpeg, env_setup, tmp_path):
    large_file = tmp_path / "large.mp4"
    large_file.write_bytes(b"\x00" * (51 * 1024 * 1024))  # 51 MB

    mock_ffmpeg.return_value = None

    with pytest.raises(VideoTooLargeError, match="ffmpeg not found"):
        send_video_to_chat(
            chat_id=-100123456789,
            file_path=str(large_file),
            token="test:token",
        )
