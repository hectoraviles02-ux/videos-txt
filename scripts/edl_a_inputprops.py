# -*- coding: utf-8 -*-
"""
F3 — Adaptador EDL -> inputProps para el componente generico Documental (Remotion).

GENERICO: no contiene tokens de ningun caso. Toma un directorio de EDL
(investigacion.json, guion.json, plan_montaje.json, narracion_map.json) y:
  1) valida con edl_validar (aborta si hay errores);
  2) resuelve cada recurso por su id en el manifiesto AV (verifica existencia +
     SHA256; rechaza PENDIENTE/REQUIERE-AUTORIZACION/NO-UTILIZABLE/supersedido);
  3) verifica los WAV de narracion (sha256 + duracion vs ventana del EDL);
  4) prepara assets en public/f3_render/ con nombres genericos por id;
     corta el audio_original del archivo_local (ffmpeg seek exacto);
  5) genera subtitulos 'literal_sincronizado': TIEMPOS por faster-whisper,
     TEXTO del procesado/*.json (acentuado, trazable) enlazado por hash_url;
  6) arma la tabla plano->recurso->beat->claim y la certeza por plano;
  7) emite inputProps.json + traza.json. NO renderiza.

Cuantizacion unica: frames = round(seg*fps).
"""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))
import edl_validar as E  # noqa: E402

PUBLIC = BASE / "motor_documental_test" / "public"
RENDER_DIR = PUBLIC / "f3_render"
PROCESADO = BASE / "procesado"

CERTEZA_DISPLAY = {
    "HECHO_DOCUMENTAL": "Hecho documental",
    "DECLARACION_ATRIBUIDA": "Declaración atribuida",
    "HECHO_REPORTADO_PRENSA": "Hecho reportado (prensa)",
    "INFERENCIA_PERIODISTICA": "Inferencia periodística",
    "PREGUNTA_ABIERTA": "Pregunta abierta",
}
FORMATOS = {"horizontal": (1920, 1080), "vertical": (1080, 1920)}


class AbortF3(Exception):
    pass


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with Path(p).open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def ffprobe_dur(p) -> float:
    o = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
                       capture_output=True, text=True)
    return float(o.stdout.strip())


def _hms_to_seg(s: str) -> float:
    parts = [float(x) for x in str(s).split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def _procesado_por_hash(url_hash: str):
    """Devuelve el registro procesado conforme cuyo url_hash coincide."""
    for jp in PROCESADO.glob("*/*.json"):
        if jp.name == "contenido.json" or "_borrador_original" in str(jp):
            continue
        try:
            d = json.loads(jp.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("url_hash") == url_hash:
            return d
    return None


def _subtitulos_literales(audio_wav: Path, texto: str, base_f: int, fps: int):
    """TIEMPOS por whisper (word_timestamps), TEXTO = `texto` (del procesado).
    Agrupa en lineas de ~7 palabras. Si el conteo difiere, alinea al minimo."""
    from faster_whisper import WhisperModel
    m = WhisperModel("small", device="cpu", compute_type="int8")
    segs, _ = m.transcribe(str(audio_wav), language="es", word_timestamps=True)
    starts = [w.start for s in segs for w in s.words]
    palabras = texto.split()
    n = min(len(starts), len(palabras))
    subs, linea, l_start = [], [], None
    for i in range(n):
        if l_start is None:
            l_start = starts[i]
        linea.append(palabras[i])
        fin_linea = (len(linea) >= 7) or (i == n - 1)
        if fin_linea:
            end = starts[i + 1] if i + 1 < n else ffprobe_dur(audio_wav)
            subs.append({"fromF": base_f + round(l_start * fps),
                         "durF": max(1, round((end - l_start) * fps)),
                         "text": " ".join(linea)})
            linea, l_start = [], None
    return subs


def _subs_narracion(texto, inF, durF, palabras_por_linea=9):
    """Parte la narracion en lineas de ~N palabras repartidas uniformemente en
    la ventana [inF, inF+durF]. Sin whisper: aproximacion por conteo de palabras."""
    pal = texto.split()
    if not pal:
        return []
    lineas = [" ".join(pal[i:i + palabras_por_linea]) for i in range(0, len(pal), palabras_por_linea)]
    # reparte por nº de palabras de cada linea (lineas mas largas duran mas)
    total = len(pal)
    subs, cursor = [], inF
    for ln in lineas:
        frac = len(ln.split()) / total
        d = max(1, round(durF * frac))
        subs.append({"fromF": cursor, "durF": d, "text": ln})
        cursor += d
    return subs


CACHE_VOZ = BASE / "motor_documental_test" / ".cache_tiempos_voz"  # fuera de RENDER_DIR (la suite lo redirige)
AVISOS_SUBS = []  # beats que cayeron a reparto uniforme (se reportan, nunca en silencio)


def _norm_tok(s):
    import re
    import unicodedata
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower())


def _tiempos_voz(wav: Path, palabras: list):
    """Inicio (s, relativo al WAV) de cada palabra de `palabras`, alineando el texto con los tiempos
    por palabra de whisper (cache por SHA del WAV). Palabras no alineadas: interpolacion lineal entre
    vecinas alineadas. None si la alineacion es pobre (<60 % de palabras)."""
    import difflib
    CACHE_VOZ.mkdir(parents=True, exist_ok=True)
    cp = CACHE_VOZ / f"{sha256(wav)}.json"
    if cp.is_file():
        ws = json.loads(cp.read_text(encoding="utf-8"))
    else:
        from faster_whisper import WhisperModel
        m = WhisperModel("small", device="cpu", compute_type="int8")
        segs, _ = m.transcribe(str(wav), language="es", word_timestamps=True)
        ws = [[w.word, round(w.start, 3)] for s in segs for w in s.words]
        cp.write_text(json.dumps(ws, ensure_ascii=False), encoding="utf-8")
    a = [_norm_tok(p) for p in palabras]
    b = [_norm_tok(w) for w, _ in ws]
    t = [None] * len(a)
    for bl in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(bl.size):
            t[bl.a + k] = ws[bl.b + k][1]
    conocidos = [i for i, x in enumerate(t) if x is not None]
    if len(conocidos) < 0.6 * len(a):
        return None
    dur = ffprobe_dur(wav)
    for i in range(len(t)):  # interpolar huecos (numeros escritos distinto, etc.)
        if t[i] is None:
            prev = max((j for j in conocidos if j < i), default=None)
            nxt = min((j for j in conocidos if j > i), default=None)
            t0, i0 = (t[prev], prev) if prev is not None else (0.0, -1)
            t1, i1 = (t[nxt], nxt) if nxt is not None else (dur, len(t))
            t[i] = t0 + (t1 - t0) * (i - i0) / (i1 - i0)
    for i in range(1, len(t)):  # monotono
        t[i] = max(t[i], t[i - 1])
    return t


def _subs_ventana(texto, inF, outF, fps, tiempos=None, palabras_por_linea=9):
    """Subtitulos de UNA ventana de narracion: el texto completo UNA sola vez, en lineas de <=N
    palabras (corte preferente tras punto/dos puntos/punto y coma). Contiguos, sin solape, dentro de
    [inF, outF). Tiempos: los de la voz si se dan; si no, reparto por conteo de palabras."""
    pal = texto.split()
    if not pal:
        return []
    lineas, cur = [], []
    for i, w in enumerate(pal):
        cur.append(i)
        if len(cur) >= palabras_por_linea or (len(cur) >= 4 and w[-1:] in ".:;?"):
            lineas.append(cur); cur = []
    if cur:
        lineas.append(cur)
    durF = outF - inF
    if tiempos:
        starts = [inF + round(tiempos[ln[0]] * fps) for ln in lineas]
        starts[0] = inF
    else:
        acum, starts = 0, []
        for ln in lineas:
            starts.append(inF + round(durF * acum / len(pal)))
            acum += len(ln)
    subs = []
    for k, ln in enumerate(lineas):
        a = min(max(starts[k], subs[-1]["fromF"] + 1 if subs else inF), outF - 1)
        b = starts[k + 1] if k + 1 < len(lineas) else outF
        b = min(max(b, a + 1), outF)
        if subs:
            subs[-1]["durF"] = a - subs[-1]["fromF"]
        subs.append({"fromF": a, "durF": b - a, "text": " ".join(pal[i] for i in ln)})
    subs[-1]["durF"] = outF - subs[-1]["fromF"]
    return subs


def _texto_pantalla(b):
    """Texto a mostrar como subtitulo de narracion: la version acentuada (texto_pantalla) si
    existe; si no, el texto con que se genero la voz. La equivalencia entre ambas (solo
    diacriticos/signos de apertura) la comprueba edl_validar."""
    return b.get("texto_pantalla") or b.get("texto") or b.get("narracion")


def construir(edl_dir, formato="horizontal"):
    d = Path(edl_dir)
    W, H = FORMATOS[formato]
    inv = json.loads((d / "investigacion.json").read_text(encoding="utf-8"))
    guion = json.loads((d / "guion.json").read_text(encoding="utf-8"))
    plan = json.loads((d / "plan_montaje.json").read_text(encoding="utf-8"))
    narr_map = json.loads((d / "narracion_map.json").read_text(encoding="utf-8"))["beats"]
    man = E.cargar_manifiesto()
    fps = plan["fps"]
    f = lambda seg: round(float(seg) * fps)  # cuantizacion unica

    # 1) validacion
    errs = E.validar_todo(d)
    if errs:
        raise AbortF3("EDL no valido: " + "; ".join(errs))

    # 2/3) verificar recursos + narracion
    def recurso_ok(rid):
        if rid not in man:
            raise AbortF3(f"recurso inexistente: {rid}")
        r = man[rid]
        if not E.recurso_aprobado(r):
            raise AbortF3(f"recurso no aprobado (derechos/estado): {rid}")
        al = r.get("archivo_local")
        if not al or not Path(al).is_file():
            raise AbortF3(f"archivo ausente: {rid} -> {al}")
        if r.get("hash_archivo") and sha256(al) != r["hash_archivo"]:
            raise AbortF3(f"SHA256 no coincide: {rid}")
        return r

    RENDER_DIR.mkdir(parents=True, exist_ok=True)
    claims = {c["id"]: c for c in inv["claims"]}
    beats = {b["id"]: b for b in guion["beats"]}
    import importlib
    cm = importlib.import_module("comun")

    def claim_por_recurso(r):
        """claim cuya fuente.url hashea al hash_url del recurso."""
        if not r.get("hash_url"):
            return None
        for c in inv["claims"]:
            for fu in c.get("fuentes", []):
                if fu.get("url") and cm.calcular_hash(fu["url"]) == r["hash_url"]:
                    return c
        return None

    def beat_por_claim(cid):
        for b in guion["beats"]:
            if cid in b.get("claim_ids", []):
                return b["id"]
        return None

    audio_items, traza = [], []
    # ── pista de audio ── (y precalcular enlace de cada clip: beat + claim)
    clips_audio = []  # (inF, outF, beat_id, claim_id, wav_original|None)
    for i, a in enumerate(plan["pista_audio"]):
        inF, outF = f(a["in"]), f(a["out"])
        if a["tipo"] == "narracion_tts":
            b = a["beat_id"]
            nm = narr_map[b]
            src = Path(nm["archivo_local"])
            if sha256(src) != nm["sha256"]:
                raise AbortF3(f"narracion {b}: sha256 no coincide")
            if abs(nm["duracion_seg"] - (a["out"] - a["in"])) > 0.2:
                raise AbortF3(f"narracion {b}: duracion {nm['duracion_seg']} != ventana {a['out']-a['in']}")
            # la duracion declarada en el mapa no basta: se mide el WAV real (evita recorte silencioso)
            real = ffprobe_dur(src)
            if abs(real - (a["out"] - a["in"])) > 0.2:
                raise AbortF3(f"narracion {b}: WAV real dura {real:.2f}s != ventana {a['out']-a['in']:.2f}s")
            dst = RENDER_DIR / f"narr_{b}.wav"
            shutil.copy2(src, dst)
            audio_items.append({"type": "narracion", "src": f"f3_render/{dst.name}",
                                "fromF": inF, "durF": outF - inF})
            cids = beats.get(b, {}).get("claim_ids", [])
            clips_audio.append((inF, outF, b, (cids[0] if cids else None), None))
        elif a["tipo"] == "audio_original":
            r = recurso_ok(a["recurso_id"])
            si, so = _hms_to_seg(a["src_in"]), _hms_to_seg(a["src_out"])
            dst = RENDER_DIR / f"audio_orig_{i}.wav"
            # opcional por EDL: igualar la sonoridad de las declaraciones a la de la narracion
            # (loudnorm EBU R128); sin el campo, el audio original pasa tal cual (compat f2/f6).
            af = []
            if plan.get("normalizar_declaraciones_lufs") is not None:
                af = ["-af", f"loudnorm=I={plan['normalizar_declaraciones_lufs']}:TP=-1.5:LRA=11"]
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", r["archivo_local"],
                            "-ss", str(si), "-to", str(so), *af, "-ar", "48000", "-ac", "2", str(dst)], check=True)
            audio_items.append({"type": "original", "src": f"f3_render/{dst.name}",
                                "fromF": inF, "durF": outF - inF})
            cl = claim_por_recurso(r)
            clips_audio.append((inF, outF, beat_por_claim(cl["id"]) if cl else None,
                                cl["id"] if cl else None, str(dst)))

    def enlace_por_tiempo(inF, outF):
        """Devuelve (beat_id, claim_id, wav_original) del clip de audio de mayor solape."""
        best, bov = (None, None, None), 0
        for cinF, coutF, b, cid, wav in clips_audio:
            ov = min(outF, coutF) - max(inF, cinF)
            if ov > bov:
                bov, best = ov, (b, cid, wav)
        return best

    visual, subtitles = [], []
    for p in plan["pista_visual"]:
        inF, outF = f(p["in"]), f(p["out"])
        beat_id, claim_id, wav_orig = enlace_por_tiempo(inF, outF)
        cert = ""
        if claim_id and claims.get(claim_id):
            cert = CERTEZA_DISPLAY.get(claims[claim_id]["nivel_certeza"], "")
        # ── Sin recurso catalogado: tarjeta de texto o infografia (datos propios) ──
        if p.get("tipo") in ("tarjeta", "infografia") or (not p.get("recurso_id") and p.get("tratamiento") != "recreacion"):
            trat = p.get("tratamiento") or ("infografia" if p.get("datos") else "rotulo_pleno")
            visual.append({"plano": p["plano"], "type": "tarjeta", "tratamiento": trat, "src": "",
                           "efecto": p.get("efecto", "fade"), "fromF": inF, "durF": outF - inF,
                           "clase": p.get("clase", "PROPIO"), "certeza": cert,
                           "credito": p.get("credito", ""), "muted": True,
                           "texto": p.get("texto", ""), "kicker": p.get("kicker", ""),
                           "datos": p.get("datos")})
            traza.append({"plano": p["plano"], "recurso": None, "beat": beat_id, "claim": claim_id,
                          "clase": p.get("clase", "PROPIO"),
                          "derechos_uso": p.get("derechos_uso", "Tarjeta narrativa: texto propio, sin material ajeno.")})
            continue
        # ── Recreacion (imagen SD u otra): por src bajo public/, con ROTULO obligatorio ──
        if p.get("tratamiento") == "recreacion":
            if not p.get("src"):
                raise AbortF3(f"plano {p['plano']}: recreacion sin src")
            if not p.get("rotulo"):
                raise AbortF3(f"plano {p['plano']}: recreacion sin rotulo (obligatorio)")
            visual.append({"plano": p["plano"], "type": "image", "tratamiento": "recreacion",
                           "src": p["src"], "efecto": p.get("efecto", "push"), "fromF": inF, "durF": outF - inF,
                           "clase": "RECREACION", "certeza": cert, "credito": p.get("credito", ""),
                           "muted": True, "rotulo": p["rotulo"]})
            traza.append({"plano": p["plano"], "recurso": None, "beat": beat_id, "claim": claim_id,
                          "clase": "RECREACION", "derechos_uso": "Recreacion propia (SD) rotulada; NO es evidencia del caso."})
            continue
        r = recurso_ok(p["recurso_id"])
        ext = Path(r["archivo_local"]).suffix.lower()
        tipo = "video" if r["tipo"] == "video" else "image"
        segdur = p["out"] - p["in"]
        TOL = 0.12  # s
        # ── VIDEO: cobertura + corte por ventana src_in/src_out (cierra D04-visual/D06) ──
        if tipo == "video":
            srcdur = ffprobe_dur(r["archivo_local"])
            tiene_ventana = p.get("src_in") is not None and p.get("src_out") is not None
            if tiene_ventana:
                si, so = _hms_to_seg(p["src_in"]), _hms_to_seg(p["src_out"])
                if not (0 <= si < so <= srcdur + 1e-3):
                    raise AbortF3(f"plano {p['plano']} ({p['recurso_id']}): ventana src {si}..{so} "
                                  f"fuera de [0,{srcdur:.3f}]")
                if abs((so - si) - segdur) > TOL:
                    raise AbortF3(f"plano {p['plano']} ({p['recurso_id']}): ventana src dura "
                                  f"{so - si:.3f}s != plano {segdur:.3f}s")
                # sincronia: si hay audio_original del MISMO ARCHIVO que solapa en el timeline,
                # el video debe arrancar donde arranca el audio MAS el desfase del plano respecto
                # al audio (permite varios planos bajo una misma declaracion).
                for a in plan["pista_audio"]:
                    if a.get("tipo") != "audio_original":
                        continue
                    ra = man.get(a.get("recurso_id"), {})
                    if ra.get("archivo_local") != r.get("archivo_local"):
                        continue
                    if min(outF, f(a["out"])) - max(inF, f(a["in"])) <= 0:
                        continue
                    esperado = _hms_to_seg(a["src_in"]) + (p["in"] - a["in"])
                    if abs(si - esperado) > TOL:
                        raise AbortF3(f"plano {p['plano']} ({p['recurso_id']}): video desincronizado del audio "
                                      f"original (src_in video={si:.3f} != esperado {esperado:.3f} = audio src_in "
                                      f"{_hms_to_seg(a['src_in']):.3f} + desfase {p['in'] - a['in']:.3f})")
                dst = RENDER_DIR / f"{p['recurso_id']}_p{p['plano']}.mp4"  # recorte re-encodeado a H.264/MP4
                vf = []
                if p.get("recorte"):  # [x,y,w,h] en px de la fuente: encuadre (p.ej. sacar sellos del medio)
                    x, y, w, h = p["recorte"]
                    vf = ["-vf", f"crop={w}:{h}:{x}:{y},scale={W}:{H}:flags=lanczos"]
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(si), "-to", str(so),
                                "-i", r["archivo_local"], *vf, "-an", "-c:v", "libx264", "-crf", "19",
                                "-pix_fmt", "yuv420p", str(dst)], check=True)
            else:
                if srcdur < segdur - TOL:
                    raise AbortF3(f"plano {p['plano']} ({p['recurso_id']}): video dura {srcdur:.3f}s < plano "
                                  f"{segdur:.3f}s (cobertura visual insuficiente)")
                if srcdur > segdur + TOL:
                    raise AbortF3(f"plano {p['plano']} ({p['recurso_id']}): video dura {srcdur:.3f}s > plano "
                                  f"{segdur:.3f}s SIN ventana src_in/src_out (riesgo de metraje equivocado)")
                dst = RENDER_DIR / f"{p['recurso_id']}{ext}"
                if not dst.exists():
                    shutil.copy2(r["archivo_local"], dst)
        else:  # imagen: Ken Burns/paneo sobre fija, sin problema de cobertura temporal
            dst = RENDER_DIR / f"{p['recurso_id']}{ext}"
            # una imagen muy grande (p.ej. 4000x3000) no termina de pintarse en el primer frame del
            # plano: con corte directo se ve un destello negro/parcial. Se reduce SOLO la copia de
            # render a <=1.5x el lienzo (el original en raw/biblioteca no se toca).
            o = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                                r["archivo_local"]], capture_output=True, text=True).stdout.strip().split(",")
            iw, ih = (int(o[0]), int(o[1])) if len(o) >= 2 and o[0].isdigit() else (0, 0)
            if iw > 1.5 * W or ih > 1.5 * H:
                dst = RENDER_DIR / f"{p['recurso_id']}_r{int(1.5 * W)}{ext}"
                if not dst.exists():
                    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", r["archivo_local"], "-vf",
                                    f"scale={int(1.5 * W)}:{int(1.5 * H)}:force_original_aspect_ratio=decrease:flags=lanczos",
                                    "-q:v", "2", str(dst)], check=True)
            elif not dst.exists():
                shutil.copy2(r["archivo_local"], dst)
        # tratamiento explicito; si no viene, se infiere del efecto (compat f3/f6)
        trat = p.get("tratamiento")
        if not trat:
            ef = (p.get("efecto") or "").lower()
            trat = "clip" if tipo == "video" else "paneo" if ef.startswith("paneo") else "ficha"
        _vis = {"plano": p["plano"], "type": tipo, "tratamiento": trat,
                "src": f"f3_render/{dst.name}", "efecto": p["efecto"], "fromF": inF, "durF": outF - inF,
                "clase": p["clase"], "certeza": cert, "credito": p["credito"],
                "muted": tipo == "video", "rotulo": p.get("rotulo")}
        if p.get("cubrir"):       # rectangulos para cubrir nombres de menores quemados (D10)
            _vis["cubrir"] = p["cubrir"]
        if p.get("hablante"):     # lower-third de identificacion de voz (nombre + rol)
            _vis["hablante"] = p["hablante"]
        visual.append(_vis)
        traza.append({"plano": p["plano"], "recurso": p["recurso_id"], "beat": beat_id,
                      "claim": claim_id, "clase": p["clase"], "derechos_uso": p["derechos_uso"]})
        # subtitulos literales EXPLICITOS (transcripcion de la fuente, trazable al audio):
        # [{texto, desde_local_f, hasta_local_f}] -> se emiten relativos al inicio del plano.
        for sl in p.get("subtitulos_literales", []):
            subtitles.append({"fromF": inF + int(sl["desde_local_f"]),
                              "durF": max(1, int(sl["hasta_local_f"]) - int(sl["desde_local_f"])),
                              "text": sl["texto"]})
        # subtitulo de narracion (texto del beat) para planos con imagen/video
        # subtitulos literales: TEXTO del procesado del claim enlazado; TIEMPOS por whisper del wav original
        if p.get("subtitulo") == "literal_sincronizado" and claim_id and wav_orig:
            cl = claims[claim_id]
            texto = None
            for fu in cl.get("fuentes", []):
                if fu.get("url"):
                    pr = _procesado_por_hash(cm.calcular_hash(fu["url"]))
                    if pr and pr.get("segmentos"):
                        texto = " ".join(s["texto"] for s in pr["segmentos"])
                        break
            if texto:
                subtitles += _subtitulos_literales(Path(wav_orig), texto, inF, fps)

    # ── D1: subtitulos de narracion UNA vez por ventana narracion_tts (no por plano) ──
    AVISOS_SUBS.clear()
    for a in plan["pista_audio"]:
        if a["tipo"] != "narracion_tts":
            continue
        bt = beats.get(a["beat_id"], {})
        txt = _texto_pantalla(bt)
        if not txt or not bt.get("mostrar_subtitulo_narracion", True):
            continue
        tiempos = None
        if plan.get("subtitulos_narracion") == "voz":
            tiempos = _tiempos_voz(Path(narr_map[a["beat_id"]]["archivo_local"]), txt.split())
            if tiempos is None:
                AVISOS_SUBS.append(f"{a['beat_id']}: alineacion con la voz pobre -> reparto uniforme en su ventana")
                print("AVISO subtitulos:", AVISOS_SUBS[-1])
        subtitles += _subs_ventana(txt, f(a["in"]), f(a["out"]), fps, tiempos)

    # ── D2: transiciones. Con plan.transiciones == "capitulos": corte directo dentro del capitulo y
    #    fundido solo al cambiar de capitulo (y al inicio/fin). Sin el campo: fundido en todo plano
    #    (comportamiento previo, compat con EDLs aprobadas). Los insertos heredan el capitulo previo.
    if plan.get("transiciones") == "capitulos":
        vis = sorted(visual, key=lambda v: v["fromF"])
        cap_prev = None
        for v in vis:
            b_id, _, _ = enlace_por_tiempo(v["fromF"], v["fromF"] + v["durF"])
            cap = beats.get(b_id, {}).get("capitulo") if b_id else None
            v["capitulo"] = cap or cap_prev
            cap_prev = v["capitulo"]
        for i, v in enumerate(vis):
            v["fundidoEntrada"] = i == 0 or vis[i - 1]["capitulo"] != v["capitulo"]
            v["fundidoSalida"] = i == len(vis) - 1 or vis[i + 1]["capitulo"] != v["capitulo"]
        for x, y in zip(vis, vis[1:]):  # con corte directo un hueco de 1 frame se ve como destello
            if x["fromF"] + x["durF"] != y["fromF"]:
                raise AbortF3(f"planos {x['plano']}->{y['plano']} no contiguos "
                              f"({x['fromF'] + x['durF']} != {y['fromF']}): con corte directo se veria un destello")

    durF = max(f(p["out"]) for p in plan["pista_visual"])
    # el video no puede terminar antes que el audio (Remotion cortaria la narracion en silencio)
    finA = max((a["fromF"] + a["durF"] for a in audio_items), default=0)
    if finA > durF:
        raise AbortF3(f"audio termina en el frame {finA} y la pista visual en {durF}: narracion recortada")
    # cobertura visual continua: sin huecos negros entre planos (tolerancia 1 frame)
    cursor = 0
    for p in sorted(plan["pista_visual"], key=lambda x: x["in"]):
        if f(p["in"]) - cursor > 1:
            raise AbortF3(f"hueco visual sin plano entre frame {cursor} y {f(p['in'])} (plano {p['plano']})")
        cursor = max(cursor, f(p["out"]))
    props = {"width": W, "height": H, "fps": fps, "durationInFrames": durF,
             "formato": formato, "visual": visual, "audio": [{k: v for k, v in a.items() if not k.startswith("_")} for a in audio_items],
             "subtitles": subtitles}
    # pasa-through de diseno sonoro y entregable (para los gates de encargo)
    if plan.get("musica"):
        props["musica"] = plan["musica"]
        props["musicaVol"] = plan.get("musicaVol", 0.12)
    if plan.get("entregable_id"):
        props["entregable_id"] = plan["entregable_id"]
    return props, traza


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("edl_dir")
    ap.add_argument("--formato", choices=list(FORMATOS), default="horizontal")
    ap.add_argument("--out", required=True)
    ap.add_argument("--traza", default=None)
    a = ap.parse_args()
    try:
        props, traza = construir(a.edl_dir, a.formato)
    except AbortF3 as e:
        print("ABORTADO:", e)
        return 2
    Path(a.out).write_text(json.dumps(props, ensure_ascii=False, indent=2), encoding="utf-8")
    if a.traza:
        Path(a.traza).write_text(json.dumps(traza, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK {a.formato}: {props['width']}x{props['height']} {props['durationInFrames']}f "
          f"| visual={len(props['visual'])} audio={len(props['audio'])} subs={len(props['subtitles'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
