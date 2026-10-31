# -*- coding: utf-8 -*-
"""Importa el stock LEGADO de reels (e01-e35) a content.db como piezas trazables.

Cambio SDD: content-factory-produccion-y-distribucion (E1).

Principios (ajustes del operador):
- Estado DERIVADO DEL ARTEFACTO fisico, no inflado:
    hay MP4 renderizado           -> PRODUCIDA
    ademas publicacion COMPROBABLE (video_id de la API) -> PUBLICADA
    solo banco/codigo ejecutado, sin MP4 -> VERIFICADA (codigo verificado por ejecucion)
- Evidencia por EVENTOS, no por `claims`: estos reels no tienen afirmaciones
  factuales externas; su evidencia es la EJECUCION LOCAL (code + output real) y el
  render MP4. `claims` se reserva para afirmaciones verificables con fuente+URL.
- Identidad ESTABLE independiente del titulo: `legacy_id` (eNN) + origen. El
  `hash_titulo` del tema se deriva de la identidad legada, no del texto visible,
  asi que renombrar el reel no crea un tema nuevo.
- Idempotente: reimportar no duplica; un conflicto REAL de identidad (mismo
  legacy_id, artefacto distinto) FALLA de forma visible, no se fusiona en silencio.

NO publica nada. NO toca content.db real salvo que se pase --produccion (no lo hagas).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from content_factory import db

RAIZ = Path(__file__).resolve().parent.parent
POC = RAIZ / "docs/operations/evidencia/poc_terminal_shorts_20261005"
PUB = RAIZ / "docs/operations/evidencia/publicacion_fb_20261005"
REELS = POC / "reels"
BANCO = POC / "_banco.json"
EJERCICIOS = POC / "_ejercicios.json"
PUBLICADOS = PUB / "_publicados.json"

ORIGEN = "legacy_python_bank"
PILAR = "P4"            # Automatizacion practica
FORMATO = "corto"
ANGULO = "tutorial"
ANGULO_METODO = "legado"
CANAL = "facebook"

NOTA_LEGADO = ("stock legado previo al pipeline completo; importado sin reconstruir "
               "investigacion inexistente; evidencia = ejecucion local (code+output) y render MP4")


class ConflictoImportacion(RuntimeError):
    """Mismo legacy_id con un artefacto distinto: no se fusiona en silencio."""


def _hash_titulo(legacy_id: str) -> str:
    return hashlib.sha256(("%s|%s" % (ORIGEN, legacy_id)).encode("utf-8")).hexdigest()


def _hash_archivo(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _mp4_de(legacy_id: str) -> Path | None:
    hits = sorted(REELS.glob("%s_*.mp4" % legacy_id))
    return hits[0] if hits else None


def _cargar_stock() -> list[dict]:
    """Une e01-e10 (_ejercicios.json) y e11-e35 (_banco.json) en una lista comun."""
    items: dict[str, dict] = {}
    if EJERCICIOS.is_file():
        for e in json.loads(EJERCICIOS.read_text(encoding="utf-8")):
            items[e["id"]] = {
                "id": e["id"], "titulo": e.get("titulo", e["id"]),
                "code": e.get("code", ""), "output": e.get("output", ""),
                "estado_ejec": e.get("estado", ""), "fuente_banco": "_ejercicios.json",
            }
    if BANCO.is_file():
        for e in json.loads(BANCO.read_text(encoding="utf-8")):
            items[e["id"]] = {
                "id": e["id"], "titulo": e.get("titulo", e["id"]),
                "code": e.get("code", ""), "output": e.get("output", ""),
                "estado_ejec": e.get("estado", ""), "fuente_banco": "_banco.json",
            }
    return [items[k] for k in sorted(items)]


def _publicados() -> dict:
    if PUBLICADOS.is_file():
        return json.loads(PUBLICADOS.read_text(encoding="utf-8"))
    return {}


def _estado_derivado(legacy_id: str, mp4: Path | None, pub: dict, estado_ejec: str) -> tuple[str, dict]:
    """Deriva el estado de la pieza del artefacto fisico. Devuelve (estado, info_pub)."""
    registro = pub.get(legacy_id, {})
    vid = str(registro.get("video_id", "")).strip()
    comprobable = bool(vid) and vid.isdigit()   # id numerico de la API; 'manual' NO cuenta
    if mp4 is None:
        # sin render: a lo sumo el codigo esta verificado por ejecucion
        return ("VERIFICADA" if estado_ejec == "OK" else "GUIONADA"), {}
    if comprobable:
        return "PUBLICADA", {"identificador_remoto": vid, "fuente": registro}
    return "PRODUCIDA", ({"reporte_manual": registro} if registro else {})


def importar(conn, *, fecha: str, stock: list[dict] | None = None, publicados: dict | None = None) -> dict:
    """Importa el stock al `conn` dado (idempotente). Devuelve un resumen."""
    db.crear_esquema(conn)
    stock = stock if stock is not None else _cargar_stock()
    pub = publicados if publicados is not None else _publicados()
    creados = saltados = publicadas = 0
    detalle: list[dict] = []
    for it in stock:
        lid = it["id"]
        mp4 = _mp4_de(lid)
        art_hash = _hash_archivo(mp4) if mp4 is not None else hashlib.sha256(
            it["code"].encode("utf-8")).hexdigest()
        estado, info_pub = _estado_derivado(lid, mp4, pub, it.get("estado_ejec", ""))

        ht = _hash_titulo(lid)
        ident = {"origen": ORIGEN, "legacy_id": lid, "artefacto_hash": art_hash,
                 "artefacto": (mp4.name if mp4 else None)}

        # idempotencia + conflicto visible por identidad estable
        fila = conn.execute("SELECT id, evidencia_json FROM temas WHERE hash_titulo = ?", (ht,)).fetchone()
        if fila is not None:
            prev = json.loads(fila["evidencia_json"] or "[]")
            prev_ident = next((x for x in prev if isinstance(x, dict) and x.get("legacy_id") == lid), {})
            if prev_ident.get("artefacto_hash") and prev_ident["artefacto_hash"] != art_hash:
                raise ConflictoImportacion(
                    "%s ya importado con artefacto %s; ahora el artefacto es %s. No se "
                    "fusiona en silencio: resuelve a mano cual es el bueno."
                    % (lid, prev_ident["artefacto_hash"][:12], art_hash[:12]))

        tema_id = db.upsert_tema(conn, {
            "hash_titulo": ht, "titulo": it["titulo"], "pilar": PILAR,
            "intent": "educativo", "creado_en": fecha, "estado": "SELECCIONADA",
            "metodo_clasificacion": "legado", "evidencia": [ident],
        })

        out = (it.get("output") or "").strip().replace("\n", " ")[:160]
        detalle_nac = ("%s | legacy_id=%s | artefacto=%s | salida_real=%r"
                       % (NOTA_LEGADO, lid, (mp4.name if mp4 else "(sin MP4)"), out))
        pieza_id, creada = db.registrar_pieza(
            conn, tema_id=tema_id, formato=FORMATO, angulo=ANGULO, angulo_metodo=ANGULO_METODO,
            pilar_decision=PILAR, estado=estado, creada_en=fecha, detalle_nacimiento=detalle_nac)
        if creada:
            creados += 1
        else:
            saltados += 1

        # Si el artefacto esta PUBLICADO de forma comprobable, deja tambien la fila
        # de publicacion (idempotente). El reporte manual (e01-e10) NO se marca como
        # publicacion comprobable: queda constancia en el evento de nacimiento.
        if estado == "PUBLICADA" and info_pub.get("identificador_remoto"):
            vid = info_pub["identificador_remoto"]
            db.registrar_publicacion(
                conn, pieza_id=pieza_id, canal=CANAL, publicada_en=fecha,
                identificador_remoto=vid, url="/reel/%s/" % vid, estado_pub=db.PUB_CONFIRMADA)
            publicadas += 1

        detalle.append({"legacy_id": lid, "pieza_id": pieza_id, "estado": estado, "creada": creada})

    return {"creados": creados, "saltados": saltados, "publicadas": publicadas,
            "total": len(stock), "detalle": detalle}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Importa el stock legado de reels a content.db")
    ap.add_argument("--db", default=None, help="ruta de la base (por defecto content.test.db)")
    ap.add_argument("--produccion", action="store_true", help="NO usar salvo que sepas lo que haces")
    ap.add_argument("--fecha", default=None, help="fecha de importacion (YYYY-MM-DD)")
    a = ap.parse_args(argv)
    if not a.fecha:
        ap.error("--fecha es obligatoria (YYYY-MM-DD); no se lee el reloj del sistema (determinismo, D-05/replay)")
    fecha = a.fecha
    conn = db.conectar(a.db, produccion=a.produccion)
    try:
        res = importar(conn, fecha=fecha)
    finally:
        conn.close()
    print("IMPORTACION stock legado -> creados=%d saltados=%d publicadas=%d total=%d"
          % (res["creados"], res["saltados"], res["publicadas"], res["total"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
