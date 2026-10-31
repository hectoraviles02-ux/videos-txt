import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from runs.run_manager import RunManager, VALID_STATUSES


def test_generate_run_id_format():
    from runs.run_manager import _generate_run_id
    rid = _generate_run_id()
    import re
    assert re.match(r"^\d{8}T\d{6}Z_[a-f0-9]{6}$", rid), f"Bad format: {rid}"


def test_start_creates_directories():
    rm = RunManager(pipeline="test", run_id="test-start-dir")
    try:
        rid = rm.start()
        assert rm.run_dir.is_dir()
        assert rm.logs_dir.is_dir()
        assert rm.run_id == rid
    finally:
        import shutil
        shutil.rmtree(rm.run_dir, ignore_errors=True)


def test_start_writes_initial_manifest():
    rm = RunManager(pipeline="test-pipe", run_id="test-start-manifest")
    try:
        rm.start()
        mp = rm.manifest_path()
        assert os.path.isfile(mp)
        with open(mp) as f:
            data = json.load(f)
        assert data["pipeline"] == "test-pipe"
        assert data["run_id"] == "test-start-manifest"
        assert data["started_at"]
        assert data["status"] == ""
    finally:
        import shutil
        shutil.rmtree(rm.run_dir, ignore_errors=True)


def test_transition_records_state():
    rm = RunManager(pipeline="test", run_id="test-transition")
    try:
        rm.start()
        rm.transition("STORYBOARD_READY")
        assert rm._manifest["status"] == "STORYBOARD_READY"
        assert len(rm._manifest["transitions"]) == 1
        t = rm._manifest["transitions"][0]
        assert t["from"] == ""
        assert t["to"] == "STORYBOARD_READY"
    finally:
        import shutil
        shutil.rmtree(rm.run_dir, ignore_errors=True)


def test_transition_with_evidence():
    rm = RunManager(pipeline="test", run_id="test-trans-evidence")
    try:
        rm.start()
        rm.transition("VIDEO_RENDERED", evidence=["output.mp4"])
        t = rm._manifest["transitions"][0]
        assert t["evidence"] == ["output.mp4"]
    finally:
        import shutil
        shutil.rmtree(rm.run_dir, ignore_errors=True)


def test_transition_invalid_status_raises():
    rm = RunManager(pipeline="test", run_id="test-invalid")
    try:
        rm.start()
        with pytest.raises(ValueError, match="Invalid status"):
            rm.transition("INVALID_STATUS")
    finally:
        import shutil
        shutil.rmtree(rm.run_dir, ignore_errors=True)


def test_add_input():
    rm = RunManager(pipeline="test", run_id="test-add-input")
    try:
        rm.start()
        rm.add_input("file", "tests/fixtures/storyboard_minimal.json", "abc123")
        assert len(rm._manifest["inputs"]) == 1
        inp = rm._manifest["inputs"][0]
        assert inp["type"] == "file"
    finally:
        import shutil
        shutil.rmtree(rm.run_dir, ignore_errors=True)


def test_add_output(tmp_path):
    rm = RunManager(pipeline="test", run_id="test-add-output")
    try:
        rm.start()
        artifact = tmp_path / "test.txt"
        artifact.write_text("hello")
        rm.add_output("render", str(artifact))
        assert "render" in rm._manifest["outputs"]
        o = rm._manifest["outputs"]["render"]
        assert o["size_bytes"] == 5
        assert len(o["hash"]) == 64
    finally:
        import shutil
        shutil.rmtree(rm.run_dir, ignore_errors=True)


def test_add_error():
    rm = RunManager(pipeline="test", run_id="test-add-error")
    try:
        rm.start()
        rm.add_error("render", "FFMPEG_FAIL", "ffmpeg exited with code 1")
        assert len(rm._manifest["errors"]) == 1
        e = rm._manifest["errors"][0]
        assert e["phase"] == "render"
        assert e["code"] == "FFMPEG_FAIL"
    finally:
        import shutil
        shutil.rmtree(rm.run_dir, ignore_errors=True)


def test_add_warning():
    rm = RunManager(pipeline="test", run_id="test-add-warning")
    try:
        rm.start()
        rm.add_warning("tts", "audio shorter than expected")
        assert len(rm._manifest["warnings"]) == 1
    finally:
        import shutil
        shutil.rmtree(rm.run_dir, ignore_errors=True)


def test_add_evidence(tmp_path):
    rm = RunManager(pipeline="test", run_id="test-evidence")
    try:
        rm.start()
        ef = tmp_path / "report.json"
        ef.write_text("{}")
        rm.add_evidence(str(ef), "validation_report")
        assert len(rm._manifest["evidence_files"]) == 1
        ev = rm._manifest["evidence_files"][0]
        assert ev["type"] == "validation_report"
        assert len(ev["hash"]) == 64
    finally:
        import shutil
        shutil.rmtree(rm.run_dir, ignore_errors=True)


def test_copy_input(tmp_path):
    rm = RunManager(pipeline="test", run_id="test-copy")
    try:
        rm.start()
        src = tmp_path / "source.txt"
        src.write_text("data")
        dst = rm.copy_input(str(src), "storyboard.json")
        assert os.path.isfile(dst)
        with open(dst) as f:
            assert f.read() == "data"
    finally:
        import shutil
        shutil.rmtree(rm.run_dir, ignore_errors=True)


def test_finish_sets_status_and_timestamp():
    rm = RunManager(pipeline="test", run_id="test-finish")
    try:
        rm.start()
        rm.finish("TECHNICAL_PASS")
        assert rm._manifest["status"] == "TECHNICAL_PASS"
        assert rm._manifest["finished_at"]
        assert rm._manifest["finished_at"] > rm._manifest["started_at"]
    finally:
        import shutil
        shutil.rmtree(rm.run_dir, ignore_errors=True)


def test_finish_invalid_status_raises():
    rm = RunManager(pipeline="test", run_id="test-finish-invalid")
    try:
        rm.start()
        with pytest.raises(ValueError, match="Invalid final status"):
            rm.finish("BOGUS")
    finally:
        import shutil
        shutil.rmtree(rm.run_dir, ignore_errors=True)


def test_load_roundtrip():
    rm = RunManager(pipeline="test", run_id="test-load")
    try:
        rm.start()
        rm.transition("STORYBOARD_READY")
        rm.finish("TECHNICAL_PASS")
        rm2 = RunManager.load("test-load")
        assert rm2._manifest["run_id"] == "test-load"
        assert rm2._manifest["pipeline"] == "test"
        assert rm2._manifest["status"] == "TECHNICAL_PASS"
        assert len(rm2._manifest["transitions"]) == 1
    finally:
        import shutil
        shutil.rmtree(rm.run_dir, ignore_errors=True)


def test_set_tool_versions():
    rm = RunManager(pipeline="test", run_id="test-tools")
    try:
        rm.start()
        rm.set_tool_versions({"ffmpeg": "7.0", "gtts": "2.5.4"})
        assert rm._manifest["tools"]["ffmpeg"] == "7.0"
    finally:
        import shutil
        shutil.rmtree(rm.run_dir, ignore_errors=True)


def test_to_dict():
    rm = RunManager(pipeline="test", run_id="test-todict")
    try:
        rm.start()
        d = rm.to_dict()
        assert d["pipeline"] == "test"
        assert d["run_id"] == "test-todict"
    finally:
        import shutil
        shutil.rmtree(rm.run_dir, ignore_errors=True)


def test_all_valid_statuses():
    expected = [
        "SOURCE_RECEIVED", "SOURCE_EXTRACTED", "CONTENT_NORMALIZED",
        "SCRIPT_READY", "STORYBOARD_READY", "AUDIO_READY", "VIDEO_RENDERED",
        "TECHNICAL_PASS", "EDITORIAL_APPROVED", "PUBLISHED", "FAILED", "REJECTED",
    ]
    assert VALID_STATUSES == expected


def test_multiple_transitions():
    rm = RunManager(pipeline="test", run_id="test-multi-trans")
    try:
        rm.start()
        rm.transition("STORYBOARD_READY")
        rm.transition("AUDIO_READY")
        rm.transition("VIDEO_RENDERED")
        assert len(rm._manifest["transitions"]) == 3
        assert rm._manifest["transitions"][0]["to"] == "STORYBOARD_READY"
        assert rm._manifest["transitions"][1]["to"] == "AUDIO_READY"
        assert rm._manifest["transitions"][2]["to"] == "VIDEO_RENDERED"
    finally:
        import shutil
        shutil.rmtree(rm.run_dir, ignore_errors=True)
