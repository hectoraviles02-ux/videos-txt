# -*- coding: utf-8 -*-
"""Corre un comando SOLO si el candado de la fábrica (telegram_bot/candado.py) está libre, y lo sostiene mientras
dura (el candado verifica que el pid que lo tomó siga vivo: por eso lo toma este proceso y no un shell suelto).

Uso: python con_candado.py <quien> <comando> [args …]     → sale 2 si está OCUPADO (y dice quién lo tiene)"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_bot import candado  # noqa: E402

if __name__ == "__main__":
    quien, cmd = sys.argv[1], sys.argv[2:]
    with candado.tomar(quien) as libre:
        if not libre:
            print("OCUPADO:", candado.quien(), flush=True)
            sys.exit(2)
        print("candado tomado por %s (pid %d)" % (quien, os.getpid()), flush=True)
        sys.exit(subprocess.run(cmd).returncode)
