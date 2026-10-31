# -*- coding: utf-8 -*-
"""Motor pizarra en producción: una pieza = un comando.
    python scripts\\pizarra\\producir_pizarra.py guiones\\pizarra\\AAAA-MM-DD_<slug>
Contrato: ver CONTRATO.md. Origen: laboratorio/motor_eventos_20261018/estilo_mate/motor (3 pilotos aprobados 19-oct-2026).
Etapas: compuertas del guion → (candado de la fábrica para TODA la corrida) → voz Chatterbox por tramos + alineación con
cobertura → mezcla (sin música por defecto) → dos renders idénticos → revisiones → entrega inmutable con manifiesto.
Salida con código 2 si la fábrica está ocupada o la GPU no tiene memoria libre; 1 si una compuerta o revisión falla.
Nada se publica."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

PIZ = Path(__file__).resolve().parent
RAIZ = PIZ.parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(PIZ))
from telegram_bot import candado  # noqa: E402

PY_CB = r"C:/Users/<usuario>/chatterbox_ab_env/Scripts/python.exe"
CONFIG = RAIZ / "config" / "motores_video.json"
VRAM_MIN_MB = 6000          # medido 19-oct: voz 5.7 GB + margen
_log = None


def log(msg):
    print(msg, flush=True)
    if _log:
        with open(_log, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def cargar(nombre, ruta):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def activo() -> bool:
    try:
        return bool(json.loads(CONFIG.read_text(encoding="utf-8"))["motores"]["pizarra"]["activo"])
    except Exception:
        return False


def compuertas(g):
    errores = []
    if g.get("motor") != "pizarra":
        errores.append(f"el guion pide el motor «{g.get('motor')}», no «pizarra»")
    for k in ("id", "titulo", "leyenda", "escena", "centro", "tramos", "verificaciones", "voz"):
        if k not in g:
            errores.append(f"falta «{k}» en el guion")
    for tr in g.get("tramos", []):
        if re.search(r"\d", tr["texto"]):
            errores.append(f"{tr['id']}: el texto de voz contiene cifras (Chatterbox debe recibir palabras): «{tr['texto']}»")
    for vf in g.get("verificaciones", []):
        r = eval(vf["expr"], {"__builtins__": {}}, {"round": round, "abs": abs, "min": min, "max": max})
        if abs(r - vf["esperado"]) > vf.get("tol", 1e-9):
            errores.append(f"verificación falló: {vf['desc']}: {vf['expr']} = {r}, esperado {vf['esperado']}")
    mus = g.get("musica")
    if mus is not None and not Path(mus.get("archivo", "")).is_file():
        errores.append(f"música declarada pero inexistente: {mus}")
    if errores:
        raise SystemExit("COMPUERTA DEL GUION:\n  " + "\n  ".join(errores))
    return [f"{vf['desc']}: {vf['expr']} = {vf['esperado']}" for vf in g["verificaciones"]]


def revisar_cobertura(carpeta: Path, g) -> tuple[bool, str]:
    """Se recalcula en CADA corrida, también si la voz se reutiliza."""
    import soundfile as sf
    tp = carpeta / "tiempos.json"
    if not tp.is_file() or not (carpeta / "voz.wav").is_file():
        return False, "falta tiempos.json o voz.wav"
    ti = json.loads(tp.read_text(encoding="utf-8"))
    por_id = {x["id"]: x for x in ti.get("tramos", [])}
    cob = {c.split(":")[0]: c for c in ti.get("cobertura", [])}
    malos, partes = [], []
    for tr in g["tramos"]:
        a = por_id.get(tr["id"])
        if a is None or a["texto"] != tr["texto"]:
            malos.append(f"{tr['id']}: alineación ausente o de otro texto")
            continue
        m = re.search(r"(\d+)/(\d+) palabras medidas", cob.get(tr["id"], ""))
        if not m:
            malos.append(f"{tr['id']}: sin registro de cobertura")
            continue
        med, tot = int(m.group(1)), int(m.group(2))
        partes.append(f"{tr['id']} {med}/{tot}")
        if tot == 0 or med / tot < 0.8:
            malos.append(f"{tr['id']}: solo {med}/{tot} palabras medidas")
    dur_wav = sf.info(str(carpeta / "voz.wav")).duration
    if abs(dur_wav - ti.get("duracion", -1)) > 0.02:
        malos.append(f"duración de tiempos.json {ti.get('duracion')} ≠ voz.wav {dur_wav:.3f}")
    return (not malos, "; ".join(malos) if malos else "; ".join(partes))


def vram_libre_mb():
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
        return int(r.stdout.strip().splitlines()[0])
    except Exception:
        return None


def producir(carpeta: Path) -> int:
    global _log
    _log = carpeta / "produccion.log"
    g = json.loads((carpeta / "guion.json").read_text(encoding="utf-8"))
    if not activo():
        log(f"motor pizarra DESACTIVADO en {CONFIG}: no se produce"); return 1
    verif = compuertas(g)
    log(f"compuertas del guion: OK ({len(verif)} verificaciones)")
    with candado.tomar("pizarra") as libre:
        if not libre:
            log("OCUPADO: " + candado.quien()); return 2
        return _producir_con_candado(carpeta, g, verif)


def _producir_con_candado(carpeta, g, verif) -> int:
    huella_voz = hashlib.sha256(json.dumps([g["voz"], g["tramos"]], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    marca = carpeta / ".huella_voz"
    if not (carpeta / "voz.wav").is_file() or not marca.is_file() or marca.read_text() != huella_voz:
        libre = vram_libre_mb()
        if libre is None or libre < VRAM_MIN_MB:
            log(f"GPU sin memoria suficiente para la voz ({libre} MB libres < {VRAM_MIN_MB}): no se hace nada"); return 2
        subprocess.run([PY_CB, str(PIZ / "voz.py"), str(carpeta)], check=True)
        subprocess.run([PY_CB, str(PIZ / "alinear.py"), str(carpeta)], check=True)
        marca.write_text(huella_voz)
    else:
        log("voz: el texto no cambió, se reutiliza")
    tiempos = json.loads((carpeta / "tiempos.json").read_text(encoding="utf-8"))
    mz = cargar("mezcla", PIZ / "mezcla.py")
    mus = g.get("musica")
    if mus:
        mz.mezclar(carpeta / "voz.wav", Path(mus["archivo"]), mus.get("nivel_db", -22), tiempos["duracion"], -14, carpeta / "mezcla.wav")
    else:
        mz.mezclar_solo_voz(carpeta / "voz.wav", tiempos["duracion"], -14, carpeta / "mezcla.wav")
    motor = cargar("motor", PIZ / "motor.py")
    a, b = carpeta / "render_a.mp4", carpeta / "render_b.mp4"
    m = motor.render(carpeta, a)
    motor.render(carpeta, b)
    md5 = [subprocess.run(["ffmpeg", "-v", "error", "-i", str(x), "-map", "0:v", "-f", "md5", "-"], capture_output=True, text=True).stdout.strip() for x in (a, b)]
    v = cargar("revisiones", PIZ / "revisiones.py")
    rev = {"render_repetido": (md5[0] == md5[1] and md5[0] != "", f"md5 decodificado {md5[0]} / {md5[1]}")}
    for nombre, fn in (("parpadeo", v.c_parpadeo), ("movimiento_pizarra", v.c_movimiento_pizarra), ("audio", v.c_audio)):
        ok, det, _ = fn(a)
        rev[nombre] = (ok, det)
    dur = tiempos["duracion"]
    rev["duracion_61_90"] = (61 <= dur <= 90, f"{dur:.2f} s")
    rev["cobertura_voz"] = revisar_cobertura(carpeta, g)
    for k, (ok, det) in rev.items():
        log(("PASA  " if ok else "FALLA ") + f"{k} {det}")
    b.unlink()
    todo = all(ok for ok, _ in rev.values())
    man = {"tema": g["id"], "motor": "pizarra", "fecha": str(date.today()), "duracion_s": dur, "musica": mus,
           "revisiones": {k: {"pasa": ok, "detalle": d} for k, (ok, d) in rev.items()},
           "verificaciones_aritmeticas": verif, "anclas_con_tiempo_medido": m.tema.anclas_usadas,
           "sha256_guion": sha(carpeta / "guion.json"), "sha256_tiempos": sha(carpeta / "tiempos.json"), "sha256_mezcla": sha(carpeta / "mezcla.wav"),
           "sha256_motor": {p.name: sha(p) for p in sorted(PIZ.glob("*.py"))},
           "sha256_candado": sha(RAIZ / "telegram_bot" / "candado.py")}
    if not todo:
        man["estado"] = "NO CONGELADO: alguna revisión falló"
        (carpeta / f"MANIFIESTO_NO_APROBADO_{datetime.now():%Y%m%d_%H%M%S}.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
        log("no se congela: hay revisiones que fallan"); return 1
    huella = sha(a)[:8]
    ent = carpeta / "entrega" / f"{datetime.now():%Y%m%d_%H%M%S}_{huella}"
    if ent.exists():
        log(f"la entrega {ent} ya existe: no se sobrescribe"); return 1
    ent.mkdir(parents=True)
    final = ent / f"{g['id']}_9x16.mp4"
    shutil.copy(a, final)
    os.chmod(final, stat.S_IREAD)
    man.update(archivo=str(final), sha256_video=sha(final), bytes=final.stat().st_size, estado="CANDIDATA INMUTABLE (solo lectura)")
    (ent / f"MANIFIESTO_{final.stem}.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"CONGELADO {final} {man['sha256_video']}")
    return 0


if __name__ == "__main__":
    sys.exit(producir(Path(sys.argv[1]).resolve()))
