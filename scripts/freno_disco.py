# -*- coding: utf-8 -*-
"""Freno de disco para la fábrica (operador, 24-oct-2026: «sigue con freno»).

El NVMe de C: (Harddisk0) registró 110 avisos «bloque defectuoso» el 24-oct a las 12:32. Este vigilante cuenta los
avisos del disco 0 POSTERIORES a ese momento; si aparece uno solo, crea logs/DETENER_FABRICA (la fábrica termina la
pieza en curso y se apaga) y avisa por Telegram. No toca el rescate ni borra nada.
Uso: python scripts/freno_disco.py   (corre hasta que frena o lo cierras)
"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
DESDE = "2026-10-24 12:33:00"          # después de la ráfaga de 110 avisos (12:32:38)
DETENER = REPO / "logs" / "DETENER_FABRICA"
LOG = REPO / "logs" / "freno_disco.log"
CADA_S = 120


def log(m: str) -> None:
    linea = f"{datetime.now():%Y-%m-%d %H:%M:%S} {m}"
    print(linea, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(linea + "\n")


def avisos_nuevos() -> int:
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "@(Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=[datetime]'%s'} -EA SilentlyContinue"
                        " | ? { $_.ProviderName -match '^disk$|stornvme' -and $_.Message -match 'Harddisk0' }).Count" % DESDE],
                       capture_output=True, text=True)
    return int((r.stdout or "0").strip() or 0)


def main() -> int:
    log(f"freno de disco activo: avisos del disco 0 desde {DESDE} = {avisos_nuevos()}")
    while True:
        n = avisos_nuevos()
        if n > 0:
            DETENER.write_text(f"freno_disco {datetime.now():%Y-%m-%d %H:%M:%S}: {n} avisos nuevos del disco 0\n",
                               encoding="utf-8")
            # operador, 24-oct 23:00: «detén la fábrica cuanto antes, sin esperar a terminar el render» → se termina el
            # árbol de procesos de la fábrica (render y voz incluidos). El rescate y el bot no se tocan.
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | ? { $_.CommandLine -match "
                            "'fabrica_continua' } | % { taskkill /PID $_.ProcessId /T /F }"], capture_output=True)
            log(f"FRENO: {n} avisos nuevos del disco 0 → fábrica detenida de inmediato (render incluido)")
            try:
                from review_channels.telegram_channel import _load_env
                _load_env()
                sys.path.insert(0, str(REPO / "scripts"))
                from fabrica_continua import telegram
                telegram(f"🛑 Freno de disco: el disco C: registró {n} avisos nuevos. Detuve la fábrica al terminar la "
                         f"pieza en curso. El rescate sigue. No reinicies la fábrica hasta revisar el disco.")
            except Exception as e:  # noqa: BLE001 - el freno ya quedó puesto; el aviso es secundario
                log(f"no pude avisar por Telegram: {e}")
            return 0
        time.sleep(CADA_S)


if __name__ == "__main__":
    sys.exit(main())
