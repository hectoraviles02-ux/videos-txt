# -*- coding: utf-8 -*-
"""
Paso 0 — Verificacion de EQUIVALENCIA del golden V2 contra el MP4 aprobado.

NO declara equivalencia por hashes ni por estructura JSON (regla del usuario):
compara el video renderizado desde el EDL golden contra muestra_periodistica_A_v2.mp4
en duracion, resolucion, AUDIO AUTENTICO del fragmento presidencial, y composicion/
movimiento (SSIM en la region superior, excluyendo el lower-third de subtitulos/creditos).

Incluye CONTROL NEGATIVO del umbral SSIM: comparar fotogramas de timecodes distintos
debe puntuar MENOS que los del mismo timecode; si no, el umbral no discrimina.

Uso: python scripts/verificar_golden_v2.py <golden.mp4> [ref.mp4]
"""
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
REF_DEFAULT = BASE / "motor_documental_test" / "out" / "muestra_periodistica_A_v2.mp4"
FRAG_SRC = BASE / "motor_documental_test" / "public" / "periodistica" / "fragmento_720p.mp4"
# Ventana del audio del fragmento, alineada a la colocacion de CADA render:
#   golden: audio_original en frame 402 = 13.40s ; V2: Clip Sequence from=401 = 13.367s
GAUD_INI, GAUD_FIN = 13.40, 21.00
V2AUD_INI, V2AUD_FIN = 13.367, 20.967

# El fragmento presidencial en V2 va de 401 a 635 frames @30 = 13.37s a 21.17s
FRAG_INI, FRAG_FIN = 13.37, 21.17
KEYFRAMES = [1.0, 17.0, 24.0]  # paneo / fragmento / documento
SSIM_MIN = 0.55
AUDIO_MIN_DB = -45.0
FPS = 30

# Los 3 subtitulos literales de V2 sobre el fragmento presidencial (texto exacto + frame absoluto)
SUBS_ESPERADOS = [
    ("Es algo grave, evidentemente,", 401),
    ("se está apoyando a las familias", 485),
    ("y tiene que llegar la investigación a las últimas consecuencias.", 557),
]


def verificar_subtitulos(inputprops):
    """req 8: texto EXACTO y tiempos de los 3 subtitulos literales. Devuelve (ok, detalle)."""
    import json as _j
    subs = _j.loads(Path(inputprops).read_text(encoding="utf-8")).get("subtitles", [])
    faltan = []
    for texto, fabs in SUBS_ESPERADOS:
        match = [s for s in subs if s.get("text", "").strip() == texto and abs(s.get("fromF", -999) - fabs) <= 6]
        if not match:
            faltan.append(f"'{texto[:30]}...'@~{fabs}f")
    return (not faltan), (f"{len(SUBS_ESPERADOS)-len(faltan)}/{len(SUBS_ESPERADOS)} subtitulos literales OK"
                          if not faltan else f"FALTAN/desalineados: {faltan}")


def _probe(v, stream, entries):
    o = subprocess.run(["ffprobe", "-v", "error", "-select_streams", stream,
                        "-show_entries", entries, "-of", "default=noprint_wrappers=1:nokey=1", str(v)],
                       capture_output=True, text=True)
    return o.stdout.strip().splitlines()


def dur(v):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "default=nk=1:nw=1", str(v)], capture_output=True, text=True).stdout)


def mean_volume_db(v, ini, fin):
    o = subprocess.run(["ffmpeg", "-v", "info", "-ss", str(ini), "-to", str(fin), "-i", str(v),
                        "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
    for ln in (o.stderr or "").splitlines():
        if "mean_volume:" in ln:
            try:
                return float(ln.split("mean_volume:")[1].split("dB")[0].strip())
            except Exception:
                return None
    return None


def _frame_top(v, tc):
    import cv2
    import tempfile
    f = Path(tempfile.gettempdir()) / f"_gf_{abs(hash((str(v), tc)))}.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(tc), "-i", str(v),
                    "-frames:v", "1", str(f)], check=True)
    img = cv2.imread(str(f), cv2.IMREAD_GRAYSCALE)
    f.unlink(missing_ok=True)
    h = img.shape[0]
    return cv2.resize(img[: int(h * 0.65), :], (640, 234))  # region superior normalizada


def _band(v, tc):
    """Banda inferior (lower-third, donde van los subtitulos) normalizada."""
    import cv2
    import tempfile
    f = Path(tempfile.gettempdir()) / f"_gb_{abs(hash((str(v), tc)))}.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(tc), "-i", str(v),
                    "-frames:v", "1", str(f)], check=True)
    img = cv2.imread(str(f), cv2.IMREAD_GRAYSCALE)
    f.unlink(missing_ok=True)
    h = img.shape[0]
    return cv2.resize(img[int(h * 0.72):, :], (640, 120))


def _audio_samples(v, ini, fin):
    """PCM mono 16k del intervalo, como numpy float normalizado (array vacio si no hay audio)."""
    import numpy as np
    o = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(ini), "-to", str(fin), "-i", str(v),
                        "-ac", "1", "-ar", "16000", "-f", "s16le", "-"], capture_output=True)
    a = np.frombuffer(o.stdout, dtype="<i2").astype("float64")
    if a.size == 0:
        return a
    return a / (np.abs(a).max() + 1e-9)


def audio_xcorr_peak(g, r, gi, gf, ri=None, rf=None, maxlag=16000):
    """Pico de correlacion cruzada normalizada LINEAL (no circular) con busqueda de
    desfase ±1s. Admite ventanas distintas para cada fuente. ~1.0 = misma señal
    (audio autentico); ~0 = señal distinta o silencio."""
    import numpy as np
    a = _audio_samples(g, gi, gf)
    b = _audio_samples(r, ri if ri is not None else gi, rf if rf is not None else gf)
    if len(a) < 8000 or len(b) < 8000:
        return 0.0
    a, b = a - a.mean(), b - b.mean()

    def corr_at(lag):
        if lag >= 0:
            aa, bb = a[lag:], b[:len(a) - lag]
        else:
            aa, bb = a[:len(a) + lag], b[-lag:]
        n = min(len(aa), len(bb))
        if n < 8000:
            return 0.0
        aa, bb = aa[:n], bb[:n]
        den = (np.linalg.norm(aa) * np.linalg.norm(bb)) + 1e-9
        return float(np.dot(aa, bb) / den)

    coarse = max(range(-maxlag, maxlag + 1, 80), key=corr_at)
    return max(corr_at(coarse + d) for d in range(-80, 81, 4))


def _frame_full(v, tc):
    """Fotograma COMPLETO (incluye lower-third de subtitulos) normalizado."""
    import cv2
    import tempfile
    f = Path(tempfile.gettempdir()) / f"_gff_{abs(hash((str(v), tc)))}.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(tc), "-i", str(v),
                    "-frames:v", "1", str(f)], check=True)
    img = cv2.imread(str(f), cv2.IMREAD_GRAYSCALE)
    f.unlink(missing_ok=True)
    return cv2.resize(img, (640, 360))


def ssim(a, b):
    try:
        from skimage.metrics import structural_similarity as sk
        return float(sk(a, b))
    except Exception:
        a = a.astype("float64"); b = b.astype("float64")
        mu_a, mu_b = a.mean(), b.mean()
        va, vb = a.var(), b.var()
        cov = ((a - mu_a) * (b - mu_b)).mean()
        c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
        return float(((2 * mu_a * mu_b + c1) * (2 * cov + c2)) /
                     ((mu_a ** 2 + mu_b ** 2 + c1) * (va + vb + c2)))


def verificar(golden, ref=REF_DEFAULT, inputprops=None):
    golden, ref = Path(golden), Path(ref)
    checks = []  # (nombre, ok, detalle)

    dg, dr = dur(golden), dur(ref)
    checks.append(("duracion", abs(dg - dr) <= 2 / 30 + 0.05, f"golden={dg:.2f}s ref={dr:.2f}s (dif {abs(dg-dr)*1000:.0f}ms)"))

    rg = tuple(_probe(golden, "v:0", "stream=width,height"))
    rr = tuple(_probe(ref, "v:0", "stream=width,height"))
    checks.append(("resolucion", rg == rr, f"golden={rg} ref={rr}"))

    # AUDIO autentico del fragmento presidencial (condicion explicita del usuario)
    vg = mean_volume_db(golden, FRAG_INI, FRAG_FIN)
    vr = mean_volume_db(ref, FRAG_INI, FRAG_FIN)
    checks.append(("audio_fragmento_presidencial",
                   vg is not None and vg > AUDIO_MIN_DB,
                   f"mean_volume golden={vg}dB (ref={vr}dB) en [{FRAG_INI},{FRAG_FIN}]s — audio real presente"))

    # Subtitulos literales (req 8): texto exacto + tiempos (contra el EDL)
    if inputprops and Path(inputprops).is_file():
        ok_s, det_s = verificar_subtitulos(inputprops)
        checks.append(("subtitulos_literales_edl", ok_s, det_s))

    # req 9: el audio del golden debe ser IDENTICO al del MP4 aprobado (V2), que ya establecio
    # la autenticidad del fragmento. Correlacion LINEAL con ventanas alineadas a cada render.
    # (Comparar contra la fuente cruda da ~0.36 por la transformacion del render, IGUAL en V2 y golden;
    #  lo que importa es que golden == V2, no golden == raw.)
    xpk = audio_xcorr_peak(golden, ref, GAUD_INI, GAUD_FIN, V2AUD_INI, V2AUD_FIN)
    checks.append(("audio_identico_a_v2", xpk >= 0.9,
                   f"xcorr golden vs V2 aprobado={xpk:.3f} (>=0.9 = audio identico al autentico de V2)"))

    # SSIM composicion/movimiento (region superior)
    try:
        import cv2  # noqa
        fs_g = {tc: _frame_top(golden, tc) for tc in KEYFRAMES}
        fs_r = {tc: _frame_top(ref, tc) for tc in KEYFRAMES}
        for tc in KEYFRAMES:
            s = ssim(fs_g[tc], fs_r[tc])
            checks.append((f"ssim@{tc}s", s >= SSIM_MIN, f"SSIM={s:.3f} (min {SSIM_MIN})"))
        # req 10: SSIM de cuadro COMPLETO en el fragmento (15s, con subtitulo visible), sin excluir el lower-third
        s_full = ssim(_frame_full(golden, 15.0), _frame_full(ref, 15.0))
        checks.append(("ssim_full_fragmento@15s", s_full >= SSIM_MIN,
                       f"SSIM cuadro completo (incl. subtitulos)={s_full:.3f}"))
        # req 10 (nivel render): banda de subtitulos por linea (una dentro de cada subtitulo)
        BAND_MIN = 0.40
        for tc in (14.5, 17.2, 20.0):
            sb = ssim(_band(golden, tc), _band(ref, tc))
            checks.append((f"banda_subtitulo@{tc}s", sb >= BAND_MIN, f"SSIM banda inferior={sb:.3f} (min {BAND_MIN})"))
        # req 2: el paneo SE MUEVE (intra-golden: inicio y fin del plano 1 difieren)
        mov = 1 - ssim(_frame_top(golden, 1.0), _frame_top(golden, 12.0))
        checks.append(("paneo_tiene_movimiento", mov >= 0.05, f"diferencia intra-golden 1s<->12s={mov:.3f} (>0 = se mueve)"))
        # CONTROL NEGATIVO: mismo-tc debe superar a distinto-tc
        s_match = ssim(fs_g[17.0], fs_r[17.0])
        s_mismatch = ssim(fs_g[17.0], fs_r[24.0])
        checks.append(("control_negativo_umbral", s_match > s_mismatch,
                       f"match={s_match:.3f} > mismatch={s_mismatch:.3f} (el umbral discrimina)"))
    except ImportError:
        checks.append(("ssim", False, "cv2 no disponible — no se pudo medir composicion"))

    apto = all(ok for _, ok, _ in checks)
    return apto, checks


if __name__ == "__main__":
    g = sys.argv[1]
    r = sys.argv[2] if len(sys.argv) > 2 else REF_DEFAULT
    ip = sys.argv[3] if len(sys.argv) > 3 else (BASE / "motor_documental_test" / "_golden_inputprops.json")
    apto, checks = verificar(g, r, inputprops=ip if Path(ip).is_file() else None)
    print("=== Equivalencia golden V2 vs MP4 aprobado ===")
    for n, ok, det in checks:
        print(f"  [{'OK ' if ok else 'XX '}] {n}: {det}")
    print(f"VEREDICTO: {'EQUIVALENTE' if apto else 'NO EQUIVALENTE'}")
    raise SystemExit(0 if apto else 1)
