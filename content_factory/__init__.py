"""Fabrica de contenido — motor de temas (V0).

Paquete AISLADO y aditivo: no importa ni modifica `synthesis/`, `adapters/`,
`pipeline/`, `voz/`, `quality/`, `runs/` ni `telegram_bot/`.

**Este modulo no importa nada pesado y no debe hacerlo** (RQ-24, RQ-53, D-15):
ni `torch`, ni `sentence_transformers`, ni `requests`, ni `yt_dlp`. Cargar
MiniLM cuesta 52.9 s medidos; si el paquete lo arrastrara al importarse, los
378 tests existentes pagarian ese costo en cada corrida de `pytest`. El import
de `sentence_transformers` vive, perezoso, dentro de `topics/diversity.py`.
"""

from __future__ import annotations

__version__ = "0.1.0"
