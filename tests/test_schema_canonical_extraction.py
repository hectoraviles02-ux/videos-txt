"""Test: validar extracciones existentes contra el schema."""
import json
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:
    print("FALTA jsonschema. Corre: pip install jsonschema")
    sys.exit(1)

BASE = Path(__file__).resolve().parent.parent
SCHEMA_PATH = BASE / "schemas" / "canonical_extraction.schema.json"
PROCESADO = BASE / "procesado"


def main():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema = json.load(f)

    jsonschema.Draft7Validator.check_schema(schema)
    print(f"Schema: VALIDO")

    json_files = list(PROCESADO.rglob("*.json"))
    if not json_files:
        print("No hay archivos JSON para validar")
        sys.exit(0)

    errores = 0
    for jf in sorted(json_files):
        with open(jf, encoding="utf-8") as f:
            data = json.load(f)
        try:
            jsonschema.validate(data, schema)
            print(f"  OK  {jf.relative_to(BASE)}")
        except jsonschema.ValidationError as e:
            errores += 1
            print(f"  FAIL {jf.relative_to(BASE)}")
            print(f"       {e.message}")

    if errores:
        print(f"\n{errores} archivo(s) NO pasan el schema")
        sys.exit(1)
    print("\nTodas las extracciones pasan el schema")


if __name__ == "__main__":
    main()
