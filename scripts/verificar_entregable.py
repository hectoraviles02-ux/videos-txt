# -*- coding: utf-8 -*-
"""
Contrato PREVISUALIZACION vs ENTREGABLE (content-addressed).

- PREVISUALIZACION: cualquier MP4 sin constancia F5 valida para su sha256 exacto.
  (p.ej. el que sale de `npx remotion render` directo, saltando el orquestador).
- ENTREGABLE: MP4 con (1) constancia tecnica F5 APTO para su sha256 EXACTO
  y (2) constancia editorial humana aprobada para ese mismo sha256.

Las constancias son content-addressed: viven en carpetas versionadas y se
localizan por el sha256 del video. Si el video cambia 1 byte, su sha cambia y
deja de tener constancia -> vuelve a ser PREVISUALIZACION. Asi el control no se
puede saltar por accidente (render directo, archivo movido, re-encode).

LIMITE HONESTO: un JSON se puede falsificar a mano. Esto protege contra
accidentes y rutas alternas, NO contra un adversario. No hay firma criptografica.

Uso:
  verificar_entregable.py <video>            -> exige F5 APTO + aprobacion editorial
  verificar_entregable.py <video> --tecnico  -> exige solo F5 APTO
  verificar_entregable.py --certificar <video> --formato horizontal|vertical
                                             -> corre F5 sobre los bytes EXACTOS y emite constancia
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
REG_F5 = BASE / "registro_f5"
REG_EDIT = BASE / "registro_editorial"


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def sha256_text(p):
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:16]
    except Exception:
        return None


def emitir_constancia_f5(video, res, perfil, inputprops=None, traza=None, documental_tsx=None,
                         encargo_sha=None, edl_sha=None):
    """Escribe registro_f5/<sha256>.json SOLO si el veredicto es APTO."""
    if not res.get("apto"):
        return None  # NO APTO no deja constancia: sigue siendo previsualizacion
    REG_F5.mkdir(exist_ok=True)
    sha = sha256_file(video)
    recurso_shas = []
    if traza and Path(traza).is_file():
        try:
            t = json.loads(Path(traza).read_text(encoding="utf-8"))
            recurso_shas = sorted({p.get("recurso_hash", p.get("hash_archivo"))
                                   for p in (t if isinstance(t, list) else t.get("planos", []))
                                   if isinstance(p, dict) and p.get("recurso_hash", p.get("hash_archivo"))})
        except Exception:
            pass
    rec = {
        "sha256": sha,
        "video": Path(video).name,
        "perfil": perfil,
        "veredicto": "APTO",
        "puntaje_gate": res.get("puntaje_gate"),
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "inputprops_sha16": sha256_text(inputprops) if inputprops else None,
        "documental_tsx_sha16": sha256_text(documental_tsx) if documental_tsx else None,
        "encargo_sha256": encargo_sha,  # vinculo al contrato; None = certificacion sin encargo
        "edl_sha256": edl_sha,
        "recurso_shas": recurso_shas,
        "nota": "Constancia TECNICA (F5). No implica aprobacion editorial.",
    }
    out = REG_F5 / f"{sha}.json"
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def verificar(video, requerir_editorial=True):
    """Devuelve (estado, detalle). estado in ENTREGABLE/APTO_TECNICO/PREVISUALIZACION/RECHAZADO."""
    video = Path(video)
    if not video.is_file():
        return "RECHAZADO", {"motivo": "el archivo no existe"}
    sha = sha256_file(video)
    f5 = REG_F5 / f"{sha}.json"
    if not f5.is_file():
        return "PREVISUALIZACION", {"sha256": sha, "motivo": "sin constancia F5 para este sha (posible render directo)"}
    rec = json.loads(f5.read_text(encoding="utf-8"))
    if rec.get("veredicto") != "APTO":
        return "RECHAZADO", {"sha256": sha, "motivo": f"constancia F5 con veredicto {rec.get('veredicto')}"}
    # Un documental DEBE tener la constancia vinculada a un encargo (cierra el bypass de
    # --certificar / render directo: una constancia sin encargo NO distingue de una previsualizacion).
    if rec.get("perfil") == "documental" and not rec.get("encargo_sha256"):
        return "PREVISUALIZACION", {"sha256": sha, "motivo": "constancia tecnica SIN vinculo de encargo "
                                    "(render directo o certificacion sin contrato): no es certificable"}
    edit = REG_EDIT / f"{sha}.json"
    edit_ok = edit.is_file() and json.loads(edit.read_text(encoding="utf-8")).get("aprobado") is True
    if not requerir_editorial:
        return "APTO_TECNICO", {"sha256": sha, "f5": rec.get("puntaje_gate"), "editorial": edit_ok}
    if not edit_ok:
        return "PREVISUALIZACION", {"sha256": sha, "motivo": "F5 APTO pero SIN aprobacion editorial humana"}
    return "ENTREGABLE", {"sha256": sha, "f5": rec.get("puntaje_gate")}


def _certificar(video, formato):
    sys.path.insert(0, str(BASE / "scripts"))
    import f5_control_calidad as F5  # noqa
    PROY = BASE / "motor_documental_test"
    ip = PROY / f"_pipeline_inputprops_{formato}.json"
    tz = PROY / f"_pipeline_traza_{formato}.json"
    res = F5.controlar(str(video), vertical=(formato == "vertical"),
                       inputprops=str(ip) if ip.is_file() else None,
                       traza=str(tz) if tz.is_file() else None)
    print(F5.informe(res))
    out = emitir_constancia_f5(video, res, perfil="documental",
                               inputprops=str(ip) if ip.is_file() else None,
                               traza=str(tz) if tz.is_file() else None,
                               documental_tsx=str(PROY / "src" / "Documental.tsx"))
    if out:
        print(f"Constancia F5 emitida: {out}")
        return 0
    print("NO APTO: no se emite constancia (sigue siendo PREVISUALIZACION).")
    return 4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--tecnico", action="store_true", help="exige solo F5 (no editorial)")
    ap.add_argument("--certificar", action="store_true", help="corre F5 y emite constancia")
    ap.add_argument("--formato", choices=["horizontal", "vertical"], default="horizontal")
    a = ap.parse_args()
    if a.certificar:
        return _certificar(a.video, a.formato)
    estado, det = verificar(a.video, requerir_editorial=not a.tecnico)
    print(f"{estado}: {Path(a.video).name}  {json.dumps(det, ensure_ascii=False)}")
    return 0 if estado in ("ENTREGABLE", "APTO_TECNICO") else 5


if __name__ == "__main__":
    raise SystemExit(main())
