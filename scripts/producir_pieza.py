# -*- coding: utf-8 -*-
r"""Selector de motor por configuración: una sola entrada para producir una pieza.
    python scripts\producir_pieza.py <carpeta_pieza> [--motor nombre]
Lee el motor de guion.json["motor"] (o --motor) y ejecuta el comando registrado en config/motores_video.json.
No modifica ningún motor: solo los invoca. Un motor con "activo": false no se ejecuta (reversa sin borrar nada).
Devuelve el código de salida del motor (0 entregado · 1 compuerta/revisión falló · 2 fábrica ocupada)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
CONFIG = RAIZ / "config" / "motores_video.json"


def resolver(pieza: Path, motor: str | None = None) -> list[str]:
    reg = json.loads(CONFIG.read_text(encoding="utf-8"))["motores"]
    if motor is None:
        g = pieza / "guion.json"
        if not g.is_file():
            raise SystemExit(f"no hay guion.json en {pieza}")
        motor = json.loads(g.read_text(encoding="utf-8")).get("motor")
    if motor not in reg:
        raise SystemExit(f"motor «{motor}» no registrado (registrados: {sorted(reg)})")
    m = reg[motor]
    if not m.get("activo"):
        raise SystemExit(f"motor «{motor}» desactivado: {m.get('motivo', 'activo=false')}")
    return [c.replace("{python}", sys.executable).replace("{pieza}", str(pieza)) for c in m["comando"]]


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--motor")]
    motor = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--motor=")), None)
    cmd = resolver(Path(args[0]).resolve(), motor)
    print("motor:", " ".join(cmd), flush=True)
    sys.exit(subprocess.run(cmd, cwd=RAIZ).returncode)
