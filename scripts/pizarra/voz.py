# -*- coding: utf-8 -*-
"""Uso: <python de chatterbox_ab_env> voz.py <carpeta_tema>.
Voz Chatterbox POR TRAMOS (camino a): cada tramo se sintetiza aparte, así su inicio exacto en la línea de
tiempo se conoce por construcción. Dentro del tramo, los tiempos por palabra salen de whisper large-v3 GUIADO
(initial_prompt = texto exacto del tramo). Corre con el python de chatterbox_ab_env.
Salida: voz.wav (48 kHz mono) + tiempos.json"""
import json
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import torchaudio
from chatterbox.mtl_tts import ChatterboxMultilingualTTS
from faster_whisper import WhisperModel

import sys
AQUI = Path(sys.argv[1]).resolve()  # carpeta del tema (guion.json)
TMP = AQUI / "tramos"
TMP.mkdir(exist_ok=True)
g = json.loads((AQUI / "guion.json").read_text(encoding="utf-8"))
SR = 48000
tts = ChatterboxMultilingualTTS.from_pretrained(device="cuda")
piezas = []
for t in g["tramos"]:
    torch.manual_seed(g["voz"]["semilla"])
    wav = tts.generate(t["texto"], language_id="es", audio_prompt_path=g["voz"]["referencia"])
    crudo = TMP / f"{t['id']}_crudo.wav"
    torchaudio.save(str(crudo), wav, tts.sr)
    limpio = TMP / f"{t['id']}.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(crudo), "-af",
                    "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
                    "silenceremove=start_periods=1:start_threshold=-45dB,areverse",
                    "-ar", str(SR), "-ac", "1", str(limpio)], check=True)
    x, _ = sf.read(str(limpio))
    rms = np.sqrt(np.mean(x[np.abs(x) > 0.01] ** 2))  # nivel de la voz (sin silencios) igual en todos los tramos
    x = x * (0.1 / rms)
    sf.write(str(limpio), x, SR)
    piezas.append((t, limpio, x))
del tts
torch.cuda.empty_cache()

asr = WhisperModel("large-v3", device="cuda", compute_type="float16")
lead, pausa = 0.5, g["voz"]["pausa_s"]
pos, salida, tramos = lead, [np.zeros(int(lead * SR))], []
for t, limpio, x in piezas:
    segs, _ = asr.transcribe(str(limpio), language="es", word_timestamps=True, beam_size=5,
                             condition_on_previous_text=False, initial_prompt=t["texto"])
    pal = [{"w": w.word.strip(), "ini": round(pos + w.start, 3), "fin": round(pos + w.end, 3)} for s in segs for w in s.words]
    dur = len(x) / SR
    tramos.append({"id": t["id"], "texto": t["texto"], "ini": round(pos, 3), "fin": round(pos + dur, 3), "palabras": pal})
    print(t["id"], f"{pos:.2f}-{pos + dur:.2f}", " ".join(p["w"] for p in pal), flush=True)
    salida += [x, np.zeros(int(pausa * SR))]
    pos += dur + pausa
salida.append(np.zeros(int(1.5 * SR)))
v = np.concatenate(salida)
v = v / max(1.0, np.abs(v).max() / 0.9)
sf.write(str(AQUI / "voz.wav"), v, SR)
(AQUI / "tiempos.json").write_text(json.dumps({"duracion": round(len(v) / SR, 3), "tramos": tramos}, ensure_ascii=False, indent=1), encoding="utf-8")
print("OK duracion", round(len(v) / SR, 2))
