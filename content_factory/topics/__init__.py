# -*- coding: utf-8 -*-
"""Motor de temas: descubrimiento, clasificacion, puntuacion, diversidad y seleccion.

Sin imports pesados a nivel de paquete: `sentence_transformers` y `torch` se
cargan perezosamente dentro de `diversity.py` (RQ-24, RQ-53).
"""

from __future__ import annotations
