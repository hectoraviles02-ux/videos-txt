# -*- coding: utf-8 -*-
"""Valida las versiones TikTok sin música (_v2tt) contra el original publicado/programado (_v2yt):
duración, resolución, imagen (PSNR de un cuadro a la mitad), audio presente, sonoridad y narración
(transcripción con faster-whisper vs narración del banco). Solo reporta.
Uso: python validar_reels_tiktok.py [--sufijo v3tt] e11 e12 ...
     → JSON en docs/operations/evidencia/publicacion_tiktok_20261017/_validacion_<sufijo>.json

Compuerta de términos técnicos (17-oct-2026): una similitud general alta NO aprueba un reel educativo.
Cada nombre de código que se narra (palabra de la narración que aparece en el código, más «Python») debe
estar en synthesis.texto_locucion.LEXICO_CODIGO / LEXICO y llegar a la voz con su pronunciación aprobada.
Término sin revisar o sin corregir → REVISAR. Se guardan los fonemas que Kokoro recibió para cada término."""
from __future__ import annotations

import difflib
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
POC = REPO / "docs/operations/evidencia/poc_terminal_shorts_20261005"
TTD = REPO / "guiones/reels_python/2026-10-17_tiktok_sin_musica"
EVI = REPO / "docs/operations/evidencia/publicacion_tiktok_20261017"
sys.path.insert(0, str(REPO))
from synthesis.texto_locucion import LEXICO, LEXICO_CODIGO, aplicar_lexico_codigo  # noqa: E402


def probe(f):
    j = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                                   "-of", "json", str(f)], capture_output=True, text=True).stdout)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    return float(j["format"]["duration"]), "%dx%d" % (v["width"], v["height"]), any(s["codec_type"] == "audio" for s in j["streams"])


def lufs(f):
    o = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(f), "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True).stderr
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", o)
    return float(m[-1]) if m else None


def psnr(a, b, t):
    o = subprocess.run(["ffmpeg", "-hide_banner", "-ss", str(t), "-i", str(a), "-ss", str(t), "-i", str(b), "-frames:v", "1",
                        "-lavfi", "psnr", "-f", "null", "-"], capture_output=True, text=True).stderr
    m = re.search(r"average:(\S+)", o)
    return m.group(1) if m else None


def norm(s):
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]", " ", s).split()


def palabras(s):
    return re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ_]+", s)


def terminos(ej, voz, g2p):
    """Revisa cada nombre de código narrado: aprobado tal cual, corregido en la voz, o sin revisar."""
    codigo = set(re.findall(r"[A-Za-z_][A-Za-z_0-9]*", ej["code"]))
    vistos = [w for w in palabras(ej["narracion"]) if w in codigo or w.lower() == "python"]
    out, fallas = {}, []
    for w in dict.fromkeys(x.lower() for x in vistos):
        suena = LEXICO.get(w) if w in LEXICO else LEXICO_CODIGO.get(w, "SIN_REVISAR")
        if suena == "SIN_REVISAR":
            estado = "SIN_REVISAR"
        elif suena is None:
            estado = "OK_TAL_CUAL"
        else:
            estado = "CORREGIDO" if w not in [x.lower() for x in palabras(voz)] else "SIN_CORREGIR"
        fon = [r.phonemes for r in g2p(suena or w, voice="em_alex")]
        out[w] = {"estado": estado, "voz": suena or w, "fonemas": fon[0] if fon else None}
        if estado in ("SIN_REVISAR", "SIN_CORREGIR"):
            fallas.append("%s:%s" % (w, estado))
    return out, fallas


def main():
    from faster_whisper import WhisperModel
    from kokoro import KPipeline
    args = sys.argv[1:]
    suf = "v2tt"
    if args[:1] == ["--sufijo"]:
        suf, args = args[1], args[2:]
    SAL = EVI / ("_validacion_%s.json" % suf)
    g2p = KPipeline(lang_code="e", model=False)
    asr = WhisperModel("small", device="cpu", compute_type="int8")
    banco = {e["id"]: e for e in json.loads((POC / "_banco.json").read_text(encoding="utf-8"))}
    res = json.loads(SAL.read_text(encoding="utf-8")) if SAL.is_file() else {}
    for eid in args:
        orig = sorted((POC / "reels_v2_yt").glob(eid + "_*.mp4"))[0]
        nuevo = sorted(TTD.glob(eid + "_*_%s.mp4" % suf))[0]
        voz = ej_voz = banco[eid]["narracion"] if suf == "v2tt" else aplicar_lexico_codigo(banco[eid]["narracion"])
        terms, fallas = terminos(banco[eid], ej_voz, g2p)
        do, ro, ao = probe(orig); dn, rn, an = probe(nuevo)
        segs, _ = asr.transcribe(str(nuevo), language="es", beam_size=1)
        oido = " ".join(s.text for s in segs)
        sim = difflib.SequenceMatcher(None, norm(banco[eid]["narracion"]), norm(oido)).ratio()
        p = psnr(orig, nuevo, round(dn / 2, 2))
        misma_voz = voz == banco[eid]["narracion"]          # si la voz cambió, la duración cambia a propósito
        ok = (abs(do - dn) <= 0.2 or not misma_voz) and ro == rn and an and sim >= 0.80 and not fallas
        res[eid] = {"original": orig.name, "nuevo": nuevo.name, "dur_original": round(do, 2), "dur_nuevo": round(dn, 2),
                    "resolucion": rn, "audio": an, "lufs_nuevo": lufs(nuevo), "psnr_mitad": p,
                    "similitud_narracion": round(sim, 3), "oido": oido.strip()[:300], "texto_voz": voz,
                    "terminos": terms, "fallas_terminos": fallas, "veredicto": "VALIDADO" if ok else "REVISAR"}
        print(eid, res[eid]["veredicto"], "dur %.2f→%.2f" % (do, dn), rn, "sim %.2f" % sim, "psnr", p, "terminos", fallas or "ok", flush=True)
    SAL.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
