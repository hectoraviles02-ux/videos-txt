# -*- coding: utf-8 -*-
"""Uso: <python de chatterbox_ab_env> alinear.py <carpeta_tema>.
Rehace tiempos.json a partir de los tramos ya sintetizados (tramos/*.wav), con COMPROBACIÓN DE COBERTURA:
el whisper guiado a veces se salta una frase entera (medido 18-oct: «Hagamos el tramo más pequeño» del t5). Por tramo:
  1) guiado; si sus palabras = las del guion -> se usa;
  2) si no, transcripción libre y alineación con el guion (difflib sobre palabras normalizadas); las palabras del guion sin
     pareja (p. ej. «diez punto uno» que whisper escribe «10.1») toman tiempos interpolados entre sus vecinas.
Falla (SystemExit) si menos del 80 % de las palabras del guion quedan con tiempo medido. Corre con chatterbox_ab_env."""
import difflib
import importlib.util
import re
import json
import unicodedata
from pathlib import Path

import soundfile as sf
from faster_whisper import WhisperModel

import sys
AQUI = Path(sys.argv[1]).resolve()  # carpeta del tema (guion.json, tramos/, voz.wav)
g = json.loads((AQUI / "guion.json").read_text(encoding="utf-8"))
asr = WhisperModel("large-v3", device="cuda", compute_type="float16")


def norm(s):
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if c.isalnum())


def transcribir(wav, prompt):
    segs, _ = asr.transcribe(str(wav), language="es", word_timestamps=True, beam_size=5,
                             condition_on_previous_text=False, initial_prompt=prompt)
    return [{"w": w.word.strip(), "ini": w.start, "fin": w.end} for s in segs for w in s.words]


_spec = importlib.util.spec_from_file_location("cifras", Path(__file__).resolve().parent / "cifras.py")  # solo para alinear
_num = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_num)


def cifras_a_palabras(palabras):
    """El transcriptor libre escribe «20%», «500»; el guion dice «veinte por ciento», «quinientos». Se expande cada palabra
    con cifras a sus palabras (convertidor del laboratorio) y se reparte su tiempo, para poder alinear (medido 19-oct, t5)."""
    out = []
    for p in palabras:
        if not re.search(r"\d", p["w"]):
            out.append(p)
            continue
        partes = _num.preparar_para_locucion(p["w"]).split()
        paso = (p["fin"] - p["ini"]) / max(1, len(partes))
        out += [{"w": w, "ini": p["ini"] + k * paso, "fin": p["ini"] + (k + 1) * paso} for k, w in enumerate(partes)]
    return out


lead, pausa, SR = 0.5, g["voz"]["pausa_s"], 48000
pos, tramos, informe = lead, [], []
for t in g["tramos"]:
    wav = AQUI / "tramos" / f"{t['id']}.wav"
    dur = sf.info(str(wav)).frames / SR
    guion = t["texto"].split()
    gw = transcribir(wav, t["texto"])
    if [norm(x["w"]) for x in gw] == [norm(x) for x in guion]:
        pal, modo, medidas = [{"w": w, "ini": x["ini"], "fin": x["fin"]} for w, x in zip(guion, gw)], "guiado", len(guion)
    else:
        lw = cifras_a_palabras(transcribir(wav, None))
        sm = difflib.SequenceMatcher(a=[norm(x) for x in guion], b=[norm(x["w"]) for x in lw], autojunk=False)
        tiempos = [None] * len(guion)
        for a, b, n in sm.get_matching_blocks():
            for k in range(n):
                tiempos[a + k] = (lw[b + k]["ini"], lw[b + k]["fin"])
        medidas = sum(x is not None for x in tiempos)
        if medidas < 0.8 * len(guion):
            raise SystemExit(f"{t['id']}: solo {medidas}/{len(guion)} palabras con tiempo medido")
        # interpolar huecos entre vecinas medidas (o bordes del tramo)
        i = 0
        while i < len(guion):
            if tiempos[i] is None:
                j = i
                while j < len(guion) and tiempos[j] is None:
                    j += 1
                a0 = tiempos[i - 1][1] if i > 0 else 0.0
                b0 = tiempos[j][0] if j < len(guion) else dur
                paso = (b0 - a0) / (j - i)
                for k in range(i, j):
                    tiempos[k] = (a0 + paso * (k - i), a0 + paso * (k - i + 1))
                i = j
            else:
                i += 1
        pal, modo = [{"w": w, "ini": x[0], "fin": x[1]} for w, x in zip(guion, tiempos)], "libre+alineado"
    pal = [{"w": p["w"], "ini": round(pos + p["ini"], 3), "fin": round(pos + p["fin"], 3)} for p in pal]
    tramos.append({"id": t["id"], "texto": t["texto"], "ini": round(pos, 3), "fin": round(pos + dur, 3), "modo": modo, "palabras": pal})
    informe.append(f"{t['id']}: {modo}, {medidas}/{len(guion)} palabras medidas")
    print(informe[-1], flush=True)
    pos += dur + pausa
total = round(sf.info(str(AQUI / "voz.wav")).frames / SR, 3)
(AQUI / "tiempos.json").write_text(json.dumps({"duracion": total, "tramos": tramos, "cobertura": informe}, ensure_ascii=False, indent=1), encoding="utf-8")
print("OK", total)
