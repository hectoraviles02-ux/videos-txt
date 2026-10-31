# -*- coding: utf-8 -*-
"""
Pipeline documental CONECTADO: EDL -> inputProps -> render Remotion -> F5 gate.

Cierra el hallazgo de P3: el control de calidad (F5) deja de ser un comando
manual suelto y pasa a ser un PASO del pipeline. Un video solo se considera
'ENTREGABLE' si paso el gate documental. NO publica, NO sobrescribe sin querer
(usa el --out que se le indique), NO toca los reels.

Uso:
  python scripts/producir_documental_edl.py <edl_dir> --formato horizontal|vertical --out <mp4>
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))
import edl_a_inputprops as A  # noqa: E402
import f5_control_calidad as F5  # noqa: E402

PROY = BASE / "motor_documental_test"


def producir(edl_dir, formato, out_mp4, override_periodistico=None, force=False):
    out_mp4 = Path(out_mp4).resolve()  # absoluta: el render corre con cwd=PROY
    # D12: proteger originales y no sobrescribir en silencio.
    partes = {p.lower() for p in out_mp4.parts}
    if "raw" in partes or "procesado" in partes:
        print(f"[0] RECHAZADO: destino bajo raw/ o procesado/ (read-only): {out_mp4}")
        return 7
    if out_mp4.exists() and not force:
        print(f"[0] RECHAZADO: el destino ya existe (usa --force para sobrescribir a proposito): {out_mp4}")
        return 7
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    props_path = PROY / f"_pipeline_inputprops_{formato}.json"
    traza_path = PROY / f"_pipeline_traza_{formato}.json"

    # ── Este orquestador es el camino DOCUMENTAL (los reels usan content_factory,
    #    medido: no tocan plan_montaje). Por eso es FAIL-CLOSED respecto al encargo. ──
    import expediente as EXPMOD
    import gate_periodistico as GP
    import gate_visual as GV
    import encargo_validar as ENC
    import edl_validar as EV
    guion = json.loads((Path(edl_dir) / "guion.json").read_text(encoding="utf-8"))
    plan = json.loads((Path(edl_dir) / "plan_montaje.json").read_text(encoding="utf-8"))
    caso = guion.get("caso_id") or plan.get("caso_id")
    manifiesto = EV.cargar_manifiesto()

    # [0.1] FAIL-CLOSED: documental sin encargo valido -> NO se produce.
    if not caso or not ENC.ruta_encargo(caso).is_file():
        print(f"[0] FAIL-CLOSED: documental sin encargo ('{caso}'): no se produce. "
              f"(esperado: guiones/{caso}/encargo.json)")
        return 6
    encargo = ENC.cargar(caso)
    se = ENC.validar_schema(encargo)
    if se:
        print("[0] FAIL-CLOSED: encargo invalido (esquema):")
        for x in se:
            print("     -", x)
        return 6
    entregable_id = plan.get("entregable_id")
    print(f"[0] Encargo '{encargo['encargo_id']}' v{encargo['version']} cargado | entregable={entregable_id}")

    # [0.3] GATE ENCARGO (conformidad del EDL con el encargo) — antes de gastar render.
    exp = EXPMOD.cargar(caso) if EXPMOD.ruta(caso).is_file() else None
    ev = ENC.evaluar(encargo, plan, guion, exp, manifiesto)
    print("[0.3] " + ENC.informe(ev).replace("\n", "\n      "))
    if not ev["apto"]:
        print(f"\n==> {out_mp4.name}: RETENIDO (gate encargo NO APTO) — no se renderiza")
        return 6

    # [0.5] GATE VISUAL (estructura + tratamientos obligatorios del encargo).
    gv = GV.evaluar(plan, guion, manifiesto, encargo=encargo)
    print("[0.5] " + GV.informe(gv).replace("\n", "\n      "))
    if not gv["apto"]:
        print(f"\n==> {out_mp4.name}: RETENIDO (gate visual NO APTO) — no se renderiza")
        return 6

    # [0.7] GATE PERIODISTICO (E7) — obligatoriedad y cobertura POR ENTREGABLE.
    gp_obligatorio, gp_cobertura = ENC.gate_periodistico_efectivo(encargo, entregable_id)
    if gp_obligatorio:
        if not (exp and EXPMOD.ruta(caso).is_file()):
            print(f"[0.7] FAIL-CLOSED: entregable '{entregable_id}' exige gate periodistico pero no hay expediente para '{caso}'")
            return 6
        kw = {"override": override_periodistico}
        if gp_cobertura is not None:
            kw["umbral"] = gp_cobertura
        gp = GP.evaluar(exp, guion, **kw)
        print("[0.7] " + GP.informe(gp).replace("\n", "\n      "))
        if not gp["apto"]:
            print(f"\n==> {out_mp4.name}: RETENIDO (gate periodistico NO APTO) — no se renderiza")
            return 5
    else:
        print(f"[0.7] Gate periodistico: no obligatorio para el entregable '{entregable_id}' (omitido)")

    # 1) EDL -> inputProps (valida/aborta/resuelve/subtitulos)
    print("[1/3] Adaptando EDL -> inputProps...")
    try:
        props, traza = A.construir(edl_dir, formato)
    except A.AbortF3 as e:
        print("ABORTADO en adaptacion:", e)
        return 2
    props_path.write_text(json.dumps(props, ensure_ascii=False, indent=2), encoding="utf-8")
    traza_path.write_text(json.dumps(traza, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2) Render Remotion (componente generico Documental)
    print(f"[2/3] Renderizando {out_mp4.name} ({props['width']}x{props['height']}, {props['durationInFrames']}f)...")
    rel_props = props_path.name
    # En Windows npx es npx.cmd: shell=True con STRING (no lista) lo resuelve via cmd.
    cmd = f'npx remotion render Documental "{out_mp4}" "--props=./{rel_props}" --codec=h264'
    r = subprocess.run(cmd, cwd=str(PROY), capture_output=True, text=True, shell=True)
    # El exito se juzga por el ARCHIVO (npx vvia shell puede devolver !=0 espurio).
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=noprint_wrappers=1:nokey=1", str(out_mp4)],
                         capture_output=True, text=True).stdout.strip() if out_mp4.is_file() else ""
    if not out_mp4.is_file() or not dur or float(dur) <= 0:
        print("FALLO el render:", (r.stderr or r.stdout or "(sin salida)")[-500:])
        return 3

    # 3) F5 gate documental (CONECTADO) + constancia content-addressed
    print("[3/3] Control de calidad F5...")
    res = F5.controlar(str(out_mp4), vertical=(formato == "vertical"),
                       inputprops=str(props_path), traza=str(traza_path),
                       encargo=encargo, entregable_id=entregable_id)
    print(F5.informe(res))
    if not res["apto"]:
        print(f"\n==> {out_mp4.name}: RETENIDO (no paso el gate) — sin constancia F5")
        return 4
    import hashlib
    import verificar_entregable as VE  # noqa
    edl_sha = hashlib.sha256((Path(edl_dir) / "plan_montaje.json").read_bytes()).hexdigest()
    cons = VE.emitir_constancia_f5(str(out_mp4), res, perfil="documental",
                                   inputprops=str(props_path), traza=str(traza_path),
                                   documental_tsx=str(PROY / "src" / "Documental.tsx"),
                                   encargo_sha=ENC.sha256_encargo(encargo), edl_sha=edl_sha)
    # APTO TECNICO != ENTREGABLE: la aprobacion editorial humana es un paso aparte.
    print(f"\n==> {out_mp4.name}: APTO TECNICO (F5) — PREVISUALIZACION hasta aprobacion editorial")
    print(f"    Constancia F5: {cons}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("edl_dir")
    ap.add_argument("--formato", choices=["horizontal", "vertical"], default="horizontal")
    ap.add_argument("--out", required=True)
    ap.add_argument("--force", action="store_true", help="sobrescribir el destino a proposito (D12)")
    a = ap.parse_args()
    # Candado de la fábrica (19-oct-2026): el render de Remotion consume mucha CPU/RAM. Se toma aquí, en la entrada de línea de
    # comandos (único responsable); quien importe producir() como biblioteca no lo toma. Código 8 = fábrica ocupada.
    sys.path.insert(0, str(BASE))
    from telegram_bot import candado
    with candado.tomar("documental-edl") as libre:
        if not libre:
            print("OCUPADO: %s; no se renderiza" % candado.quien())
            return 8
        return producir(a.edl_dir, a.formato, a.out, force=a.force)


if __name__ == "__main__":
    raise SystemExit(main())
