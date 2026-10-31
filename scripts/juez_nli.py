# -*- coding: utf-8 -*-
"""Compatibilidad: el juez NLI vive en synthesis/juez_nli.py (lo usa el gate del bot)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from synthesis.juez_nli import MODELO, juez_nli  # noqa: E402,F401
