# -*- coding: utf-8 -*-
"""Producir un reel de código NUEVO a partir de un tema, desde el bot.

Cadena:  tema -> Ollama genera {titulo, code, narracion}  ->  SE EJECUTA el código
(output real, nunca inventado: Regla 2 del proyecto) -> se registra en _banco.json
-> se renderiza con la FÁBRICA pulida generar_reels_v2 en DOS variantes:
    reels_v2/     (con música Meta, para Facebook)
    reels_v2_yt/  (sin música, seguro para YouTube)
Devuelve {eid, titulo, mp4} o {error}.

NO publica. NO toca handlers.py. El bot (asistente.py) llama a producir() y luego envía
con reels_review.enviar_reel_con_botones.
"""
from __future__ import annotations
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
POC = REPO / "docs/operations/evidencia/poc_terminal_shorts_20261005"
for _p in (str(POC),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

BANCO = POC / "_banco.json"
# 31-oct-2026 (auditoría de seguridad, P0 abierto): el código que genera Ollama corre SIN aislamiento real
# (el filtro PELIGRO se evade). Bloqueado por defecto: solo se ejecuta si existe este archivo, que se crea
# a mano cuando haya sandbox (WASI/AppContainer) probado. Afecta al bot, la fábrica, ciclo_diario y lote_python.
PERMISO_CODIGO_IA = REPO / "logs" / "PERMITIR_CODIGO_IA"
BLOQUEADO = ("la ejecución de código generado por IA está bloqueada temporalmente (auditoría de seguridad, "
             "P0 abierto). Los reels ya hechos no se afectan.")
OLLAMA = "http://localhost:11434/api/chat"
MODELO = "qwen2.5:14b-instruct-q4_K_M"

# Guard: código generado que se auto-ejecuta. Bloquea E/S de red, archivos y procesos.
PELIGRO = re.compile(
    r"\b(import\s+(os|sys|subprocess|socket|shutil|requests|urllib|pathlib|ctypes)"
    r"|from\s+(os|subprocess|socket|shutil)\b|__import__|\beval\s*\(|\bexec\s*\("
    r"|\bopen\s*\(|os\.|sys\.|subprocess|socket|shutil|rmtree|remove|unlink|rmdir|system\s*\()",
    re.I)

SISTEMA = (
    "Eres generador de retos de Python para reels educativos de 1 minuto. "
    "Devuelves SOLO un objeto JSON válido, sin markdown ni texto extra, con EXACTAMENTE estas llaves: "
    '{"titulo": str, "code": str, "narracion": str}. '
    "Reglas: titulo corto en MAYÚSCULAS (2-4 palabras). "
    "code = Python puro, 3 a 7 líneas, autocontenido, que IMPRIME un resultado con print(); "
    "SOLO cómputo (strings, listas, dicts, math, comprehensions, f-strings); "
    "PROHIBIDO: import os/sys/subprocess/socket/archivos/red/eval/exec/open. "
    "narracion = 1 a 2 frases en español de México explicando qué hace el código, clara y natural para leer en voz alta. "
    "El code debe correr sin error y producir una salida visible."
)


def _ollama(tema: str) -> dict | None:
    import requests
    user = ("Tema del reto: %s. Genera el JSON." % tema)
    try:
        r = requests.post(OLLAMA, json={"model": MODELO, "stream": False, "format": "json",
                          "messages": [{"role": "system", "content": SISTEMA},
                                       {"role": "user", "content": user}]}, timeout=120)
        if r.status_code != 200:
            return None
        cont = (r.json().get("message", {}) or {}).get("content", "")
        # el 14b a veces devuelve "TITULO" (el prompt pide el título en MAYÚSCULAS): llaves sin distinguir mayúsculas
        d = {str(k).strip().lower(): v for k, v in json.loads(cont).items()}
        if all(k in d for k in ("titulo", "code", "narracion")) and d["code"].strip():
            return {"titulo": str(d["titulo"]).strip()[:40], "code": str(d["code"]).strip(),
                    "narracion": str(d["narracion"]).strip()}
    except Exception:
        return None
    return None


def _ejecutar(code: str) -> tuple[bool, str]:
    """Corre el código aislado y devuelve (ok, output_real). Nunca inventa la salida."""
    if not PERMISO_CODIGO_IA.is_file():   # segunda barrera: aunque alguien llame directo a _ejecutar
        return False, BLOQUEADO
    if PELIGRO.search(code):
        return False, "codigo con operaciones no permitidas"
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "reto.py"
        f.write_text(code, encoding="utf-8")
        try:
            r = subprocess.run([sys.executable, "-I", str(f)], capture_output=True, text=True,
                               timeout=8, cwd=td)
        except subprocess.TimeoutExpired:
            return False, "timeout"
        if r.returncode != 0 or r.stderr.strip():
            return False, (r.stderr.strip() or "exit %d" % r.returncode)[:200]
        out = r.stdout.strip()
        return (bool(out), out)


def _siguiente_id() -> str:
    nums = []
    try:
        for e in json.loads(BANCO.read_text(encoding="utf-8")):
            m = re.match(r"e(\d+)$", e.get("id", ""))
            if m:
                nums.append(int(m.group(1)))
    except Exception:
        pass
    for d in ("reels_v2", "reels_v2_yt"):
        for p in (POC / d).glob("e*.mp4"):
            m = re.match(r"e(\d+)_", p.name)
            if m:
                nums.append(int(m.group(1)))
    return "e%d" % ((max(nums) + 1) if nums else 86)


def _registrar_banco(ej: dict) -> None:
    banco = json.loads(BANCO.read_text(encoding="utf-8"))
    if not any(e.get("id") == ej["id"] for e in banco):
        banco.append(ej)
        tmp = BANCO.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(banco, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(BANCO)


def _render(ej: dict) -> dict:
    """Renderiza FB (con música) y YT (sin música). Devuelve rutas."""
    import generar_reels as g
    import generar_reels_v2 as v2
    try:  # voz del documental (Chatterbox, verificada con whisper); si no está disponible, Kokoro como antes
        from voz_documental import VozDocumental
        prov = VozDocumental()
    except Exception:
        from pipeline.providers.kokoro_tts import KokoroTTSProvider
        prov = KokoroTTSProvider(velocidad=g.SPEED)
    slug = ej["titulo"].lower().replace(" ", "_")
    res = {}

    # ---- FB: reels_v2/ con música ----
    vid = v2.REELS_V2 / ("%s_%s_v2.mp4" % (ej["id"], slug))
    raw = v2.REELS_V2 / ("_%s_raw.wav" % ej["id"]); mast = v2.REELS_V2 / ("_%s.wav" % ej["id"])
    sdur, total = v2.narracion_v2(prov, ej["narracion"], raw, mast)
    mix = v2.REELS_V2 / ("_%s_mix.wav" % ej["id"])
    if v2.mezclar_musica(mast, total, mix):
        mast.unlink(missing_ok=True); mix.replace(mast)
    v2.render_reel_v2(ej, v2.LEAD, sdur, total, mast, vid)
    raw.unlink(missing_ok=True); mast.unlink(missing_ok=True)
    res["fb"] = vid

    # ---- YT: reels_v2_yt/ SIN música (override temporal de globals) ----
    _rv2, _mus = v2.REELS_V2, v2.MUSIC_DIR
    try:
        v2.REELS_V2 = POC / "reels_v2_yt"; v2.REELS_V2.mkdir(parents=True, exist_ok=True)
        v2.MUSIC_DIR = POC / "assets_musica_yt"
        vyt = v2.REELS_V2 / ("%s_%s_v2yt.mp4" % (ej["id"], slug))
        raw = v2.REELS_V2 / ("_%s_raw.wav" % ej["id"]); mast = v2.REELS_V2 / ("_%s.wav" % ej["id"])
        sdur, total = v2.narracion_v2(prov, ej["narracion"], raw, mast)
        v2.render_reel_v2(ej, v2.LEAD, sdur, total, mast, vyt)
        raw.unlink(missing_ok=True); mast.unlink(missing_ok=True)
        res["yt"] = vyt
    finally:
        v2.REELS_V2, v2.MUSIC_DIR = _rv2, _mus
    return res


def producir(tema: str) -> dict:
    tema = (tema or "").strip()
    if not tema:
        return {"error": "sin tema"}
    if not PERMISO_CODIGO_IA.is_file():   # ni siquiera se le pide código a Ollama
        return {"error": BLOQUEADO}
    # 1) generar + 2) ejecutar (hasta 3 intentos: el output debe ser REAL)
    ej_gen = None
    for _ in range(3):
        g = _ollama(tema)
        if not g:
            continue
        ok, out = _ejecutar(g["code"])
        if ok:
            ej_gen = g; ej_gen["output"] = out
            break
    if not ej_gen:
        return {"error": "no logré generar un reto que corriera limpio para «%s»" % tema[:60]}

    eid = _siguiente_id()
    ej = {"id": eid, "bloque": "BD", "nivel": "bajo-demanda", "titulo": ej_gen["titulo"],
          "narracion": ej_gen["narracion"], "code": ej_gen["code"], "output": ej_gen["output"],
          "estado": "OK", "stderr": "",
          "caption": "%s — reto de Python" % ej_gen["titulo"].capitalize(),
          "hash": hashlib.sha1(ej_gen["code"].encode("utf-8")).hexdigest()[:12],
          "tags": ["python", "bajo-demanda"]}
    try:
        _registrar_banco(ej)
        rutas = _render(ej)
    except Exception as e:
        return {"error": "falló el render: %s" % str(e)[:160]}
    return {"eid": eid, "titulo": ej["titulo"], "tema": tema,
            "mp4": str(rutas.get("fb")), "mp4_yt": str(rutas.get("yt", ""))}


if __name__ == "__main__":
    import json as _j
    print(_j.dumps(producir(" ".join(sys.argv[1:]) or "list comprehension con filtro"), ensure_ascii=False, indent=1))
