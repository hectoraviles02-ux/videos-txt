# -*- coding: utf-8 -*-
"""Cadena completa de un reel «foto real» (31-oct-2026):
citas literales → voz Chatterbox (con candado de GPU) → render → medición → publicacion.json → Telegram.

La pieza vive en guiones/reels_revision/AAAA-MM-DD_<slug>/ con:
  guion.json   fuentes, tramos (texto, fuente, cita_fuente), planos y cierre (ver motor.py)
  textos.json  tema, titulo_youtube, descripcion, tags_youtube (los textos de publicación)
Uso: python scripts/foto_real/entregar.py <carpeta_pieza> [--voz] [--muestras] [--sin-enviar]
NO aprueba ni publica: solo manda los botones de Telegram (AGENTS.md R27).
Códigos: 0 entregado · 1 falló una compuerta · 2 GPU ocupada.
"""
import glob
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PIZ = REPO / "scripts" / "pizarra"
PY_VOZ = os.environ.get("CHATTERBOX_PY", r"C:/Users/<usuario>/chatterbox_ab_env/Scripts/python.exe")
RITMO = REPO / "laboratorio" / "estilo_celula_20261026" / "medir_ritmo.py"
sys.path.insert(0, str(REPO))


def texto_fuente(d):
    return " ".join(" ".join(s["texto"] for s in json.load(open(p, encoding="utf-8"))["segmentos"])
                    for p in glob.glob(d + "/*.json"))


def _compacto(s):
    """Sin espacios ni caracteres de reemplazo: los PDF parten palabras y meten saltos de línea."""
    return "".join(c for c in s if not c.isspace() and c != "\ufffd")


def verificar_citas(g):
    T = {k: _compacto(texto_fuente(v)) for k, v in g["fuentes"].items()}
    faltan = []
    for t in g["tramos"]:
        fs = [f for f in t["fuente"].split("+") if f]
        for f in fs:
            if f not in T:
                faltan.append((t["id"], "fuente desconocida " + f))
        for c in t["cita_fuente"]:
            if fs and not any(_compacto(c) in T.get(f, "") for f in fs):
                faltan.append((t["id"], c[:80]))
        if fs and not t["cita_fuente"]:
            faltan.append((t["id"], "tramo con fuente pero sin cita"))
    return faltan


def voz(carpeta: Path):
    from telegram_bot import candado
    with candado.tomar("foto_real_" + carpeta.name) as ok:
        if not ok:
            print("GPU ocupada:", candado.quien())
            return 2
        subprocess.run([PY_VOZ, str(PIZ / "voz.py"), str(carpeta)], check=True)
        # alinear.py a veces truena AL CERRAR (0xC0000409, limpieza de CUDA) después de escribir tiempos.json:
        # se acepta solo si imprimió su línea final «OK <duración>».
        r = subprocess.run([PY_VOZ, str(PIZ / "alinear.py"), str(carpeta)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode and not any(ln.startswith("OK ") for ln in r.stdout.splitlines()):
            print(r.stdout[-800:], r.stderr[-800:])
            return 1
    spec = importlib.util.spec_from_file_location("mezcla", PIZ / "mezcla.py")
    mz = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mz)
    dur = json.loads((carpeta / "tiempos.json").read_text(encoding="utf-8"))["duracion"]
    mz.mezclar_solo_voz(carpeta / "voz.wav", dur, -14, carpeta / "mezcla.wav")
    return 0


def main():
    carpeta = Path(sys.argv[1]).resolve()
    slug = carpeta.name.split("_", 1)[1]
    g = json.loads((carpeta / "guion.json").read_text(encoding="utf-8"))
    faltan = verificar_citas(g)
    n = sum(len(t["cita_fuente"]) for t in g["tramos"])
    print(f"citas: {n} revisadas, {len(faltan)} faltantes")
    for f in faltan:
        print("  FALTA", f)
    if faltan:
        return 1
    if not (carpeta / "mezcla.wav").is_file() or "--voz" in sys.argv:
        r = voz(carpeta)
        if r:
            return r
    motor = [sys.executable, str(Path(__file__).with_name("motor.py")), str(carpeta)]
    if "--muestras" in sys.argv:
        return subprocess.run(motor + ["--muestras"]).returncode
    r = subprocess.run(motor, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        print(r.stderr[-2000:])
        return 1
    tmp = carpeta / "render_9x16.mp4"
    if RITMO.is_file():
        ritmo = subprocess.run([sys.executable, str(RITMO), str(tmp)], capture_output=True, text=True).stdout
        print("ritmo:", (ritmo.strip().splitlines() or ["?"])[-1])
    lufs = [ln for ln in subprocess.run(["ffmpeg", "-hide_banner", "-i", str(tmp), "-af", "ebur128=framelog=quiet",
                                         "-f", "null", "-"], capture_output=True, text=True).stderr.splitlines()
            if ln.strip().startswith("I:")][-1].strip()
    mb = tmp.stat().st_size / 1e6
    print(f"volumen: {lufs} · peso: {mb:.1f} MB")
    if mb >= 10:
        print("PESA 10 MB O MÁS: no se manda (baja MAXRATE)")
        return 1
    arch = f"{slug}_9x16.mp4"
    os.replace(tmp, carpeta / arch)
    pub = json.loads((carpeta / "textos.json").read_text(encoding="utf-8"))
    pub["origen"] = "motor_foto_real"
    pub["videos"] = [{"formato": f"reel_{c}", "archivo": arch, "canal": c, "etiqueta_ia": True,
                      "clave_legado": f"foto_real:{slug}:{c}", **({"modo": "studio"} if c == "tiktok" else {})}
                     for c in ("facebook", "youtube", "tiktok")]
    (carpeta / "publicacion.json").write_text(json.dumps(pub, ensure_ascii=False, indent=2), encoding="utf-8")
    print("entrega:", carpeta / arch)
    if "--sin-enviar" in sys.argv:
        return 0
    os.chdir(REPO)
    from review_channels.telegram_channel import _load_env
    _load_env()
    from content_factory import db
    from telegram_bot import aprobacion_botones as ab
    chat = os.environ.get("VIDEOS_TXT_TELEGRAM_CHAT_ID", "").strip()
    conn = db.conectar(db.RUTA_PRODUCCION, produccion=True)
    db.crear_esquema(conn)
    en = ab._ahora()
    for pid, v in ab.registrar(conn, carpeta, en=en):
        r = ab.enviar_para_aprobar(conn, pid, destinatario=chat, en=en)
        print("telegram:", pid, v["canal"], "enviado" if r["ok"] else r)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
