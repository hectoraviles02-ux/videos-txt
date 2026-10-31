"""Tests for pipeline/review_integration.py"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from adapters.base import (
    NarrationInput,
    RenderConfig,
    RenderResult,
    RunContext,
    Storyboard,
    StoryboardScene,
    ValidationReport,
)
from pipeline.review_integration import (
    ReviewEvidence,
    is_technical_pass,
    submit_for_review,
)


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_pass_validation() -> ValidationReport:
    return ValidationReport(
        codec_check={"pass": True, "actual": "h264"},
        resolution_check={"pass": True, "actual": "1280x720"},
        fps_check={"pass": True, "actual": 30.0},
        duration_check={"pass": True, "actual": 60.0, "expected": 60.0},
        audio_check={"pass": True, "channels": 2, "sample_rate": 44100},
        file_integrity={"pass": True, "size_bytes": 50000},
        min_frame_check={"pass": True, "message": "OK"},
    )


def _make_fail_validation() -> ValidationReport:
    return ValidationReport(
        codec_check={"pass": False, "actual": "unknown"},
        resolution_check={"pass": False, "actual": "640x360"},
        fps_check={"pass": True, "actual": 30.0},
        duration_check={"pass": True, "actual": 60.0, "expected": 60.0},
        audio_check={"pass": True, "channels": 2, "sample_rate": 44100},
        file_integrity={"pass": True, "size_bytes": 50000},
        min_frame_check={"pass": True, "message": "OK"},
    )


def _make_success_result(validation: ValidationReport | None = ...) -> RenderResult:
    if validation is ...:
        validation = _make_pass_validation()
    return RenderResult(
        status="success",
        video_path="/fake/output.mp4",
        video_size_bytes=50000,
        video_duration_seconds=60.0,
        video_hash="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        actual_resolution="1280x720",
        actual_fps=30.0,
        validation=validation,
        engine_info={"name": "ffmpeg-pillow", "version": "0.1.0"},
        duration_seconds=12.5,
    )


def _make_failed_result() -> RenderResult:
    return RenderResult(
        status="failed",
        error={"message": "render crashed", "exception_type": "AdapterRenderError"},
        engine_info={"name": "ffmpeg-pillow", "version": "0.1.0"},
        duration_seconds=2.0,
    )


def _make_storyboard() -> Storyboard:
    return Storyboard(
        storyboard_version="1.0",
        metadata={
            "title": "Test Storyboard",
            "source_script": "abc",
            "total_duration_seconds": 60,
            "scene_count": 2,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        scenes=[
            StoryboardScene(
                id="scene-001", type="hook", title="Intro",
                start_seconds=0.0, end_seconds=15.0,
                narration="Intro text.",
                visual={"components": [{"type": "text", "content": "Hello"}]},
            ),
            StoryboardScene(
                id="scene-002", type="narrative", title="Body",
                start_seconds=15.0, end_seconds=60.0,
                narration="Body text.",
                visual={"components": [{"type": "solid_color", "color": "#000000"}]},
            ),
        ],
    )


# ── is_technical_pass ──────────────────────────────────────────────────


def test_technical_pass_success():
    result = _make_success_result()
    assert is_technical_pass(result) is True


def test_technical_pass_failed_status():
    result = _make_failed_result()
    assert is_technical_pass(result) is False


def test_technical_pass_no_validation():
    result = _make_success_result(validation=None)
    assert is_technical_pass(result) is False


def test_technical_pass_validation_fails():
    result = _make_success_result(validation=_make_fail_validation())
    assert is_technical_pass(result) is False


# ── submit_for_review ──────────────────────────────────────────────────


def test_submit_skips_when_not_technical_pass():
    result = _make_failed_result()
    evidence = submit_for_review(result)
    assert evidence.status == "skipped"
    assert "Not TECHNICAL_PASS" in evidence.warnings[0]


def test_submit_skips_when_disabled():
    result = _make_success_result()
    with patch("review_channels.telegram_channel.is_enabled", return_value=False):
        evidence = submit_for_review(result)
    assert evidence.status == "skipped"
    assert "not enabled" in evidence.warnings[0].lower()


@patch("review_channels.telegram_channel.send_review_package")
def test_submit_calls_send_review_package(mock_send, tmp_path):
    mock_send.return_value = {
        "status": "success",
        "message_ids": [100],
        "sent_files": ["output.mp4"],
        "warnings": [],
        "error": None,
    }
    result = _make_success_result()

    with patch("review_channels.telegram_channel.is_enabled", return_value=True):
        evidence = submit_for_review(result, run_id="test-run-001")

    assert evidence.status == "success"
    assert evidence.run_id == "test-run-001"
    assert evidence.provider == "telegram"
    assert 100 in evidence.message_ids
    mock_send.assert_called_once()


@patch("review_channels.telegram_channel.send_review_package")
def test_submit_includes_render_summary(mock_send, tmp_path):
    mock_send.return_value = {
        "status": "success", "message_ids": [], "sent_files": [], "warnings": [],
        "error": None,
    }
    result = _make_success_result()
    storyboard = _make_storyboard()

    with patch("review_channels.telegram_channel.is_enabled", return_value=True):
        evidence = submit_for_review(result, run_id="r-001", storyboard=storyboard)

    render = evidence.render
    assert render["duration_seconds"] == 60.0
    assert render["resolution"] == "1280x720"
    assert render["hash_short"] == "a1b2c3d4"
    assert render["hash_sha256"] == result.video_hash
    assert render["engine"]["name"] == "ffmpeg-pillow"
    assert render["scene_count"] == 2
    assert render["title"] == "Test Storyboard"


@patch("review_channels.telegram_channel.send_review_package")
def test_submit_handles_partial_delivery(mock_send):
    mock_send.return_value = {
        "status": "partial",
        "message_ids": [100],
        "sent_files": [],
        "warnings": ["Video too large for Telegram"],
        "error": None,
    }
    result = _make_success_result()

    with patch("review_channels.telegram_channel.is_enabled", return_value=True):
        evidence = submit_for_review(result, run_id="r-002")

    assert evidence.status == "partial"
    assert "Video too large" in str(evidence.warnings)


@patch("review_channels.telegram_channel.send_review_package")
def test_submit_uses_run_dir_for_validation(mock_send, tmp_path):
    mock_send.return_value = {
        "status": "success", "message_ids": [], "sent_files": [], "warnings": [],
        "error": None,
    }
    result = _make_success_result()
    run_dir = tmp_path / "runs" / "test-003"
    val_dir = run_dir / "validation"
    val_dir.mkdir(parents=True)
    (val_dir / "validation_report.json").write_text(
        json.dumps({"all_pass": True}), encoding="utf-8"
    )

    with patch("review_channels.telegram_channel.is_enabled", return_value=True):
        evidence = submit_for_review(result, run_id="test-003", run_dir=str(run_dir))

    assert evidence.status == "success"
    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args[1]
    assert "validation_report.json" in call_kwargs.get("validation_report_path", "")


@patch("review_channels.telegram_channel.send_review_package")
def test_submit_writes_validation_to_run_dir(mock_send, tmp_path):
    mock_send.return_value = {
        "status": "success", "message_ids": [], "sent_files": [], "warnings": [],
        "error": None,
    }
    result = _make_success_result()
    run_dir = tmp_path / "runs" / "test-004"

    with patch("review_channels.telegram_channel.is_enabled", return_value=True):
        evidence = submit_for_review(result, run_id="test-004", run_dir=str(run_dir))

    val_path = run_dir / "validation" / "validation_report.json"
    assert val_path.is_file()
    data = json.loads(val_path.read_text())
    assert data["codec_check"]["pass"] is True


@patch("review_channels.telegram_channel.send_review_package")
def test_submit_no_run_dir_still_works(mock_send):
    mock_send.return_value = {
        "status": "success", "message_ids": [1], "sent_files": [], "warnings": [],
        "error": None,
    }
    result = _make_success_result()

    with patch("review_channels.telegram_channel.is_enabled", return_value=True):
        evidence = submit_for_review(result, run_id="r-005")

    assert evidence.status == "success"


@patch("review_channels.telegram_channel.send_review_package")
def test_submit_with_narration(mock_send):
    mock_send.return_value = {
        "status": "success", "message_ids": [], "sent_files": [], "warnings": [],
        "error": None,
    }
    result = _make_success_result()
    narration = NarrationInput(
        audio_path="/fake/audio.wav", duration_seconds=60.0, source="tts"
    )

    with patch("review_channels.telegram_channel.is_enabled", return_value=True):
        evidence = submit_for_review(result, run_id="r-006", narration=narration)

    assert evidence.render["narration_source"] == "tts"
    assert evidence.render["narration_duration"] == 60.0


@patch("review_channels.telegram_channel.send_review_package")
def test_submit_does_not_touch_approval_states(mock_send):
    mock_send.return_value = {
        "status": "success", "message_ids": [], "sent_files": [], "warnings": [],
        "error": None,
    }
    result = _make_success_result()

    with patch("review_channels.telegram_channel.is_enabled", return_value=True):
        evidence = submit_for_review(result, run_id="r-007")

    assert "EDITORIAL_APPROVED" not in str(evidence.to_dict())
    assert "REJECTED" not in str(evidence.to_dict())
    assert "PUBLISHED" not in str(evidence.to_dict())


def test_short_hash():
    full = "abcdef1234567890"
    from pipeline.review_integration import _short_hash
    assert _short_hash(full) == "abcdef12"
    assert _short_hash(full, 4) == "abcd"
    assert _short_hash(None) == "N/A"
    assert _short_hash("") == "N/A"


def test_review_evidence_to_from_dict():
    ev = ReviewEvidence(
        at="2026-07-25T12:00:00",
        provider="telegram",
        status="success",
        run_id="r-001",
        message_ids=[100, 101],
        sent_files=["out.mp4"],
        render={"duration_seconds": 60.0},
    )
    d = ev.to_dict()
    assert d["type"] == "REVIEW_PACKAGE_SENT"
    assert d["status"] == "success"
    restored = ReviewEvidence.from_dict(d)
    assert restored.status == "success"
    assert restored.run_id == "r-001"
