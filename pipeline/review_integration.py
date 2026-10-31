"""Integration point: Render → Quality Gate → Telegram → Evidence.

Connects the FFmpeg adapter output (RenderResult) to the Telegram review
channel. This is the bridge between FASE 7 (adapter) and FASE 8 (Telegram).

Flow:
  Storyboard → Render (FFmpegPillowAdapter) → TECHNICAL_PASS? → submit_for_review → REVIEW_PACKAGE_SENT

This module does NOT modify EDITORIAL_APPROVED, REJECTED or PUBLISHED.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adapters.base import NarrationInput, RenderResult, Storyboard


@dataclass
class ReviewEvidence:
    type: str = "REVIEW_PACKAGE_SENT"
    at: str = ""
    provider: str = ""
    status: str = ""
    run_id: str = ""
    message_ids: list[int] = field(default_factory=list)
    sent_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    render: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "at": self.at,
            "provider": self.provider,
            "status": self.status,
            "run_id": self.run_id,
            "message_ids": self.message_ids,
            "sent_files": self.sent_files,
            "warnings": self.warnings,
            "render": self.render,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ReviewEvidence:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def _short_hash(full_hash: str | None, length: int = 8) -> str:
    if not full_hash or len(full_hash) < length:
        return full_hash or "N/A"
    return full_hash[:length]


def _build_render_summary(
    result: RenderResult,
    storyboard: Storyboard | None = None,
    narration: NarrationInput | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "duration_seconds": result.video_duration_seconds or 0.0,
        "resolution": result.actual_resolution or "N/A",
        "fps": result.actual_fps or 0,
        "video_size_bytes": result.video_size_bytes or 0,
        "hash_sha256": result.video_hash or "",
        "hash_short": _short_hash(result.video_hash),
        "render_time_seconds": result.duration_seconds,
        "engine": result.engine_info,
    }
    if storyboard:
        summary["storyboard_version"] = storyboard.storyboard_version
        summary["scene_count"] = len(storyboard.scenes)
        meta = storyboard.metadata or {}
        summary["title"] = meta.get("title", "")
        summary["total_duration_sb"] = meta.get("total_duration_seconds", 0)
    if narration:
        summary["narration_source"] = narration.source
        summary["narration_duration"] = narration.duration_seconds
    return summary


def is_technical_pass(result: RenderResult) -> bool:
    """TECHNICAL_PASS when status=success AND all validation checks pass."""
    if result.status != "success":
        return False
    if result.validation is None:
        return False
    return result.validation.all_pass


def submit_for_review(
    result: RenderResult,
    *,
    run_id: str | None = None,
    storyboard: Storyboard | None = None,
    narration: NarrationInput | None = None,
    run_dir: str | None = None,
) -> ReviewEvidence:
    """Submit a render result for human review via Telegram.

    Checks for TECHNICAL_PASS first. If the render failed or validation
    didn't pass, returns evidence with status="skipped" instead of sending.

    Returns a ReviewEvidence dataclass that can be stored in the run manifest.

    This function does NOT touch EDITORIAL_APPROVED, REJECTED or PUBLISHED.
    """
    from review_channels.telegram_channel import (
        is_enabled,
        send_review_package,
        send_validation_report,
    )

    now = datetime.now(timezone.utc)
    evidence = ReviewEvidence(
        at=now.isoformat(),
        provider="telegram",
        run_id=run_id or "unknown",
    )

    if not is_technical_pass(result):
        evidence.status = "skipped"
        evidence.warnings.append(
            f"Not TECHNICAL_PASS: status={result.status}, "
            f"validation={'missing' if result.validation is None else 'checks_failed'}"
        )
        return evidence

    if not is_enabled():
        evidence.status = "skipped"
        evidence.warnings.append("Telegram channel not enabled")
        return evidence

    evidence.render = _build_render_summary(result, storyboard, narration)

    validation_path = None
    if run_dir:
        validation_dir = Path(run_dir) / "validation"
        report = validation_dir / "validation_report.json"
        if report.is_file():
            validation_path = str(report)
        elif result.validation:
            validation_dir.mkdir(parents=True, exist_ok=True)
            v_dict = {
                "codec_check": result.validation.codec_check,
                "resolution_check": result.validation.resolution_check,
                "fps_check": result.validation.fps_check,
                "duration_check": result.validation.duration_check,
                "audio_check": result.validation.audio_check,
                "file_integrity": result.validation.file_integrity,
                "min_frame_check": result.validation.min_frame_check,
            }
            report.write_text(json.dumps(v_dict, indent=2), encoding="utf-8")
            validation_path = str(report)

    pkg_result = send_review_package(
        video_path=result.video_path or "",
        validation_report_path=validation_path,
        metadata={
            "title": evidence.render.get("title", "Untitled"),
            "total_duration_seconds": evidence.render.get("duration_seconds", 0),
            "scene_count": evidence.render.get("scene_count", 0),
            "resolution": evidence.render.get("resolution", "N/A"),
            "run_id": run_id or "unknown",
            "hash_short": evidence.render.get("hash_short", "N/A"),
        },
    )

    evidence.status = pkg_result.get("status", "failed")
    evidence.message_ids = pkg_result.get("message_ids", [])
    evidence.sent_files = pkg_result.get("sent_files", [])
    evidence.warnings.extend(pkg_result.get("warnings", []))

    if pkg_result.get("error"):
        evidence.warnings.append(
            f"Delivery error: {pkg_result['error'].get('message', 'unknown')}"
        )

    return evidence
