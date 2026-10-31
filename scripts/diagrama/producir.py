# -*- coding: utf-8 -*-
"""Motor «diagrama» — orquestador (23-oct-2026). operador pidió que sus enlaces de Telegram salgan con el estilo del
video de PHP (diagramas que explican + voz Chatterbox), no con tomas de banco: «ya lo hubieras terminado».

producir(storyboard, fuente, carpeta) — storyboard YA aprobado por la revisión de hechos del bot:
  1) plan.planificar  → plan.json (Claude, o respaldo); textos en pantalla literales y ensayados;
  2) voz.py (python de chatterbox_ab_env) → voz.wav + tiempos.json, cada frase verificada al oído;
  3) render.render    → <carpeta>/<id>_9x16.mp4 a −14 LUFS.
Cualquier falla lanza FallaDiagrama con el motivo: el bot la avisa; NO cae a tomas de banco.
El candado de la fábrica lo sostiene quien llama (handlers._run_pipeline_in_thread).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
import plan as PL  # noqa: E402
import render as RD  # noqa: E402

PY_CHATTER = r"C:/Users/<usuario>\chatterbox_ab_env\Scripts\python.exe"


class FallaDiagrama(RuntimeError):
    pass


def leyenda_de(fuente) -> str:
    url = str((fuente or {}).get("source_url") or "") if isinstance(fuente, dict) else ""
    m = re.match(r"https?://(?:www\.|m\.)?([^/]+)(.*)", url)
    if not m:
        return "ESQUEMA ILUSTRATIVO"
    dominio = {"facebook.com": "REEL FB", "youtube.com": "YOUTUBE", "youtu.be": "YOUTUBE", "tiktok.com": "TIKTOK",
               "instagram.com": "INSTAGRAM"}.get(m.group(1), m.group(1).upper())
    cola = [p for p in m.group(2).split("?")[0].split("/") if p][-1:] or [""]
    return f"ESQUEMA ILUSTRATIVO · FUENTE: {dominio} {cola[0]}"[:60]


def producir(storyboard, fuente, carpeta: Path, registro=print, dossier: dict | None = None) -> Path:
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    frases = PL.frases_de(storyboard)
    if not frases:
        raise FallaDiagrama("el guion no tiene frases")
    leyenda = leyenda_de(fuente)
    try:
        plan = PL.planificar(frases, leyenda, registro=registro, dossier=dossier)
    except SystemExit as e:
        raise FallaDiagrama(f"no se pudo planear el video: {e}") from e
    if dossier:
        (carpeta / "dossier.json").write_text(json.dumps(dossier, ensure_ascii=False, indent=1), encoding="utf-8")
    (carpeta / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    registro(f"diagrama: plan {plan.get('origen_plan')} · {len(plan['escenas'])} escenas · {len(frases)} frases")
    r = subprocess.run([PY_CHATTER, str(AQUI / "voz.py"), str(carpeta)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=3600,
                       env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    (carpeta / "voz.log").write_text(r.stdout + "\n" + r.stderr[-4000:], encoding="utf-8")
    if r.returncode != 0 or not (carpeta / "tiempos.json").is_file():
        ultima = [ln for ln in r.stdout.splitlines() if ln.startswith("FALLA")] or [r.stderr.strip()[-200:]]
        raise FallaDiagrama(f"la voz no quedó: {ultima[-1]}")
    registro(f"diagrama: voz lista ({time.time() - t0:.0f} s)")
    try:
        mp4 = RD.render(carpeta)
    except SystemExit as e:
        raise FallaDiagrama(f"el armado se detuvo: {e}") from e
    tiempos = json.loads((carpeta / "tiempos.json").read_text(encoding="utf-8"))
    dudosas = [t["texto"][:60] + " → no se oyó: " + ", ".join(t["palabras_no_oidas"])
               for t in tiempos["tramos"] if t.get("palabras_no_oidas")]
    (carpeta / "manifiesto.json").write_text(json.dumps({
        "video": str(mp4), "plan": plan.get("origen_plan"), "motivo_respaldo": plan.get("motivo_respaldo"),
        "duracion_s": tiempos["duracion"], "frases": len(frases), "voz_referencia": tiempos.get("referencia"),
        "frases_con_palabras_dudosas": dudosas, "segundos": round(time.time() - t0)}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    registro(f"diagrama: video {mp4} ({time.time() - t0:.0f} s)")
    return mp4
