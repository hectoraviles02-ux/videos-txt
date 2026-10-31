# -*- coding: utf-8 -*-
"""Acceso verificado a los procesado/*.json (regla 2 de CLAUDE.md: nada inventado).

cita(alias_o_ruta, segmento, texto) devuelve el texto SOLO si aparece literal en ese segmento; si no, lanza
CitaNoEncontrada. Así cada demo y la matriz del documental quedan verificadas al correr, no a mano.

Uso:  from fuentes import cita, RUTA
      cita("sec", 21, "Ultimately, Knight lost over $460 million from these unwanted positions.")
"""
from __future__ import annotations
import json
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# alias → ruta relativa al repo (formato de cita del proyecto: «procesado/....json — «cita»»)
RUTA = {
    "sec": "procesado/2026-10-15_sec-knight-capital-34-70694_eff763b768b5/sec-order-release-no-70694-in-the-matter-of-knight_eff763b768b5.json",
    "npm": "procesado/2026-10-15_npm-kik-left-pad-2016_a27682b77a12/npm-blog-archive-kik-leftpad-and-npm_a27682b77a12.json",
    "gitlab": "procesado/2026-10-15_gitlab-postmortem-db-outage-2017_d16e9f8b129e/postmortem-of-database-outage-of-january-31_d16e9f8b129e.json",
    "cf_a": "procesado/2026-10-14_details-of-the-cloudflare-outage-on-july-2-2019_904e601a4478/details-of-the-cloudflare-outage-on-july-2-2019_904e601a4478.json",
    "cf_b": "procesado/2026-10-14_cloudflare-outage-caused-by-bad-software-deploy-up_f13de0cc714d/cloudflare-outage-caused-by-bad-software-deploy-up_f13de0cc714d.json",
    "cs_blog": "procesado/2026-10-15_crowdstrike-falcon-technical-details-2024_603004b2f183/technical-details-falcon-update-for-windows-hosts-_603004b2f183.json",
    "cs_rca": "procesado/2026-10-15_crowdstrike-rca-channel-file-291-2024_6fa38375041d/external-technical-root-cause-analysis-channel-fil_6fa38375041d.json",
    "ms": "procesado/2026-10-15_microsoft-crowdstrike-outage-2024_a1c64e9fb861/helping-our-customers-through-the-crowdstrike-outa_a1c64e9fb861.json",
}


class CitaNoEncontrada(AssertionError):
    pass


def ruta(alias_o_ruta: str) -> str:
    return RUTA.get(alias_o_ruta, alias_o_ruta)


@lru_cache(maxsize=None)
def segmentos(alias_o_ruta: str) -> tuple[str, ...]:
    p = REPO / ruta(alias_o_ruta)
    return tuple(s.get("texto", "") for s in json.loads(p.read_text(encoding="utf-8"))["segmentos"])


def cita(alias_o_ruta: str, seg: int, texto: str) -> str:
    segs = segmentos(alias_o_ruta)
    if seg >= len(segs) or texto not in segs[seg]:
        raise CitaNoEncontrada("%s segmentos[%d] no contiene «%s»" % (ruta(alias_o_ruta), seg, texto))
    return texto


def buscar(alias_o_ruta: str, texto: str) -> int:
    """Índice del primer segmento que contiene el texto literal, o -1."""
    for i, s in enumerate(segmentos(alias_o_ruta)):
        if texto in s:
            return i
    return -1
