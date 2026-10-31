# -*- coding: utf-8 -*-
"""Valida un mp4 contra las reglas de monetización de una plataforma. SOLO REPORTA: no modifica nada.

Uso:
  python scripts/validar_monetizacion.py <video.mp4> --plataforma youtube|tiktok|facebook [metadatos]
  python scripts/validar_monetizacion.py --verificar-fuentes [--md archivo.md ...]

Metadatos (si se omiten, se infieren de forma conservadora y se marca «inferido»):
  --musica  ninguna|meta_sound_collection|youtube_audio_library|youtube_audio_library_cc|tiktok_cml|
            otra_con_licencia|otra_sin_licencia|desconocida
            (inferencia por nombre: *_v2yt*/*_YT* -> youtube_audio_library; *_v2.mp4/*_FB* -> meta_sound_collection)
  --voz-ia si|no            voz sintética (default: si — toda la producción usa TTS)
  --voz-realista si|no      ¿la voz IA suena como persona real? (default: si — criterio conservador)
  --voz-clonada-tercero si|no   ¿voz clonada de una persona real distinta del creador? (default: desconocido,
                                hasta que se confirme el origen de la referencia de Chatterbox)
  --escena-realista-ia si|no    ¿imágenes/video fotorrealistas generados con IA de personas o hechos reales? (default: no)
  --musica-ia si|no         ¿la música fue generada con IA? (default: no)
  --etiqueta-ia si|no|desconocido   ¿se activó la etiqueta de IA al publicar? (default: desconocido)
  --contenido-marca si|no   --aviso-comercial si|no   --atribucion si|no   --contenido-terceros si|no

Reglas: config/reglas_monetizacion.json. Estados: CUMPLE · INCUMPLE · REVISAR (manual o dato faltante) · NO_APLICA · INFO.
Solo una regla bloqueante y confirmada (confirmado=true, con cita textual) puede dar INCUMPLE con salida 1; una regla
con confirmado=false nunca da INCUMPLE: se reporta como REVISAR. La elegibilidad se separa en requisitos ACTUALES y
CAMBIOS FUTUROS (campo "vigencia"). La etiqueta de IA y la licencia musical son verificaciones separadas.
--verificar-fuentes: comprueba que cada «cita» del JSON exista literal en algún segmento del procesado/*.json
citado; con --md, revisa también las citas con formato `procesado/....json — «cita»` de documentos .md.
Salida: 0 si no hay INCUMPLE bloqueante (o si todas las citas existen), 1 en caso contrario.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGLAS = REPO / "config" / "reglas_monetizacion.json"
BOOL = {"si": True, "sí": True, "no": False, "desconocido": None}


def _utf8():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass


def _ffprobe() -> str:
    for c in (shutil.which("ffprobe"), str(REPO / "bin" / "ffprobe.exe")):
        if c and Path(c).exists():
            return c
    raise SystemExit("No se encontró ffprobe (PATH o bin/ffprobe.exe)")


def _ffmpeg() -> str | None:
    for c in (shutil.which("ffmpeg"), str(REPO / "bin" / "ffmpeg.exe")):
        if c and Path(c).exists():
            return c
    return None


def medir(video: Path) -> dict:
    out = subprocess.run([_ffprobe(), "-v", "error", "-show_entries",
                          "format=duration:stream=codec_type,width,height", "-of", "json", str(video)],
                         capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    vs = [s for s in j.get("streams", []) if s.get("codec_type") == "video"]
    return {"duracion": float(j["format"]["duration"]),
            "tiene_audio": any(s.get("codec_type") == "audio" for s in j.get("streams", [])),
            "ancho": vs[0].get("width") if vs else None, "alto": vs[0].get("height") if vs else None}


def fraccion_estatica(video: Path, dur: float) -> float | None:
    """Fracción del video en que la imagen no cambia (ffmpeg freezedetect, umbral de ruido 0.003, >=2 s)."""
    ff = _ffmpeg()
    if not ff or dur <= 0:
        return None
    r = subprocess.run([ff, "-hide_banner", "-nostats", "-i", str(video), "-vf",
                        "freezedetect=n=0.003:d=2", "-map", "0:v:0", "-f", "null", "-"],
                       capture_output=True, text=True)
    total = sum(float(x) for x in re.findall(r"freeze_duration:\s*([\d.]+)", r.stderr))
    ini = re.findall(r"freeze_start:\s*([\d.]+)", r.stderr)
    fin = re.findall(r"freeze_end:\s*([\d.]+)", r.stderr)
    if len(ini) > len(fin):  # congelado hasta el final
        total += dur - float(ini[-1])
    return min(total / dur, 1.0)


def inferir_musica(video: Path) -> str:
    n = video.name.lower()
    if "_v2yt" in n or "_yt" in n:
        return "youtube_audio_library"
    if n.endswith("_v2.mp4") or "_fb" in n:
        return "meta_sound_collection"
    return "desconocida"


def _cumple_cond(cond: dict, m: dict) -> bool | None:
    """True si TODAS las condiciones se cumplen; None si falta un dato necesario."""
    for k, v in cond.items():
        if k == "duracion_mayor_a":
            if not m["duracion"] > v:
                return False
        elif k == "musica_en":
            if m["musica"] not in v:
                return False
        elif k == "musica_no_en":
            if m["musica"] in v:
                return False
        else:
            if m.get(k) is None:
                return None
            if m.get(k) != v:
                return False
    return True


def evaluar(regla: dict, m: dict) -> tuple[str, str]:
    v = regla["verificacion"]
    t = v["tipo"]
    if t == "informativa":
        return "INFO", ""
    if t == "manual":
        return "REVISAR", v.get("pregunta", "revisión manual")
    if t == "duracion_minima":
        d = m["duracion"]
        if d < v["seg"]:
            return "INCUMPLE", "dura %.1f s; mínimo %d s" % (d, v["seg"])
        if v.get("seg_recomendado") and d < v["seg_recomendado"]:
            return "REVISAR", "dura %.1f s: en el límite; recomendado >= %d s" % (d, v["seg_recomendado"])
        return "CUMPLE", "dura %.1f s" % d
    if t == "musica_fuente":
        mu = m["musica"]
        src = " (inferido)" if m.get("_musica_inferida") else ""
        if mu in v["permitidas"]:
            return "CUMPLE", "música: %s%s" % (mu, src)
        if mu in v.get("prohibidas", []):
            return "INCUMPLE", "música: %s%s no permitida" % (mu, src)
        return "REVISAR", "música: %s%s — licencia no confirmada para esta plataforma" % (mu, src)
    if t == "estatico":
        f = m.get("fraccion_estatica")
        if not m["tiene_audio"]:
            return "INCUMPLE", "sin pista de audio (sin narración)"
        if f is None:
            return "REVISAR", "no se pudo medir imagen estática (falta ffmpeg)"
        if f > v["max_fraccion"]:
            return "INCUMPLE", "%.0f%% del video sin cambio de imagen" % (f * 100)
        return "REVISAR", "%.0f%% estático; con narración. Confirmar a mano que no es solo texto" % (f * 100)
    if t == "condicion_incumple":
        r = _cumple_cond(v["si"], m)
        if r is None:
            return "REVISAR", "falta dato: %s" % ", ".join(k for k in v["si"] if m.get(k) is None)
        return ("INCUMPLE", "se da la condición %s" % v["si"]) if r else ("CUMPLE", "no se da la condición")
    if t == "requiere_si":
        if "si_alguno" in v:
            vals = [_cumple_cond({k: x}, m) for k, x in v["si_alguno"].items()]
            aplica = True if any(x is True for x in vals) else (None if any(x is None for x in vals) else False)
            motivo = ", ".join(k for k, x in v["si_alguno"].items() if m.get(k) == x)
        else:
            aplica = _cumple_cond(v["si"], m) if v["si"] else True
            motivo = ", ".join("%s=%s" % kv for kv in v["si"].items())
        if aplica is None:
            return "REVISAR", "falta dato para decidir si aplica"
        if not aplica:
            return "NO_APLICA", ""
        faltan = []
        for k, x in v["entonces"].items():
            val = m.get(k)
            if val is None:
                return "REVISAR", "aplica (%s) y no se sabe si %s" % (motivo or "siempre", k)
            if val != x:
                faltan.append(k)
        return ("INCUMPLE", "aplica (%s) y falta: %s" % (motivo or "siempre", ", ".join(faltan))) if faltan \
            else ("CUMPLE", "aplica (%s) y está" % (motivo or "siempre"))
    return "REVISAR", "tipo de verificación desconocido: %s" % t


def validar(video: Path, plataforma: str, meta: dict) -> int:
    cfg = json.loads(REGLAS.read_text(encoding="utf-8"))
    p = cfg["plataformas"][plataforma]
    m = medir(video)
    m["fraccion_estatica"] = fraccion_estatica(video, m["duracion"])
    m.update(meta)
    if m.get("musica") is None:
        m["musica"] = inferir_musica(video)
        m["_musica_inferida"] = True
    print("VALIDAR MONETIZACIÓN — %s (solo reporta, no modifica nada)" % plataforma.upper())
    print("Reglas: %s (revisión %s)" % (REGLAS, cfg["fecha_revision"]))
    print("Video : %s" % video.resolve())
    print("Medido: %.1f s · %sx%s · audio=%s · imagen estática=%s" % (
        m["duracion"], m["ancho"], m["alto"], "sí" if m["tiene_audio"] else "NO",
        "%.0f%%" % (m["fraccion_estatica"] * 100) if m["fraccion_estatica"] is not None else "?"))
    print("Metadatos: " + " · ".join("%s=%s%s" % (k, m.get(k), " (inferido)" if k in m.get("_inferidos", ())
                                      or (k == "musica" and m.get("_musica_inferida")) else "")
                                      for k in ("musica", "voz_ia", "voz_ia_realista", "voz_clonada_tercero",
                                                "escena_realista_ia", "musica_ia", "etiqueta_ia",
                                                "contenido_marca", "aviso_comercial", "contenido_terceros")))
    print("\nELEGIBILIDAD DE LA CUENTA (%s) — se revisa en la app, no en el video:" % p["programa"])
    for vig in ("actual", "futuro"):
        grupo = [e for e in p["elegibilidad"] if e.get("vigencia", "actual") == vig]
        if not grupo:
            continue
        print("  %s:" % ("REQUISITOS ACTUALES" if vig == "actual" else "CAMBIOS FUTUROS DOCUMENTADOS"))
        for e in grupo:
            extra = "" if e.get("confirmado", True) else "  [NO CONFIRMADO]"
            if e.get("vigencia_desde"):
                extra += "  [desde %s]" % e["vigencia_desde"]
            if e.get("estado_cuenta"):
                extra += "  [cuenta: %s]" % e["estado_cuenta"]
            print("    · %s%s" % (e["descripcion"], extra))
    print("\nREGLAS DEL VIDEO:")
    cuenta = {}
    bloq = 0
    for r in p["reglas"]:
        if r.get("vigencia", "actual") != "actual":
            continue  # las reglas futuras no se evalúan contra el video de hoy
        est, det = evaluar(r, m)
        if est == "INCUMPLE" and r.get("confirmado") is False:
            # una hipótesis no confirmada nunca produce INCUMPLE: solo pide revisión
            est, det = "REVISAR", "(regla no confirmada) " + det
        cuenta[est] = cuenta.get(est, 0) + 1
        if est == "INCUMPLE" and r["nivel"] == "bloqueante" and r.get("confirmado", True) is True:
            bloq += 1
        marca = {"CUMPLE": "✓", "INCUMPLE": "✗", "REVISAR": "?", "NO_APLICA": "-", "INFO": "i"}[est]
        conf = "" if r.get("confirmado", True) else " [no confirmado]"
        print("  %s %-9s %-13s %s%s" % (marca, est, "[" + r["nivel"] + "]", r["id"], conf))
        print("      %s" % r["descripcion"])
        if det:
            print("      → %s" % det)
        print("      fuente: %s — «%s»" % (r["fuente"]["ruta"], r["fuente"]["cita"][:110]
                                        + ("…" if len(r["fuente"]["cita"]) > 110 else "")))
    print("\nRESUMEN: " + " · ".join("%s=%d" % kv for kv in sorted(cuenta.items()))
          + " · bloqueantes incumplidas=%d" % bloq)
    return 1 if bloq else 0


def _segmentos(ruta: str) -> list[str] | None:
    f = REPO / ruta
    if not f.exists():
        return None
    return [s.get("texto", "") for s in json.loads(f.read_text(encoding="utf-8")).get("segmentos", [])]


def verificar_fuentes(mds: list[str]) -> int:
    cfg = json.loads(REGLAS.read_text(encoding="utf-8"))
    citas = []
    for plat, p in cfg["plataformas"].items():
        for sec in ("elegibilidad", "reglas"):
            for r in p[sec]:
                for campo in ("fuente", "fuente_secundaria"):
                    if campo in r:
                        citas.append(("reglas_monetizacion.json:%s" % r["id"], r[campo]["ruta"], r[campo]["cita"]))
                for extra in r.get("fuentes_adicionales", []):
                    citas.append(("reglas_monetizacion.json:%s" % r["id"], extra["ruta"], extra["cita"]))
                if "fuente" not in r:
                    citas.append(("reglas_monetizacion.json:%s" % r["id"], "(sin fuente)", ""))
    patron = re.compile(r"(procesado/[^\s»)`]+\.json)\s*[—-]\s*«([^»]+)»")
    for md in mds:
        txt = Path(md).read_text(encoding="utf-8")
        for i, linea in enumerate(txt.splitlines(), 1):
            for ruta, cita in patron.findall(linea):
                citas.append(("%s:%d" % (Path(md).name, i), ruta, cita))
    malas = 0
    for donde, ruta, cita in citas:
        segs = _segmentos(ruta)
        if segs is None:
            print("✗ %s — no existe %s" % (donde, ruta))
            malas += 1
        elif not cita or not any(cita in s for s in segs):
            print("✗ %s — cita no encontrada literal en %s: «%s»" % (donde, ruta, cita[:90]))
            malas += 1
    print("Citas verificadas: %d · no encontradas: %d" % (len(citas), malas))
    return 1 if malas else 0


def main() -> int:
    _utf8()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", nargs="?")
    ap.add_argument("--plataforma", choices=["youtube", "tiktok", "facebook"])
    ap.add_argument("--verificar-fuentes", action="store_true")
    ap.add_argument("--md", nargs="*", default=[])
    ap.add_argument("--musica")
    for k in ("voz-ia", "voz-realista", "voz-clonada-tercero", "escena-realista-ia", "musica-ia", "etiqueta-ia",
              "contenido-marca", "aviso-comercial", "atribucion", "contenido-terceros"):
        ap.add_argument("--" + k, choices=["si", "sí", "no", "desconocido"])
    a = ap.parse_args()
    if a.verificar_fuentes:
        return verificar_fuentes(a.md)
    if not a.video or not a.plataforma:
        ap.error("indica <video.mp4> y --plataforma")
    defaults = {"voz_ia": "si", "voz_realista": "si", "voz_clonada_tercero": "desconocido", "escena_realista_ia": "no",
                "musica_ia": "no", "etiqueta_ia": "desconocido", "contenido_marca": "no", "aviso_comercial": "no",
                "atribucion": "no", "contenido_terceros": "no"}
    meta, inferidos = {}, []
    for k, d in defaults.items():
        val = getattr(a, k)
        if val is None:
            val = d
            inferidos.append(k)
        meta[k] = BOOL[val]
    meta["voz_ia_realista"] = meta.pop("voz_realista") if meta["voz_ia"] else False
    meta["atribucion_en_descripcion"] = meta.pop("atribucion")
    inferidos = [{"voz_realista": "voz_ia_realista", "atribucion": "atribucion_en_descripcion"}.get(k, k)
                 for k in inferidos]
    meta["_inferidos"] = inferidos
    if a.musica:
        meta["musica"] = a.musica
    return validar(Path(a.video), a.plataforma, meta)


if __name__ == "__main__":
    sys.exit(main())
