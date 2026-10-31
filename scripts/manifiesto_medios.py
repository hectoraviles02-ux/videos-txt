# -*- coding: utf-8 -*-
"""
Manifiesto reproducible de MEDIOS PESADOS (public/ + biblioteca/assets).

Motivo: public/ (~43 MB) y biblioteca_audiovisual/assets (~26 MB) no deben entrar
crudos a Git. Git LFS requiere remoto compatible y HOY no hay remoto -> se usa
store externo + este manifiesto (decision #4 del usuario).

El manifiesto permite, en OTRO equipo: colocar los medios desde el store elegido
y VALIDAR su integridad por sha256. Para cada archivo registra ruta, tamanio,
sha256, y (cruzando con biblioteca_audiovisual/_manifiesto.json) recurso_id,
url_origen y si es re-descargable (tiene url y no es derivado).

Uso:
  manifiesto_medios.py construir   -> escribe manifiesto_medios.json
  manifiesto_medios.py verificar   -> re-hashea y reporta OK/ALTERADO/FALTANTE
"""
import hashlib
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DIRS = [BASE / "motor_documental_test" / "public", BASE / "biblioteca_audiovisual" / "assets"]
BIBLIO = BASE / "biblioteca_audiovisual" / "_manifiesto.json"
OUT = BASE / "manifiesto_medios.json"


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _biblio():
    try:
        d = json.loads(BIBLIO.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else d.get("recursos", d.get("items", []))
    except Exception:
        return []


def construir():
    recs = _biblio()
    by_base = {}
    for r in recs:
        al = r.get("archivo_local") or ""
        if al:
            by_base.setdefault(Path(al).name.lower(), r)
    # 1) completar sha256 faltantes en biblioteca (sobre archivo_local si existe)
    completados = 0
    for r in recs:
        if not r.get("hash_archivo") and r.get("archivo_local") and Path(r["archivo_local"]).is_file():
            r["hash_archivo"] = sha256_file(r["archivo_local"])
            completados += 1
    if completados:
        BIBLIO.write_text(json.dumps(recs, ensure_ascii=False, indent=2), encoding="utf-8")
    # 2) manifiesto de los medios fisicos
    entradas = []
    for d in DIRS:
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(BASE).as_posix()
            r = by_base.get(p.name.lower(), {})
            derivado = bool(r.get("derivado_de")) or (r.get("clase") == "derivado")
            url = r.get("url_origen")
            entradas.append({
                "ruta": rel,
                "bytes": p.stat().st_size,
                "sha256": sha256_file(p),
                "recurso_id": r.get("id"),
                "url_origen": url,
                "re_descargable": bool(url) and not derivado,
                "derivado": derivado,
            })
    man = {
        "generado": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
        "nota": "Medios gestionados fuera de Git. Store externo pendiente de eleccion del usuario.",
        "total_archivos": len(entradas),
        "total_bytes": sum(e["bytes"] for e in entradas),
        "biblioteca_sha256_completados": completados,
        "archivos": entradas,
    }
    OUT.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    refetch = sum(1 for e in entradas if e["re_descargable"])
    print(f"Manifiesto: {OUT}")
    print(f"  archivos={len(entradas)}  bytes={man['total_bytes']/1048576:.1f}MB  "
          f"re-descargables={refetch}  derivados(sin url)={len(entradas)-refetch}")
    print(f"  sha256 completados en biblioteca: {completados}")
    return 0


def verificar(root=None):
    """Re-hashea cada archivo del manifiesto. root permite validar una copia en otra ruta/equipo."""
    man = json.loads(OUT.read_text(encoding="utf-8"))
    base = Path(root) if root else BASE
    ok = alterado = faltante = 0
    problemas = []
    for e in man["archivos"]:
        p = base / e["ruta"]
        if not p.is_file():
            faltante += 1
            problemas.append(f"FALTANTE  {e['ruta']}")
            continue
        if sha256_file(p) == e["sha256"]:
            ok += 1
        else:
            alterado += 1
            problemas.append(f"ALTERADO  {e['ruta']}")
    print(f"OK={ok}  ALTERADO={alterado}  FALTANTE={faltante}  (total {len(man['archivos'])})")
    for pr in problemas[:20]:
        print("  ", pr)
    return 0 if alterado == 0 and faltante == 0 else 6


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "construir"
    root = sys.argv[2] if len(sys.argv) > 2 else None
    return construir() if cmd == "construir" else verificar(root)


if __name__ == "__main__":
    raise SystemExit(main())
