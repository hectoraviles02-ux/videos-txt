# -*- coding: utf-8 -*-
"""
Ritual de ARRANQUE (compuerta GO/NO-GO).
Corre esto como PRIMERA linea de cada sesion:
    python scripts/inicio.py
Hace tres cosas, en orden:
  1. Verifica dependencias (yt-dlp, faster-whisper, PyAV, readability, requests).
  2. Lee INDICE.md  -> que URLs ya se procesaron (memoria estructural).
  3. Lee las ultimas 30 lineas de MEMORIA.md (memoria narrativa).
Si falta una dependencia -> NO-GO, dice exactamente que instalar.
"""
import importlib.util
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DEPS = {
    "yt_dlp": "yt-dlp",
    "faster_whisper": "faster-whisper",
    "av": "av (PyAV — decodifica audio sin ffmpeg CLI)",
    "readability": "readability-lxml",
    "requests": "requests",
}


def main():
    base = Path(__file__).resolve().parent.parent
    indice = base / "INDICE.md"
    memoria = base / "MEMORIA.md"

    print("=" * 60)
    print("EXTRACTOR DE CONTENIDO — ARRANQUE")
    print(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # 1. Dependencias (compuerta)
    print("\n[1/3] Verificando dependencias...")
    faltan = []
    for mod, nombre in DEPS.items():
        ok = importlib.util.find_spec(mod) is not None
        print(f"  {'OK  ' if ok else 'FALTA'} {nombre}")
        if not ok:
            faltan.append(nombre)
    if faltan:
        print("\n  NO-GO: faltan dependencias. Corre:  powershell -File setup.ps1")
        print("  (o: python -m pip install " + " ".join(DEPS.values()).replace(" (av (PyAV — decodifica audio sin ffmpeg CLI))", "") + ")")
        sys.exit(1)

    # 2. INDICE.md (memoria estructural)
    print("\n[2/3] INDICE.md (URLs ya procesadas)...")
    if indice.exists():
        lineas = [l for l in indice.read_text(encoding="utf-8").splitlines() if l.startswith("| 20")]
        print(f"  procesadas: {len(lineas)}")
        for l in lineas[-5:]:
            print(f"  {l}")
    else:
        print("  (proyecto nuevo, aun sin extracciones)")

    # 3. MEMORIA.md (memoria narrativa)
    print("\n[3/3] MEMORIA.md (ultimas 30 entradas)...")
    if memoria.exists():
        lineas = [l for l in memoria.read_text(encoding="utf-8").splitlines() if l.startswith("- [")]
        for l in lineas[-30:]:
            print(f"  {l}")
        if not lineas:
            print("  (sin entradas)")
    else:
        print("  (sin memoria narrativa aun)")

    print("\n" + "=" * 60)
    print("GO — listo para recibir URLs:  python scripts/procesar_url.py <URL>")
    print("=" * 60)


if __name__ == "__main__":
    main()
