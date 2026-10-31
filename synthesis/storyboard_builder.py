from __future__ import annotations

import logging
from typing import Any

from adapters.base import Storyboard
from synthesis.script_generator import ScriptGenerator

logger = logging.getLogger(__name__)

COLOR_PALETTE: list[str] = [
    "#0f0f23",
    "#1a1a3e",
    "#16213e",
    "#0a0a1a",
    "#1e1e2f",
    "#2d2d44",
    "#1a1a2e",
    "#0f3460",
]


def build_from_script(
    script_json: dict,
    *,
    total_duration_seconds: int | None = None,
    generator: str = "ollama-qwen2.5:14b",
) -> Storyboard:
    """Convert ScriptGenerator output into a Storyboard with uniform timing.

    Duration is distributed evenly across scenes; the remainder (due to
    floating-point truncation) goes to the last scene.

    When *total_duration_seconds* is ``None`` (default), the duration is
    estimated from the narration text (~2.5 words/sec, 150 wpm Spanish
    speech rate) plus 1 s per scene for natural pauses.
    """
    scenes_in = script_json.get("scenes", [])
    n = len(scenes_in)
    if n == 0:
        raise ValueError("Script must contain at least one scene")

    if total_duration_seconds is None:
        total_words = sum(
            len(s.get("narration", "").split()) for s in scenes_in
        )
        # 2.5 words/sec = ~150 wpm, velocidad de habla normal en español.
        # + n (1s por escena) para transición visual — estimación sin validar
        # con datos reales, ajustable si las métricas muestran desfase.
        total_duration_seconds = max(int(total_words / 2.5) + n, 10)

    duration_per_scene = total_duration_seconds / n
    scenes_out: list[dict[str, Any]] = []
    for i, scene_data in enumerate(scenes_in):
        start = duration_per_scene * i
        if i == n - 1:
            dur = total_duration_seconds - duration_per_scene * (n - 1)
        else:
            dur = duration_per_scene
        end = start + dur

        color = COLOR_PALETTE[i % len(COLOR_PALETTE)]
        visual_description = scene_data.get("visual_description", "")
        search_keywords = scene_data.get("search_keywords", "")
        narration = scene_data.get("narration", "")

        scene_dict: dict[str, Any] = {
            "id": f"scene-{i + 1:03d}",
            "type": "narrative",
            "title": f"Scene {i + 1}",
            "start_seconds": round(start, 2),
            "end_seconds": round(end, 2),
            "narration": narration,
            "visual": {
                "description": visual_description,
                "search_keywords": search_keywords,
                "background_color": color,
                "components": [
                    {"type": "solid_color", "color": color},
                ],
            },
            "notes": "",
            "source_refs": [],
        }
        scenes_out.append(scene_dict)

    total_dur = sum(
        s["end_seconds"] - s["start_seconds"] for s in scenes_out
    )

    storyboard_dict: dict[str, Any] = {
        "storyboard_version": "1.0",
        "metadata": {
            "source_script": generator,
            "title": script_json.get("title", "Untitled"),
            "total_duration_seconds": round(total_dur, 2),
            "scene_count": n,
        },
        "scenes": scenes_out,
    }

    return Storyboard.from_dict(storyboard_dict)


def validate(storyboard_dict: dict) -> list[dict]:
    """Validate a storyboard dict.

    Checks field presence and types. Returns a list of error dicts with
    ``field`` and ``message`` keys. Empty list means valid.
    """
    errors: list[dict] = []

    scenes = storyboard_dict.get("scenes")
    if not isinstance(scenes, list) or len(scenes) == 0:
        errors.append({
            "field": "scenes",
            "message": "scenes must be a non-empty list",
        })
        return errors

    for i, scene in enumerate(scenes):
        prefix = f"scenes[{i}]"

        sid = scene.get("scene_id", scene.get("id"))
        if sid is None:
            errors.append({
                "field": f"{prefix}.id",
                "message": "Missing scene identifier (scene_id or id)",
            })

        duration = scene.get("duration")
        if duration is None and "start_seconds" in scene and "end_seconds" in scene:
            duration = scene["end_seconds"] - scene["start_seconds"]
        if duration is None or not isinstance(duration, (int, float)) or duration <= 0:
            errors.append({
                "field": f"{prefix}.duration",
                "message": "Duration must be > 0 "
                "(as 'duration' or 'start_seconds'/'end_seconds')",
            })

        narration = scene.get(
            "narration_text",
            scene.get("narration", scene.get("tts_text", "")),
        )
        if not isinstance(narration, str) or not narration.strip():
            errors.append({
                "field": f"{prefix}.narration",
                "message": "Non-empty narration string required",
            })

        visual_desc = scene.get("visual_description")
        if visual_desc and isinstance(visual_desc, str) and visual_desc.strip():
            continue
        visual = scene.get("visual")
        if not isinstance(visual, dict) or not visual:
            errors.append({
                "field": f"{prefix}.visual",
                "message": "Missing visual_description or visual dict",
            })

    return errors


def process(
    extracted_content: dict,
    generator: ScriptGenerator | None = None,
) -> Storyboard:
    """End-to-end: extract content type, generate script, build storyboard.

    Extracted content type detection (in priority order):
      ``"transcript"`` / ``"article"``
        → run ``generator.generate()`` then ``build_from_script()``
      ``"storyboard"``
        → validate and wrap via ``Storyboard.from_dict()``
      no type → defaults to ``"article"``
    """
    content_type = (
        extracted_content.get("type") or extracted_content.get("tipo")
    )
    if content_type is None:
        content_type = "article"
    elif content_type == "video":
        content_type = "transcript"
    elif content_type == "texto":
        content_type = "article"

    if content_type in ("transcript", "article"):
        gen = generator or ScriptGenerator()
        script = gen.generate(extracted_content)
        return build_from_script(script)

    if content_type == "storyboard":
        errs = validate(extracted_content)
        if errs:
            msg = "; ".join(e["message"] for e in errs)
            raise ValueError(f"Invalid storyboard: {msg}")
        return Storyboard.from_dict(extracted_content)

    raise ValueError(f"Unknown content type: {content_type!r}")
