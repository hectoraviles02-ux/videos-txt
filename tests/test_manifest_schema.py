"""Test: validar schema del manifest de ejecución."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import jsonschema
except ImportError:
    print("FALTA jsonschema. Corre: pip install jsonschema")
    sys.exit(1)

BASE = Path(__file__).resolve().parent.parent
SCHEMA_PATH = BASE / "schemas" / "run_manifest.schema.json"


def _make_valid_manifest():
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%dT%H%M%SZ")
    return {
        "manifest_version": "1.0",
        "run_id": f"{ts}_abc123",
        "pipeline": "full_production",
        "status": "VIDEO_RENDERED",
        "started_at": now.isoformat(),
        "finished_at": now.isoformat(),
        "config": {
            "file": "config/render.yaml",
            "profile": "hd1080",
            "hash": "a" * 64
        },
        "code_version": {
            "commit": "abc123def456",
            "branch": "master",
            "dirty": False
        },
        "inputs": [
            {
                "type": "url",
                "uri": "https://youtube.com/watch?v=test",
                "hash": "b" * 64,
                "metadata": {"title": "Test Video"}
            }
        ],
        "outputs": {
            "render": {
                "path": "runs/test/render/output.mp4",
                "hash": "c" * 64,
                "size_bytes": 1048576,
                "mime_type": "video/mp4"
            }
        },
        "tools": {
            "ffmpeg": "7.0",
            "scholium": "0.5.2"
        },
        "errors": [],
        "warnings": [],
        "transitions": [
            {
                "from": "SOURCE_RECEIVED",
                "to": "VIDEO_RENDERED",
                "at": now.isoformat(),
                "evidence": ["runs/test/manifest.json"]
            }
        ],
        "evidence_files": [
            {
                "path": "runs/test/validation/report.json",
                "type": "validation_report",
                "hash": "d" * 64
            }
        ],
        "authorization": {
            "mode": "auto",
            "paid_api_allowed": False
        }
    }


def main():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema = json.load(f)

    jsonschema.Draft7Validator.check_schema(schema)
    print(f"Schema manifest: VALIDO")

    # Test 1: manifest válido pasa
    manifest = _make_valid_manifest()
    try:
        jsonschema.validate(manifest, schema)
        print(f"  OK  manifest válido")
    except jsonschema.ValidationError as e:
        print(f"  FAIL manifest válido: {e.message}")
        sys.exit(1)

    # Test 2: manifest sin campos requeridos falla
    for campo in ["run_id", "status", "inputs", "errors"]:
        malo = _make_valid_manifest()
        del malo[campo]
        try:
            jsonschema.validate(malo, schema)
            print(f"  FAIL debería rechazar falta de '{campo}'")
            sys.exit(1)
        except jsonschema.ValidationError:
            print(f"  OK  rechaza falta de '{campo}'")

    # Test 3: status inválido falla
    malo = _make_valid_manifest()
    malo["status"] = "INVALID_STATUS"
    try:
        jsonschema.validate(malo, schema)
        print(f"  FAIL debería rechazar status inválido")
        sys.exit(1)
    except jsonschema.ValidationError:
        print(f"  OK  rechaza status inválido")

    print("\nTodas las pruebas del schema del manifest pasan")


if __name__ == "__main__":
    main()
