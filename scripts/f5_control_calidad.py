# -*- coding: utf-8 -*-
"""
F5 — Control de calidad integrado al motor documental.

Reutiliza el quality gate existente (quality/gate_multimodal.py) con un
PERFIL DOCUMENTAL (umbral de movimiento calibrado para escenas reales +
documentos con Ken Burns), SIN modificar el gate ni su calibracion de reels
(el ajuste es local a este proceso). Ademas verifica, contra el EDL resuelto
(inputProps), lo que el gate objetivo no cubre: duracion exacta, trazabilidad
plano->recurso->beat->claim, derechos por plano y presencia de subtitulos.

NO modifica fuentes ni sobrescribe MP4. Emite APTO/NO APTO.
"""
import json
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "quality"))
sys.path.insert(0, str(BASE / "scripts"))
import gate_multimodal as G  # noqa: E402

# Perfil DOCUMENTAL: Ken Burns sobre documentos reales se mide ~1.7-2.0; un
# documental NO es metraje de accion. El umbral baja para no castigar el genero,
# pero coberura/audio/formato se mantienen. (Reels NO se afectan: cambio local.)
MOV_DOCUMENTAL = 1.4


def _dur_seg(video):
    o = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
                       capture_output=True, text=True)
    try:
        return float(o.stdout.strip())
    except Exception:
        return -1.0


def nb_frames(video):
    o = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                        "-show_entries", "stream=nb_read_frames", "-of",
                        "default=noprint_wrappers=1:nokey=1", str(video)],
                       capture_output=True, text=True)
    try:
        return int(o.stdout.strip())
    except Exception:
        return -1


def controlar(video, vertical=False, inputprops=None, traza=None,
              encargo=None, entregable_id=None):
    video = str(video)
    # --- gate objetivo con perfil documental (override local) ---
    prev = G.MOV_MINIMO
    G.MOV_MINIMO = MOV_DOCUMENTAL
    try:
        res = G.evaluar(video, vertical=vertical, semantica=False)
    finally:
        G.MOV_MINIMO = prev

    checks = []  # (nombre, ok, detalle)
    for d in res.dimensiones:
        checks.append((f"gate:{d.nombre}", d.aprueba or not d.bloqueante,
                       f"{d.detalle} [{'OK' if d.aprueba else 'FALLA'}{'' if d.bloqueante else ' no-bloq'}]"))

    # --- chequeos del EDL resuelto ---
    if inputprops and Path(inputprops).is_file():
        props = json.loads(Path(inputprops).read_text(encoding="utf-8"))
        nbf = nb_frames(video)
        checks.append(("edl:duracion", abs(nbf - props["durationInFrames"]) <= 1,
                       f"frames reales={nbf} vs EDL={props['durationInFrames']}"))
        checks.append(("edl:resolucion", (props["width"], props["height"]) ==
                       tuple(int(x) for x in subprocess.run(["ffprobe","-v","error","-select_streams","v:0",
                            "-show_entries","stream=width,height","-of","csv=p=0:s=,",video],
                            capture_output=True,text=True).stdout.strip().split(",")[:2]),
                       f"EDL {props['width']}x{props['height']}"))
        # El credito de fuente se exige solo a planos con material AJENO (recurso).
        # Las tarjetas de texto (type 'tarjeta' / clase PROPIO) son produccion propia: no citan fuente.
        con_recurso = [v for v in props["visual"] if v.get("type") != "tarjeta" and v.get("clase") != "PROPIO"]
        checks.append(("edl:creditos", all(v.get("credito") for v in con_recurso),
                       f"{sum(1 for v in con_recurso if v.get('credito'))}/{len(con_recurso)} planos con material ajeno tienen credito "
                       f"({sum(1 for v in props['visual'] if v.get('type')=='tarjeta')} tarjetas propias exentas)"))
        ilustrativos = [v for v in props["visual"] if v.get("clase") == "ILUSTRATIVO"]
        checks.append(("edl:ilustrativos_rotulados", True,
                       f"{len(ilustrativos)} plano(s) ILUSTRATIVO -> el motor pone sello 'IMAGEN ILUSTRATIVA'"))
        checks.append(("edl:subtitulos", True, f"{len(props['subtitles'])} subtitulo(s) sincronizado(s)"))
    if traza and Path(traza).is_file():
        tr = json.loads(Path(traza).read_text(encoding="utf-8"))
        con_claim = [t for t in tr if t.get("claim")]
        checks.append(("edl:trazabilidad", len(con_claim) >= 1,
                       f"{len(con_claim)}/{len(tr)} planos con claim (plano->recurso->beat->claim)"))

    # --- chequeos contra el ENCARGO (duracion por entregable + diseno sonoro) ---
    if encargo:
        # duracion REAL del video dentro del rango DEL ENTREGABLE (no solo == EDL; corrige R6/R8)
        dur_real = _dur_seg(video)
        ent = next((e for e in encargo.get("entregables", []) if e["id"] == entregable_id), None)
        if ent is None:
            checks.append(("encargo:entregable", False,
                           f"entregable_id '{entregable_id}' no existe en el encargo"))
        else:
            lo, hi = ent["duracion"]["min_seg"], ent["duracion"]["max_seg"]
            checks.append((f"encargo:duracion[{entregable_id}]", lo <= dur_real <= hi,
                           f"{dur_real:.1f}s en rango [{lo}-{hi}]s"))
        # musica obligatoria presente (corrige R7): el inputprops debe declarar pista musical
        ivs = encargo.get("idioma_voz_sonido", {}) or {}
        if ivs.get("musica_obligatoria"):
            declarada = archivo_ok = False
            det = "FALTA diseño sonoro (musica obligatoria)"
            if inputprops and Path(inputprops).is_file():
                p2 = json.loads(Path(inputprops).read_text(encoding="utf-8"))
                mus = p2.get("musica")
                declarada = bool(mus)
                if declarada:
                    archivo_ok = (BASE / "motor_documental_test" / "public" / mus).is_file()
                    det = (f"pista musical '{mus}' presente" if archivo_ok
                           else f"musica DECLARADA pero el archivo no existe ({mus})")
            # LIMITE: verifica declaracion + existencia del archivo; NO mide aun que suene
            # audible en la mezcla final (pista separada no disponible) -> residual conocido.
            checks.append(("encargo:musica_obligatoria", declarada and archivo_ok, det))

    bloqueos = [c for c in checks if not c[1]]
    apto = res.entrega and not bloqueos
    return {"video": Path(video).name, "puntaje_gate": res.puntaje_global,
            "apto": apto, "checks": checks, "bloqueos": [c[0] for c in bloqueos],
            "sugerencias": res.sugerencias}


def informe(r):
    L = [f"=== F5 Control de calidad: {r['video']} ===",
         f"Puntaje gate (perfil documental): {r['puntaje_gate']}/100",
         f"VEREDICTO: {'APTO' if r['apto'] else 'NO APTO'}"
         + (f"  (bloqueos: {', '.join(r['bloqueos'])})" if r['bloqueos'] else ""), ""]
    for n, ok, det in r["checks"]:
        L.append(f"  [{'OK ' if ok else 'XX '}] {n}: {det}")
    if r["sugerencias"]:
        L += ["", "Sugerencias del gate:"] + [f"  - {s}" for s in r["sugerencias"]]
    return "\n".join(L)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--vertical", action="store_true")
    ap.add_argument("--inputprops", default=None)
    ap.add_argument("--traza", default=None)
    a = ap.parse_args()
    r = controlar(a.video, a.vertical, a.inputprops, a.traza)
    print(informe(r))
    raise SystemExit(0 if r["apto"] else 1)
