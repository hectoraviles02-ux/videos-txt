# -*- coding: utf-8 -*-
"""
Contrato editorial ejecutable — carga y validacion del `encargo.json`.

Gobierna el pipeline DOCUMENTAL (pasos 0-4 del diseno consolidado
DISENO_CONTRATO_ENCARGO_20261010.md). NO implementa el paso 5 (aprobacion /
distribucion autenticada): eso queda bloqueado por decision del usuario.

Expone:
  cargar(caso)                  -> dict encargo (desde guiones/<caso>/encargo.json)
  sha256_encargo(encargo)       -> sha256 del encargo canonico (para la terna de hash)
  validar_schema(encargo)       -> [errores de esquema]
  cobertura_autentica(...)      -> por claim central: ¿hay recurso autentico disponible?
  evaluar(encargo, plan, guion, exp, manifiesto) -> {apto, bloqueos, ...}

Reglas duras (no se relajan para pasar pruebas):
  - declaracion obligatoria 'pendiente'/'ausencia_temporal' -> BLOQUEA.
  - declaracion 'disponible' -> su recurso DEBE aparecer en el EDL (si no, R3).
  - exclusion 'imposibilidad_tecnica' -> se REVERIFICA la URL; si responde 200, BLOQUEA.
  - 'exclusion_editorial_autorizada' -> exige autorizacion autenticada del usuario
    (mecanismo del paso 5, aun no disponible) -> por ahora BLOQUEA.
  - recreacion: permitida? no como gancho? no si existe autentico? con justificacion?
  - tratamiento prohibido presente -> BLOQUEA.
  - entregable_id del EDL debe existir en el encargo; caso_id debe coincidir.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCHEMAS = BASE / "schemas"
sys.path.insert(0, str(BASE / "scripts"))

CLASES_AUTENTICAS = {"EVIDENCIA-REAL-DEL-CASO", "CONTEXTO"}
ESTADOS_BLOQUEANTES = {"pendiente", "ausencia_temporal"}
# exclusiones que requieren autorizacion autenticada del usuario (paso 5, bloqueado)
ESTADOS_REQUIEREN_AUTH_USUARIO = {"exclusion_editorial_autorizada", "descartada_sensibilidad"}


def _schema():
    return json.loads((SCHEMAS / "encargo.schema.json").read_text(encoding="utf-8"))


def ruta_encargo(caso) -> Path:
    return BASE / "guiones" / caso / "encargo.json"


def cargar(caso) -> dict:
    return json.loads(ruta_encargo(caso).read_text(encoding="utf-8"))


def sha256_encargo(encargo: dict) -> str:
    """sha256 del encargo canonico (sin la parte informativa de aprobacion)."""
    e = dict(encargo)
    e.pop("aprobacion_encargo", None)  # la aprobacion vive fuera (registro_encargo)
    blob = json.dumps(e, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def validar_schema(encargo: dict) -> list[str]:
    import jsonschema
    errs = []
    v = jsonschema.Draft7Validator(_schema())
    for e in sorted(v.iter_errors(encargo), key=lambda x: list(x.path)):
        errs.append(f"[schema] {e.message} (en {'/'.join(str(p) for p in e.absolute_path)})")
    return errs


def _url_estado(url: str, timeout: float = 8.0) -> str:
    """Reverificacion de ausencia. Devuelve 'responde' | 'muerta' | 'indeterminado'.
    NO falla-abierto: 403/405/429/timeout/errores de red son 'indeterminado' (el que
    afirma la imposibilidad debe probarla; la duda NO acredita la exclusion)."""
    import urllib.error
    import urllib.request
    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "Mozilla/5.0 videos-txt-verif"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code = getattr(r, "status", r.getcode())
            return "responde" if 200 <= code < 400 else "muerta"
    except urllib.error.HTTPError as e:
        if e.code in (403, 405, 429, 401):
            return "indeterminado"  # bloqueo anti-bot: no prueba que este muerta
        if 400 <= e.code < 500:
            return "muerta"
        return "indeterminado"
    except Exception:
        return "indeterminado"  # timeout / DNS / red: no se puede afirmar que este muerta


def _recurso_disponible(r: dict) -> bool:
    """Recurso usable como fuente autentica: aprobado + (si testimonio) sensibilidad aprobada."""
    import edl_validar as E
    if not E.recurso_aprobado(r):
        return False
    if r.get("tipo") == "testimonio":
        rs = r.get("revision_sensibilidad") or {}
        if rs.get("estado") != "aprobada":
            return False
    return True


def _claims_centrales(exp: dict) -> list[dict]:
    return [c for c in exp.get("claims", []) if c.get("central")]


# ---- obligaciones POR ENTREGABLE (los globales no se pueden quitar aqui) ----
def _entregable(encargo: dict, entregable_id):
    return next((e for e in encargo.get("entregables", []) if e.get("id") == entregable_id), None)


def declaracion_aplica(d: dict, entregable_id) -> bool:
    """Una declaracion aplica si su alcance es 'global' o incluye este entregable."""
    alc = d.get("alcance")
    return alc == "global" or (isinstance(alc, list) and entregable_id in alc)


def gate_periodistico_efectivo(encargo: dict, entregable_id):
    """(obligatorio, cobertura_minima) para este entregable: override del entregable o global."""
    inv = encargo.get("investigacion", {}) or {}
    ent = _entregable(encargo, entregable_id) or {}
    obligatorio = ent.get("gate_periodistico_obligatorio", inv.get("gate_periodistico_obligatorio", False))
    cobertura = ent.get("cobertura_minima", inv.get("cobertura_minima"))
    return obligatorio, cobertura


def tratamientos_obligatorios_efectivos(encargo: dict, entregable_id) -> set:
    """Globales (no removibles) + los extra del entregable."""
    glob = set((encargo.get("tratamientos") or {}).get("obligatorios") or [])
    ent = _entregable(encargo, entregable_id) or {}
    return glob | set(ent.get("tratamientos_obligatorios") or [])


def _beat_de_plano(plan: dict, p: dict) -> str | None:
    """Beat de un plano visual por MAXIMO solape temporal con la pista de audio
    de narracion (misma logica que el adaptador edl_a_inputprops.enlace_por_tiempo)."""
    pin, pout = float(p.get("in", 0)), float(p.get("out", 0))
    best, bov = None, 0.0
    for a in plan.get("pista_audio", []):
        if a.get("tipo") != "narracion_tts":
            continue
        ov = min(pout, float(a.get("out", 0))) - max(pin, float(a.get("in", 0)))
        if ov > bov:
            bov, best = ov, a.get("beat_id")
    return best


def cobertura_autentica(encargo: dict, exp: dict, manifiesto: dict, plan: dict, guion: dict) -> dict:
    """Por cada claim central: ¿hay recurso autentico DISPONIBLE que lo cubra?
    Y ¿un plano que CITA ese claim (via su beat) se resolvio con recreacion/tarjeta
    pese a existir autentico? (enlace preciso plano->beat->claim, sin falsos positivos)."""
    por_claim = {}  # claim -> [recurso_ids autenticos disponibles]
    for rid, r in manifiesto.items():
        if r.get("clase") in CLASES_AUTENTICAS and _recurso_disponible(r):
            for cid in (r.get("cubre_claims") or []):
                por_claim.setdefault(cid, []).append(rid)

    centrales = {c["id"] for c in _claims_centrales(exp)}
    beats = {b["id"]: b for b in guion.get("beats", [])}
    NO_AUTENTICO = {"recreacion", "rotulo_pleno", "infografia"}
    tarjeta_tipos = {"tarjeta", "infografia"}

    sustituidos = {}  # claim -> plano que lo sustituyo con no-autentico
    for p in plan.get("pista_visual", []):
        trat = p.get("tratamiento") or ("tarjeta" if p.get("tipo") in tarjeta_tipos else None)
        usa_autentico = bool(p.get("recurso_id") and manifiesto.get(p.get("recurso_id"), {}).get("clase") in CLASES_AUTENTICAS)
        es_no_autentico = (trat in NO_AUTENTICO) or (p.get("tipo") in tarjeta_tipos) or (not usa_autentico and not p.get("recurso_id"))
        if usa_autentico or not es_no_autentico:
            continue
        bid = _beat_de_plano(plan, p)
        for cid in beats.get(bid, {}).get("claim_ids", []):
            if cid in centrales and por_claim.get(cid):
                sustituidos.setdefault(cid, p.get("plano"))

    resultado = {}
    for cid in centrales:
        resultado[cid] = {"autentico_disponible": bool(por_claim.get(cid)),
                          "recursos": por_claim.get(cid, []),
                          "sustituido_por_no_autentico": cid in sustituidos,
                          "plano_sustituto": sustituidos.get(cid)}
    return resultado


def evaluar(encargo: dict, plan: dict, guion: dict,
            exp: dict | None = None, manifiesto: dict | None = None) -> dict:
    """Valida que el EDL cumpla el encargo. Devuelve {apto, bloqueos, avisos}."""
    bloqueos, avisos = [], []

    # 0) esquema del encargo
    se = validar_schema(encargo)
    if se:
        return {"apto": False, "bloqueos": se, "avisos": [], "veredicto": "NO APTO ENCARGO (esquema)"}

    # 1) caso + entregable
    if encargo.get("caso_id") != guion.get("caso_id"):
        bloqueos.append(f"caso_id del encargo ({encargo.get('caso_id')}) != guion ({guion.get('caso_id')})")
    ent_ids = {e["id"] for e in encargo.get("entregables", [])}
    ent = plan.get("entregable_id")
    if ent not in ent_ids:
        bloqueos.append(f"EDL sin entregable_id valido: '{ent}' no esta en {sorted(ent_ids)}")
    else:
        # ANTI-DISFRAZ PRE-RENDER: la duracion PLANEADA (max out del EDL) debe caer en el
        # rango del entregable ANTES de renderizar (no esperar a F5 tras gastar el render).
        ent_obj = _entregable(encargo, ent)
        dur_plan = max((float(p.get("out", 0)) for p in plan.get("pista_visual", [])), default=0.0)
        lo, hi = ent_obj["duracion"]["min_seg"], ent_obj["duracion"]["max_seg"]
        if dur_plan > 0 and not (lo <= dur_plan <= hi):
            bloqueos.append(f"duracion PLANEADA {dur_plan:.0f}s fuera del rango del entregable '{ent}' "
                            f"[{lo}-{hi}]s (un completo no puede disfrazarse de apertura)")

    # 2) declaraciones obligatorias
    pv = plan.get("pista_visual", [])
    pa = plan.get("pista_audio", [])
    rec_en_edl = {p.get("recurso_id") for p in pv if p.get("recurso_id")} | \
                 {a.get("recurso_id") for a in pa if a.get("recurso_id")}
    for d in encargo.get("declaraciones_obligatorias", []):
        if not declaracion_aplica(d, ent):
            continue  # no aplica a ESTE entregable (p.ej. robledo solo en 'completo')
        est = d.get("estado")
        did = d.get("id")
        if est in ESTADOS_BLOQUEANTES:
            bloqueos.append(f"declaracion obligatoria '{did}': estado '{est}' sin resolver (no es exclusion valida)")
        elif est == "disponible":
            if d.get("recurso_id") not in rec_en_edl:
                bloqueos.append(f"declaracion obligatoria '{did}' DISPONIBLE pero ausente del EDL (recurso {d.get('recurso_id')})")
        elif est == "imposibilidad_tecnica":
            url = ((d.get("evidencia") or {}).get("url"))
            estado_url = _url_estado(url) if url else "indeterminado"
            if estado_url != "muerta":
                bloqueos.append(f"declaracion '{did}': 'imposibilidad_tecnica' NO acreditada — la URL esta '{estado_url}' "
                                f"(solo 'muerta' acredita la exclusion; 403/timeout no cuentan) ({url})")
        elif est in ESTADOS_REQUIEREN_AUTH_USUARIO:
            ev = d.get("evidencia") or {}
            if not (ev.get("autorizacion_usuario") or {}).get("evidencia_autenticacion"):
                bloqueos.append(f"declaracion '{did}': exclusion '{est}' requiere autorizacion AUTENTICADA del usuario (paso 5, no disponible)")
        # descartada_derechos: aceptada con evidencia (el schema ya exige evidencia)

    # 3) tratamientos prohibidos / recreaciones
    prohibidos = set((encargo.get("tratamientos") or {}).get("prohibidos") or [])
    reglas_rec = encargo.get("recreaciones") or {}
    cob = cobertura_autentica(encargo, exp, manifiesto, plan, guion) if (exp and manifiesto) else {}
    pv_ordenado = sorted(pv, key=lambda p: float(p.get("in", 0)))
    for idx, p in enumerate(pv_ordenado):
        trat = p.get("tratamiento") or ("tarjeta" if p.get("tipo") in ("tarjeta", "infografia") else None)
        if trat in prohibidos:
            bloqueos.append(f"plano {p.get('plano')}: tratamiento '{trat}' esta PROHIBIDO por el encargo")
        if trat == "recreacion":
            if not reglas_rec.get("permitidas", False):
                bloqueos.append(f"plano {p.get('plano')}: recreacion no permitida por el encargo")
            if reglas_rec.get("prohibido_como_gancho") and idx == 0:
                bloqueos.append(f"plano {p.get('plano')}: recreacion como GANCHO (primer plano) prohibida")
            if reglas_rec.get("requiere_justificacion") and not p.get("justificacion"):
                bloqueos.append(f"plano {p.get('plano')}: recreacion sin 'justificacion' (obligatoria)")

    # 4) prelacion: claim central con autentico disponible sustituido por no-autentico
    if reglas_rec.get("prohibido_si_existe_autentico", False):
        for cid, info in cob.items():
            if info["autentico_disponible"] and info["sustituido_por_no_autentico"]:
                bloqueos.append(f"claim central '{cid}': hay material AUTENTICO disponible ({info['recursos']}) "
                                f"pero el beat se resolvio con recreacion/tarjeta (prelacion violada)")

    apto = not bloqueos
    return {"apto": apto, "bloqueos": bloqueos, "avisos": avisos,
            "cobertura_autentica": cob,
            "veredicto": "APTO ENCARGO" if apto else "NO APTO ENCARGO"}


def informe(res: dict) -> str:
    L = [f"=== Encargo: {res['veredicto']} ==="]
    if res.get("bloqueos"):
        L.append("BLOQUEOS:")
        L += [f"  - {b}" for b in res["bloqueos"]]
    if res.get("avisos"):
        L.append("AVISOS:")
        L += [f"  - {a}" for a in res["avisos"]]
    return "\n".join(L)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("caso")
    ap.add_argument("edl_dir")
    a = ap.parse_args()
    enc = cargar(a.caso)
    d = Path(a.edl_dir)
    plan = json.loads((d / "plan_montaje.json").read_text(encoding="utf-8"))
    guion = json.loads((d / "guion.json").read_text(encoding="utf-8"))
    import expediente as EXP
    import edl_validar as E
    exp = EXP.cargar(a.caso) if EXP.ruta(a.caso).is_file() else None
    res = evaluar(enc, plan, guion, exp, E.cargar_manifiesto())
    print(informe(res))
    raise SystemExit(0 if res["apto"] else 6)
