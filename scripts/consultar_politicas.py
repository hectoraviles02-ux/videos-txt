"""Vigila texto documental, no certifica elegibilidad ni interpreta cambios.

Ejecutar semanalmente o antes de planificar: python scripts/consultar_politicas.py
--force consulta aun con capturas recientes. Salida 2: requiere revisión, 0: sin novedades.
Capturas nuevas en runs/politicas_<UTC>_<uuid>; nunca sobrescribe raw ni reglas.
Usa Jina Reader sin clave/API pagada como transporte de páginas oficiales.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]


def normalizar(texto):
    contenido = texto.partition("Markdown Content:")[2]
    if not contenido:
        raise ValueError("Respuesta sin cuerpo Markdown verificable")
    return re.sub(r"\s+", " ", contenido).strip()


def evaluar(texto, fuente, minimo, previo=None):
    cuerpo = normalizar(texto)
    if len(cuerpo) < minimo or not all(
        a.casefold() in cuerpo.casefold() for a in fuente["anclas"]
    ):
        raise ValueError("Contenido insuficiente o página distinta: revisar manualmente")
    digest = hashlib.sha256(cuerpo.encode()).hexdigest()
    estado = "BASE_PENDIENTE_REVISION" if not previo else (
        "SIN_CAMBIO_TEXTUAL" if digest == previo["sha256"] else "CAMBIO_REQUIERE_REVISION"
    )
    return {"estado": estado, "sha256": digest, "caracteres": len(cuerpo),
            "revision_pendiente": fuente.get("sha256_revisado") != digest}


def descargar(url, timeout):
    request = Request("https://r.jina.ai/" + url, headers={
        "User-Agent": "VIDEOS-TXT-policy-watch/1", "X-No-Cache": "true"})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def anteriores(runs):
    resultado = {}
    for archivo in sorted(runs.glob("politicas_*/report.json")):
        report = json.loads(archivo.read_text(encoding="utf-8"))
        for item in report["fuentes"]:
            if item.get("sha256") and item["estado"] != "NO_VENCIDA":
                resultado[item["id"]] = item
            elif item["estado"] == "CONSULTA_FALLIDA" and item["id"] in resultado:
                resultado[item["id"]]["ultimo_intento_fallido"] = True
    return resultado


def ejecutar(config, runs, force=False, fetch=descargar, now=None):
    now = now or datetime.now(timezone.utc)
    previos = anteriores(runs)
    carpeta = runs / ("politicas_" + now.strftime("%Y%m%dT%H%M%S%fZ_") + uuid.uuid4().hex[:8])
    carpeta.mkdir(parents=True, exist_ok=False)
    reporte = {"consultado_utc": now.isoformat(), "fuentes": [],
               "limite": "Detecta cambios textuales; no aprueba políticas, cuentas ni publicación."}
    for fuente in config["fuentes"]:
        previo = previos.get(fuente["id"])
        if previo and previo["url"] != fuente["url"]:
            previo = None
        item = {"id": fuente["id"], "url": fuente["url"], "consultado_utc": now.isoformat()}
        edad = (now - datetime.fromisoformat(previo["consultado_utc"])).total_seconds() / 3600 if previo else None
        if previo and not force and not previo.get("ultimo_intento_fallido") and 0 <= edad < config["intervalo_horas"]:
            item.update(estado="NO_VENCIDA", ultima_consulta=previo["consultado_utc"],
                        estado_previo=previo["estado"],
                        revision_pendiente=fuente.get("sha256_revisado") != previo["sha256"])
        else:
            try:
                texto = fetch(fuente["url"], config["timeout_segundos"])
                captura = carpeta / (fuente["id"] + ".txt")
                captura.write_text(texto, encoding="utf-8")
                item.update(evaluar(texto, fuente, config["minimo_caracteres"], previo))
                item["captura"] = str(captura)
            except Exception as exc:
                item.update(estado="CONSULTA_FALLIDA", error=str(exc))
        reporte["fuentes"].append(item)
        (carpeta / "report.json").write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
    reporte["requiere_revision"] = any(
        i["estado"] not in ("SIN_CAMBIO_TEXTUAL", "NO_VENCIDA") or
        i.get("revision_pendiente", False)
        for i in reporte["fuentes"])
    (carpeta / "report.json").write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
    return carpeta, reporte


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    config = json.loads((ROOT / "config/seguimiento_politicas.json").read_text(encoding="utf-8"))
    carpeta, reporte = ejecutar(config, ROOT / "runs", args.force)
    print(carpeta)
    for item in reporte["fuentes"]:
        print(item["id"], item["estado"])
    raise SystemExit(2 if reporte["requiere_revision"] else 0)
