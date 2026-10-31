"""Pruebas de validación del contrato del adaptador de video."""

import json
import os
from pathlib import Path

import pytest

from adapters.base import (
    AdapterValidationError,
    NarrationInput,
    RenderConfig,
    RenderResult,
    RunContext,
    Storyboard,
    StoryboardScene,
    ValidationReport,
    VideoAdapter,
)


class DummyAdapter(VideoAdapter):
    """Adaptador dummy para pruebas del contrato."""

    @property
    def name(self) -> str:
        return "dummy"

    @property
    def version(self) -> str:
        return "0.1.0"

    def render(self, storyboard, narration, config, context):
        raise NotImplementedError("Dummy adapter does not implement render")


# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def audio_file(tmp_path):
    p = tmp_path / "narration.wav"
    p.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
    return str(p)


def make_storyboard(scenes_override=None, metadata_override=None):
    return Storyboard(
        storyboard_version="1.0",
        metadata={
            "source_script": "abc123",
            "title": "Test Video",
            "total_duration_seconds": 60,
            "scene_count": 2,
            "generated_at": "2026-07-25T12:00:00Z",
            **(metadata_override or {}),
        },
        scenes=scenes_override
        or [
            StoryboardScene(
                id="scene-001",
                type="hook",
                title="Opening",
                start_seconds=0.0,
                end_seconds=15.0,
                narration="Texto de prueba.",
                visual={"components": [{"type": "text", "content": "Hola"}]},
            ),
            StoryboardScene(
                id="scene-002",
                type="narrative",
                title="Body",
                start_seconds=15.0,
                end_seconds=60.0,
                narration="Segundo texto de prueba.",
                visual={"components": [{"type": "solid_color", "color": "#FF2222"}]},
            ),
        ],
    )


def make_narration(audio_path: str, duration: float = 60.0, source: str = "tts"):
    return NarrationInput(
        audio_path=audio_path,
        duration_seconds=duration,
        source=source,
        tts_engine="piper",
    )


def make_default_config():
    return RenderConfig()


# ─── Pruebas de existencia del contrato ───


def test_adapter_is_abstract():
    with pytest.raises(TypeError):
        VideoAdapter()


def test_dummy_adapter_is_concrete():
    adapter = DummyAdapter()
    assert adapter.name == "dummy"
    assert adapter.version == "0.1.0"


def test_adapter_has_render_method():
    assert hasattr(VideoAdapter, "render")
    assert callable(VideoAdapter.render)


def test_adapter_has_deterministic_property():
    adapter = DummyAdapter()
    assert adapter.deterministic is True


# ─── Pruebas de validación de inputs (pasan) ───


def test_validate_inputs_passes_minimal(audio_file):
    adapter = DummyAdapter()
    adapter.validate_inputs(make_storyboard(), make_narration(audio_file), make_default_config())


# ─── Pruebas adversarias: storyboard structure ───


def test_validate_empty_storyboard_raises(audio_file):
    adapter = DummyAdapter()
    sb = Storyboard(storyboard_version="1.0", metadata={}, scenes=[])
    with pytest.raises(AdapterValidationError, match="at least one scene"):
        adapter.validate_inputs(sb, make_narration(audio_file), make_default_config())


def test_validate_missing_version(audio_file):
    adapter = DummyAdapter()
    sb = make_storyboard()
    sb.storyboard_version = ""
    with pytest.raises(AdapterValidationError, match="Storyboard version"):
        adapter.validate_inputs(sb, make_narration(audio_file), make_default_config())


def test_validate_scene_count_mismatch(audio_file):
    adapter = DummyAdapter()
    sb = make_storyboard(metadata_override={"scene_count": 5})
    with pytest.raises(AdapterValidationError, match="scene_count"):
        adapter.validate_inputs(sb, make_narration(audio_file), make_default_config())


def test_validate_total_duration_mismatch(audio_file):
    adapter = DummyAdapter()
    sb = make_storyboard(metadata_override={"total_duration_seconds": 999})
    with pytest.raises(AdapterValidationError, match="total_duration_seconds"):
        adapter.validate_inputs(sb, make_narration(audio_file), make_default_config())


def test_validate_empty_narration_in_scene(audio_file):
    adapter = DummyAdapter()
    sb = make_storyboard()
    sb.scenes[0].narration = "   "
    with pytest.raises(AdapterValidationError, match="narration is empty"):
        adapter.validate_inputs(sb, make_narration(audio_file), make_default_config())


def test_validate_scene_without_visual_components(audio_file):
    adapter = DummyAdapter()
    sb = make_storyboard()
    sb.scenes[0].visual = {"components": []}
    with pytest.raises(AdapterValidationError, match="at least one component"):
        adapter.validate_inputs(sb, make_narration(audio_file), make_default_config())


# ─── Pruebas adversarias: componentes visuales ───


def test_validate_image_component_without_source(audio_file):
    adapter = DummyAdapter()
    sb = make_storyboard()
    sb.scenes[0].visual = {"components": [{"type": "image"}]}
    with pytest.raises(AdapterValidationError, match="requires 'source'"):
        adapter.validate_inputs(sb, make_narration(audio_file), make_default_config())


def test_validate_video_clip_component_without_source(audio_file):
    adapter = DummyAdapter()
    sb = make_storyboard()
    sb.scenes[0].visual = {"components": [{"type": "video_clip"}]}
    with pytest.raises(AdapterValidationError, match="requires 'source'"):
        adapter.validate_inputs(sb, make_narration(audio_file), make_default_config())


def test_validate_solid_color_component_without_color(audio_file):
    adapter = DummyAdapter()
    sb = make_storyboard()
    sb.scenes[0].visual = {"components": [{"type": "solid_color"}]}
    with pytest.raises(AdapterValidationError, match="requires 'color'"):
        adapter.validate_inputs(sb, make_narration(audio_file), make_default_config())


def test_validate_text_component_without_content(audio_file):
    adapter = DummyAdapter()
    sb = make_storyboard()
    sb.scenes[0].visual = {"components": [{"type": "text"}]}
    with pytest.raises(AdapterValidationError, match="requires 'content'"):
        adapter.validate_inputs(sb, make_narration(audio_file), make_default_config())


def test_validate_code_component_without_content(audio_file):
    adapter = DummyAdapter()
    sb = make_storyboard()
    sb.scenes[0].visual = {"components": [{"type": "code"}]}
    with pytest.raises(AdapterValidationError, match="requires 'content'"):
        adapter.validate_inputs(sb, make_narration(audio_file), make_default_config())


# ─── Pruebas adversarias: continuidad de escenas ───


def test_validate_scene_overlap(audio_file):
    adapter = DummyAdapter()
    sb = make_storyboard()
    sb.scenes[1].start_seconds = 10.0  # overlaps scene-001 (0-15)
    with pytest.raises(AdapterValidationError, match="overlaps"):
        adapter.validate_inputs(sb, make_narration(audio_file), make_default_config())


def test_validate_scene_gap(audio_file):
    adapter = DummyAdapter()
    sb = make_storyboard()
    sb.scenes[1].start_seconds = 20.0  # 5s gap after scene-001 ends at 15
    with pytest.raises(AdapterValidationError, match="Gap"):
        adapter.validate_inputs(sb, make_narration(audio_file), make_default_config())


def test_validate_scene_timestamps_reversed(audio_file):
    adapter = DummyAdapter()
    sb = make_storyboard()
    sb.scenes[0].start_seconds = 10.0
    sb.scenes[0].end_seconds = 5.0
    with pytest.raises(AdapterValidationError, match="scene-001"):
        adapter.validate_inputs(sb, make_narration(audio_file), make_default_config())


# ─── Pruebas adversarias: narración ───


def test_validate_narration_duration_zero(audio_file):
    adapter = DummyAdapter()
    nar = make_narration(audio_file, duration=0)
    with pytest.raises(AdapterValidationError, match="Invalid narration duration"):
        adapter.validate_inputs(make_storyboard(), nar, make_default_config())


def test_validate_narration_invalid_source(audio_file):
    adapter = DummyAdapter()
    nar = make_narration(audio_file, source="unknown")
    with pytest.raises(AdapterValidationError, match="Invalid narration source"):
        adapter.validate_inputs(make_storyboard(), nar, make_default_config())


def test_validate_narration_file_not_found():
    adapter = DummyAdapter()
    nar = make_narration("/nonexistent/audio.wav")
    with pytest.raises(AdapterValidationError, match="audio file not found"):
        adapter.validate_inputs(make_storyboard(), nar, make_default_config())


def test_validate_narration_duration_mismatch(audio_file):
    adapter = DummyAdapter()
    nar = make_narration(audio_file, duration=10.0)  # storyboard ends at 60
    with pytest.raises(AdapterValidationError, match="Narration duration"):
        adapter.validate_inputs(make_storyboard(), nar, make_default_config())


# ─── Pruebas adversarias: configuración ───


def test_validate_unsupported_resolution(audio_file):
    adapter = DummyAdapter()
    config = RenderConfig(resolution="640x480")
    with pytest.raises(AdapterValidationError, match="Unsupported resolution"):
        adapter.validate_inputs(make_storyboard(), make_narration(audio_file), config)


def test_validate_unsupported_fps(audio_file):
    adapter = DummyAdapter()
    config = RenderConfig(fps=15)
    with pytest.raises(AdapterValidationError, match="Unsupported fps"):
        adapter.validate_inputs(make_storyboard(), make_narration(audio_file), config)


# ─── Pruebas de invariantes RenderResult ───


def make_passing_validation():
    return ValidationReport(
        codec_check={"pass": True, "actual": "h264"},
        resolution_check={"pass": True, "actual": "1920x1080"},
        fps_check={"pass": True, "actual": 30.0},
        duration_check={"pass": True, "actual": 60.0, "expected": 60.0},
        audio_check={"pass": True, "channels": 2, "sample_rate": 44100},
        file_integrity={"pass": True, "hash_match": True},
        min_frame_check={"pass": True, "message": "OK"},
    )


def test_render_result_success_validates_ok():
    result = RenderResult(
        status="success",
        video_path="/out/video.mp4",
        video_size_bytes=1024,
        video_duration_seconds=60.0,
        video_hash="abc123",
        actual_resolution="1920x1080",
        actual_fps=30.0,
        validation=make_passing_validation(),
        engine_info={"name": "dummy", "version": "0.1.0"},
        duration_seconds=5.0,
    )
    result.validate()  # no raise


def test_render_result_success_missing_video_path():
    result = RenderResult(
        status="success",
        validation=make_passing_validation(),
        engine_info={"name": "dummy", "version": "0.1.0"},
    )
    with pytest.raises(AdapterValidationError, match="video_path"):
        result.validate()


def test_render_result_success_missing_video_hash():
    result = RenderResult(
        status="success",
        video_path="/out/video.mp4",
        video_size_bytes=1024,
        validation=make_passing_validation(),
        engine_info={"name": "dummy", "version": "0.1.0"},
    )
    with pytest.raises(AdapterValidationError, match="video_hash"):
        result.validate()


def test_render_result_success_missing_validation():
    result = RenderResult(
        status="success",
        video_path="/out/video.mp4",
        video_size_bytes=1024,
        video_hash="abc",
        engine_info={"name": "dummy", "version": "0.1.0"},
    )
    with pytest.raises(AdapterValidationError, match="validation"):
        result.validate()


def test_render_result_success_with_error_set():
    result = RenderResult(
        status="success",
        video_path="/out/video.mp4",
        video_size_bytes=1024,
        video_hash="abc",
        validation=make_passing_validation(),
        error={"message": "should not be here"},
        engine_info={"name": "dummy", "version": "0.1.0"},
    )
    with pytest.raises(AdapterValidationError, match="error is not None"):
        result.validate()


def test_render_result_success_failing_validation():
    v = make_passing_validation()
    v.codec_check = {"pass": False, "actual": "unknown"}
    result = RenderResult(
        status="success",
        video_path="/out/video.mp4",
        video_size_bytes=1024,
        video_hash="abc",
        validation=v,
        engine_info={"name": "dummy", "version": "0.1.0"},
    )
    with pytest.raises(AdapterValidationError, match="validation checks failed"):
        result.validate()


def test_render_result_failed_requires_error():
    result = RenderResult(status="failed")
    with pytest.raises(AdapterValidationError, match="error is None"):
        result.validate()


def test_render_result_failed_requires_error_message():
    result = RenderResult(status="failed", error={"code": 42})
    with pytest.raises(AdapterValidationError, match="'message'"):
        result.validate()


def test_render_result_failed_must_not_have_video():
    result = RenderResult(
        status="failed",
        error={"message": "render exploded"},
        video_path="/out/video.mp4",
    )
    with pytest.raises(AdapterValidationError, match="video_path is not None"):
        result.validate()


def test_render_result_invalid_status():
    result = RenderResult(status="maybe")
    with pytest.raises(AdapterValidationError, match="Invalid RenderResult status"):
        result.validate()


# ─── Pruebas de estructura de datos ───


def test_storyboard_from_dict():
    data = {
        "storyboard_version": "1.0",
        "metadata": {
            "source_script": "abc",
            "title": "Test",
            "total_duration_seconds": 30,
            "scene_count": 1,
            "generated_at": "2026-07-25T12:00:00Z",
        },
        "scenes": [
            {
                "id": "scene-001",
                "type": "hook",
                "title": "Gancho",
                "start_seconds": 0.0,
                "end_seconds": 15.0,
                "narration": "Texto.",
                "visual": {"components": [{"type": "text", "content": "Hola"}]},
            }
        ],
    }
    sb = Storyboard.from_dict(data)
    assert sb.storyboard_version == "1.0"
    assert len(sb.scenes) == 1
    assert sb.scenes[0].id == "scene-001"


def test_storyboard_scene_defaults():
    scene = StoryboardScene(
        id="scene-001",
        type="hook",
        title="Test",
        start_seconds=0.0,
        end_seconds=10.0,
        narration="Text",
        visual={"components": []},
    )
    assert scene.notes == ""
    assert scene.source_refs == []


def test_render_config_defaults():
    config = RenderConfig()
    assert config.resolution == "1920x1080"
    assert config.fps == 30
    assert config.background_music is None
    assert config.transition == "fade"


def test_run_context():
    ctx = RunContext(
        run_id="20260725T120000Z_abc123",
        output_dir="/runs/abc123/render",
        log_dir="/runs/abc123/logs",
    )
    assert ctx.run_id == "20260725T120000Z_abc123"


# ─── Pruebas de schema JSON ───


def _load_schema():
    path = Path(__file__).parents[1] / "schemas" / "storyboard.schema.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_storyboard_schema_is_valid():
    schema = _load_schema()
    assert schema["$schema"] == "http://json-schema.org/draft-07/schema#"
    assert "properties" in schema
    assert "scenes" in schema["properties"]
    assert "storyboard_version" in schema["required"]


def test_storyboard_schema_validates_minimal():
    import jsonschema

    schema = _load_schema()
    valid = {
        "storyboard_version": "1.0",
        "metadata": {
            "source_script": "abc123",
            "title": "Test Video",
            "total_duration_seconds": 60,
            "scene_count": 1,
            "generated_at": "2026-07-25T12:00:00Z",
        },
        "scenes": [
            {
                "id": "scene-001",
                "type": "hook",
                "title": "Opening",
                "start_seconds": 0.0,
                "end_seconds": 15.0,
                "narration": "Texto de prueba.",
                "visual": {"components": [{"type": "text", "content": "Hola"}]},
            }
        ],
    }
    jsonschema.validate(instance=valid, schema=schema)


def test_storyboard_schema_rejects_empty_scenes():
    import jsonschema

    schema = _load_schema()
    invalid = {
        "storyboard_version": "1.0",
        "metadata": {
            "source_script": "abc123",
            "title": "Test",
            "total_duration_seconds": 60,
            "scene_count": 0,
            "generated_at": "2026-07-25T12:00:00Z",
        },
        "scenes": [],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=invalid, schema=schema)


def test_storyboard_schema_rejects_missing_required():
    import jsonschema

    schema = _load_schema()
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance={"storyboard_version": "1.0"}, schema=schema)


def test_schema_additional_properties_on_scene():
    """Scene object rejects unknown keys via additionalProperties: false."""
    import jsonschema

    schema = _load_schema()
    invalid = {
        "storyboard_version": "1.0",
        "metadata": {
            "source_script": "abc",
            "title": "T",
            "total_duration_seconds": 10,
            "scene_count": 1,
            "generated_at": "2026-07-25T12:00:00Z",
        },
        "scenes": [
            {
                "id": "scene-001",
                "type": "hook",
                "title": "T",
                "start_seconds": 0.0,
                "end_seconds": 10.0,
                "narration": "Text",
                "visual": {"components": [{"type": "text", "content": "Hi"}]},
                "unknown_field": "should be rejected",
            }
        ],
    }
    with pytest.raises(jsonschema.ValidationError, match="unknown_field"):
        jsonschema.validate(instance=invalid, schema=schema)


def test_schema_additional_properties_on_visual():
    import jsonschema

    schema = _load_schema()
    invalid = {
        "storyboard_version": "1.0",
        "metadata": {
            "source_script": "abc",
            "title": "T",
            "total_duration_seconds": 10,
            "scene_count": 1,
            "generated_at": "2026-07-25T12:00:00Z",
        },
        "scenes": [
            {
                "id": "scene-001",
                "type": "hook",
                "title": "T",
                "start_seconds": 0.0,
                "end_seconds": 10.0,
                "narration": "Text",
                "visual": {
                    "components": [{"type": "text", "content": "Hi"}],
                    "bogus": True,
                },
            }
        ],
    }
    with pytest.raises(jsonschema.ValidationError, match="bogus"):
        jsonschema.validate(instance=invalid, schema=schema)


def test_schema_additional_properties_on_component():
    import jsonschema

    schema = _load_schema()
    invalid = {
        "storyboard_version": "1.0",
        "metadata": {
            "source_script": "abc",
            "title": "T",
            "total_duration_seconds": 10,
            "scene_count": 1,
            "generated_at": "2026-07-25T12:00:00Z",
        },
        "scenes": [
            {
                "id": "scene-001",
                "type": "hook",
                "title": "T",
                "start_seconds": 0.0,
                "end_seconds": 10.0,
                "narration": "Text",
                "visual": {
                    "components": [{"type": "text", "content": "Hi", "wat": "nope"}]
                },
            }
        ],
    }
    with pytest.raises(jsonschema.ValidationError, match="wat"):
        jsonschema.validate(instance=invalid, schema=schema)


# ─── Pruebas de requisitos condicionales en schema ───


def test_schema_image_requires_source():
    import jsonschema

    schema = _load_schema()
    invalid = {
        "storyboard_version": "1.0",
        "metadata": {
            "source_script": "abc",
            "title": "T",
            "total_duration_seconds": 10,
            "scene_count": 1,
            "generated_at": "2026-07-25T12:00:00Z",
        },
        "scenes": [
            {
                "id": "scene-001",
                "type": "hook",
                "title": "T",
                "start_seconds": 0.0,
                "end_seconds": 10.0,
                "narration": "Text",
                "visual": {"components": [{"type": "image"}]},
            }
        ],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=invalid, schema=schema)


def test_schema_solid_color_requires_color():
    import jsonschema

    schema = _load_schema()
    invalid = {
        "storyboard_version": "1.0",
        "metadata": {
            "source_script": "abc",
            "title": "T",
            "total_duration_seconds": 10,
            "scene_count": 1,
            "generated_at": "2026-07-25T12:00:00Z",
        },
        "scenes": [
            {
                "id": "scene-001",
                "type": "hook",
                "title": "T",
                "start_seconds": 0.0,
                "end_seconds": 10.0,
                "narration": "Text",
                "visual": {"components": [{"type": "solid_color"}]},
            }
        ],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=invalid, schema=schema)


def test_schema_text_requires_content():
    import jsonschema

    schema = _load_schema()
    invalid = {
        "storyboard_version": "1.0",
        "metadata": {
            "source_script": "abc",
            "title": "T",
            "total_duration_seconds": 10,
            "scene_count": 1,
            "generated_at": "2026-07-25T12:00:00Z",
        },
        "scenes": [
            {
                "id": "scene-001",
                "type": "hook",
                "title": "T",
                "start_seconds": 0.0,
                "end_seconds": 10.0,
                "narration": "Text",
                "visual": {"components": [{"type": "text"}]},
            }
        ],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=invalid, schema=schema)


# ─── Pruebas de documentación ───


def test_contract_document_exists():
    assert (Path(__file__).parents[1] / "docs" / "CONTRATO_ADAPTADOR_VIDEO.md").exists()


def test_base_adapter_exists():
    assert (Path(__file__).parents[1] / "adapters" / "base.py").exists()


def test_init_module_exists():
    assert (Path(__file__).parents[1] / "adapters" / "__init__.py").exists()
