import os
from pathlib import Path

import pytest
from PIL import Image

from adapters.base import AdapterValidationError, StoryboardScene, UnsupportedComponentError
from adapters.scene_renderer import SceneRenderer


@pytest.fixture
def renderer():
    return SceneRenderer(resolution=(640, 360))


@pytest.fixture
def tmp_output(tmp_path):
    return tmp_path / "output.png"


def test_render_solid_color_fills_frame(renderer, tmp_output):
    scene = StoryboardScene(
        id="scene-001", type="hook", title="Test",
        start_seconds=0.0, end_seconds=10.0,
        narration="Test",
        visual={"components": [{"type": "solid_color", "color": "#FF0000"}]},
    )
    renderer.render_scene(scene, str(tmp_output))
    img = Image.open(tmp_output)
    assert img.size == (640, 360)
    assert img.getpixel((10, 10)) == (255, 0, 0)


def test_render_solid_color_default_black(renderer, tmp_output):
    scene = StoryboardScene(
        id="scene-001", type="hook", title="Test",
        start_seconds=0.0, end_seconds=10.0,
        narration="Test",
        visual={"components": [{"type": "solid_color"}]},
    )
    renderer.render_scene(scene, str(tmp_output))
    img = Image.open(tmp_output)
    assert img.getpixel((10, 10)) == (0, 0, 0)


def test_render_text_center_default(renderer, tmp_output):
    scene = StoryboardScene(
        id="scene-001", type="hook", title="Test",
        start_seconds=0.0, end_seconds=10.0,
        narration="Test",
        visual={
            "components": [
                {"type": "solid_color", "color": "#000000"},
                {"type": "text", "content": "Hello World", "position": "center"},
            ]
        },
    )
    renderer.render_scene(scene, str(tmp_output))
    img = Image.open(tmp_output)
    assert img.size == (640, 360)


def test_render_text_custom_color(renderer, tmp_output):
    scene = StoryboardScene(
        id="scene-001", type="hook", title="Test",
        start_seconds=0.0, end_seconds=10.0,
        narration="Test",
        visual={
            "components": [
                {"type": "solid_color", "color": "#FFFFFF"},
                {"type": "text", "content": "Red", "color": "#FF0000", "position": "center"},
            ]
        },
    )
    renderer.render_scene(scene, str(tmp_output))
    img = Image.open(tmp_output)
    assert img.size == (640, 360)


def test_render_text_positions(renderer, tmp_output):
    for pos in ("top", "bottom", "left", "right", "top-left", "top-right", "bottom-left", "bottom-right"):
        scene = StoryboardScene(
            id="scene-001", type="hook", title="Test",
            start_seconds=0.0, end_seconds=10.0,
            narration="Test",
            visual={
                "components": [
                    {"type": "solid_color", "color": "#000000"},
                    {"type": "text", "content": "Pos", "position": pos},
                ]
            },
        )
        p = str(tmp_output.parent / f"pos_{pos.replace('-', '_')}.png")
        renderer.render_scene(scene, p)
        assert Image.open(p).size == (640, 360)


def test_render_text_empty_content_does_not_crash(renderer, tmp_output):
    scene = StoryboardScene(
        id="scene-001", type="hook", title="Test",
        start_seconds=0.0, end_seconds=10.0,
        narration="Test",
        visual={
            "components": [
                {"type": "solid_color", "color": "#000000"},
                {"type": "text", "content": ""},
            ]
        },
    )
    renderer.render_scene(scene, str(tmp_output))
    assert Image.open(tmp_output).size == (640, 360)


def test_render_image_valid(renderer, tmp_output, tmp_path):
    img_path = tmp_path / "test_img.png"
    Image.new("RGB", (200, 150), (50, 100, 200)).save(img_path)
    scene = StoryboardScene(
        id="scene-001", type="hook", title="Test",
        start_seconds=0.0, end_seconds=10.0,
        narration="Test",
        visual={
            "components": [
                {"type": "image", "source": str(img_path)},
            ]
        },
    )
    renderer.render_scene(scene, str(tmp_output))
    img = Image.open(tmp_output)
    assert img.size == (640, 360)


def test_render_image_with_overlay(renderer, tmp_output, tmp_path):
    img_path = tmp_path / "test_img.png"
    Image.new("RGB", (200, 150), (50, 100, 200)).save(img_path)
    scene = StoryboardScene(
        id="scene-001", type="hook", title="Test",
        start_seconds=0.0, end_seconds=10.0,
        narration="Test",
        visual={
            "components": [
                {"type": "image", "source": str(img_path), "overlay_text": "Caption"},
            ]
        },
    )
    renderer.render_scene(scene, str(tmp_output))
    assert Image.open(tmp_output).size == (640, 360)


def test_render_multiple_components(renderer, tmp_output, tmp_path):
    img_path = tmp_path / "test_img.png"
    Image.new("RGB", (100, 100), (200, 200, 200)).save(img_path)
    scene = StoryboardScene(
        id="scene-001", type="hook", title="Test",
        start_seconds=0.0, end_seconds=10.0,
        narration="Test",
        visual={
            "components": [
                {"type": "solid_color", "color": "#1a1a2e"},
                {"type": "image", "source": str(img_path), "position": "center"},
                {"type": "text", "content": "Title", "position": "top"},
            ]
        },
    )
    renderer.render_scene(scene, str(tmp_output))
    assert Image.open(tmp_output).size == (640, 360)


def test_reject_invalid_color(renderer, tmp_output):
    scene = StoryboardScene(
        id="scene-001", type="hook", title="Test",
        start_seconds=0.0, end_seconds=10.0,
        narration="Test",
        visual={"components": [{"type": "solid_color", "color": "not-a-color"}]},
    )
    with pytest.raises(AdapterValidationError, match="Invalid color"):
        renderer.render_scene(scene, str(tmp_output))


def test_reject_invalid_hex_color(renderer, tmp_output):
    scene = StoryboardScene(
        id="scene-001", type="hook", title="Test",
        start_seconds=0.0, end_seconds=10.0,
        narration="Test",
        visual={"components": [{"type": "solid_color", "color": "#GGG000"}]},
    )
    with pytest.raises(AdapterValidationError, match="Invalid color"):
        renderer.render_scene(scene, str(tmp_output))


def test_reject_image_missing_source(renderer, tmp_output):
    scene = StoryboardScene(
        id="scene-001", type="hook", title="Test",
        start_seconds=0.0, end_seconds=10.0,
        narration="Test",
        visual={"components": [{"type": "image", "source": "/nonexistent/img.png"}]},
    )
    with pytest.raises(AdapterValidationError, match="Image file not found"):
        renderer.render_scene(scene, str(tmp_output))


def test_reject_image_empty_source(renderer, tmp_output):
    scene = StoryboardScene(
        id="scene-001", type="hook", title="Test",
        start_seconds=0.0, end_seconds=10.0,
        narration="Test",
        visual={"components": [{"type": "image", "source": ""}]},
    )
    with pytest.raises(AdapterValidationError, match="requires 'source'"):
        renderer.render_scene(scene, str(tmp_output))


def test_reject_unsupported_video_clip(renderer, tmp_output):
    scene = StoryboardScene(
        id="scene-001", type="hook", title="Test",
        start_seconds=0.0, end_seconds=10.0,
        narration="Test",
        visual={"components": [{"type": "video_clip", "source": "clip.mp4"}]},
    )
    with pytest.raises(UnsupportedComponentError, match="Unsupported component type"):
        renderer.render_scene(scene, str(tmp_output))


def test_reject_unsupported_animation(renderer, tmp_output):
    scene = StoryboardScene(
        id="scene-001", type="hook", title="Test",
        start_seconds=0.0, end_seconds=10.0,
        narration="Test",
        visual={"components": [{"type": "animation"}]},
    )
    with pytest.raises(UnsupportedComponentError, match="Unsupported component type"):
        renderer.render_scene(scene, str(tmp_output))


def test_reject_unsupported_slide(renderer, tmp_output):
    scene = StoryboardScene(
        id="scene-001", type="hook", title="Test",
        start_seconds=0.0, end_seconds=10.0,
        narration="Test",
        visual={"components": [{"type": "slide"}]},
    )
    with pytest.raises(UnsupportedComponentError, match="Unsupported component type"):
        renderer.render_scene(scene, str(tmp_output))


def test_reject_unsupported_code(renderer, tmp_output):
    scene = StoryboardScene(
        id="scene-001", type="hook", title="Test",
        start_seconds=0.0, end_seconds=10.0,
        narration="Test",
        visual={"components": [{"type": "code"}]},
    )
    with pytest.raises(UnsupportedComponentError, match="Unsupported component type"):
        renderer.render_scene(scene, str(tmp_output))


def test_reject_unsupported_unknown_type(renderer, tmp_output):
    scene = StoryboardScene(
        id="scene-001", type="hook", title="Test",
        start_seconds=0.0, end_seconds=10.0,
        narration="Test",
        visual={"components": [{"type": "gaussian_blur"}]},
    )
    with pytest.raises(UnsupportedComponentError, match="Unsupported component type"):
        renderer.render_scene(scene, str(tmp_output))


def test_reject_font_not_found(tmp_output):
    renderer = SceneRenderer(
        resolution=(640, 360),
        font_path="/nonexistent/font.ttf",
    )
    scene = StoryboardScene(
        id="scene-001", type="hook", title="Test",
        start_seconds=0.0, end_seconds=10.0,
        narration="Test",
        visual={
            "components": [
                {"type": "solid_color", "color": "#000000"},
                {"type": "text", "content": "Hello"},
            ]
        },
    )
    with pytest.raises(AdapterValidationError, match="Font file not found"):
        renderer.render_scene(scene, str(tmp_output))


def test_image_fit_contain(renderer, tmp_output, tmp_path):
    src = tmp_path / "wide.png"
    Image.new("RGB", (800, 200), (100, 100, 100)).save(src)
    scene = StoryboardScene(
        id="scene-001", type="hook", title="Test",
        start_seconds=0.0, end_seconds=10.0,
        narration="Test",
        visual={"components": [{"type": "image", "source": str(src)}]},
    )
    renderer.render_scene(scene, str(tmp_output))
    assert Image.open(tmp_output).size == (640, 360)


def test_custom_resolution(tmp_path):
    renderer = SceneRenderer(resolution=(1280, 720))
    scene = StoryboardScene(
        id="scene-001", type="hook", title="Test",
        start_seconds=0.0, end_seconds=10.0,
        narration="Test",
        visual={"components": [{"type": "solid_color", "color": "#00FF00"}]},
    )
    path = str(tmp_path / "custom.png")
    renderer.render_scene(scene, path)
    with Image.open(path) as img:
        assert img.size == (1280, 720)
