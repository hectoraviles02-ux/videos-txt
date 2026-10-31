"""Tests for telegram_bot/processors.py"""

from unittest.mock import MagicMock, patch

import pytest

from telegram_bot.processors import (
    extract_article,
    extract_video,
    load_json,
    parse_srt,
    validate_storyboard,
)


@patch.dict("sys.modules", {"procesar_video": MagicMock(procesar=MagicMock(return_value={"estado": "ok", "metodo": "youtube", "json": "/tmp/out.json"}))})
def test_extract_video():
    result = extract_video("https://youtube.com/watch?v=abc", "/raw", "/proc")
    assert result["estado"] == "ok"


@patch.dict("sys.modules", {"procesar_texto": MagicMock(procesar=MagicMock(return_value={"estado": "ok", "metodo": "article", "json": "/tmp/out.json"}))})
def test_extract_article():
    result = extract_article("https://example.com/article", "/raw", "/proc")
    assert result["estado"] == "ok"


@patch.dict("sys.modules", {"procesar_srt": MagicMock(parse_srt=MagicMock(return_value=[{"texto": "Hola", "inicio_seg": 0.0}]))})
def test_parse_srt():
    result = parse_srt("/tmp/file.srt")
    assert len(result) == 1
    assert result[0]["texto"] == "Hola"


def test_validate_storyboard_valid():
    data = {
        "scenes": [
            {"narration": "Hello"},
            {"narration": "World"},
        ]
    }
    assert validate_storyboard(data) == []


def test_validate_storyboard_missing_scenes():
    assert validate_storyboard({}) != []


def test_validate_storyboard_empty_scenes():
    assert validate_storyboard({"scenes": []}) != []


def test_load_json(tmp_path):
    p = tmp_path / "test.json"
    p.write_text('{"key": "value"}', encoding="utf-8")
    assert load_json(str(p)) == {"key": "value"}
