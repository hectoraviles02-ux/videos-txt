"""Ruta automatica creada por Codex el 21-oct-2026 (evidencia en
docs/operations/evidencia/codex_autopublicacion_20261021/).

Etapa 1 (21-oct-2026): YA NO aprueba en nombre de el operador. Antes, `publicar` creaba una
aprobacion con la frase guardada en `palabras_operador`; ahora solo envia lo que el operador
ya aprobo con el boton de Telegram, y la compuerta de `distribucion` rechaza lo demas.
La politica (`activa`) sigue apagada."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from content_factory import db, distribucion

RAIZ = Path(__file__).resolve().parents[1]
CONFIG = RAIZ / "config/publicacion_automatica.json"


def politica(path=CONFIG):
    p = json.loads(Path(path).read_text(encoding="utf-8"))
    if p.get("activa") is not True or not p.get("palabras_operador"):
        raise ValueError("publicacion automatica desactivada o sin autoridad")
    if p.get("apis_pagadas") is not False:
        raise ValueError("esta ruta no autoriza APIs pagadas")
    return p


def comprobar_video(ruta, p):
    """Legibilidad y streams; NO mide verdad editorial ni calidad perceptual."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                        "-of", "json", str(ruta)], capture_output=True, text=True,
                       check=True, timeout=30)
    d = json.loads(r.stdout)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    dur = float(d["format"]["duration"])
    if not (p["duracion_min_s"] <= dur <= p["duracion_max_s"]):
        raise ValueError("duracion fuera de politica")
    if (v["width"], v["height"]) != (p["ancho"], p["alto"]):
        raise ValueError("dimensiones fuera de politica")
    if not any(s["codec_type"] == "audio" for s in d["streams"]):
        raise ValueError("video sin audio")
    subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(ruta),
                    "-f", "null", "-"], check=True, capture_output=True, timeout=180)
    return {"duracion": dur, "ancho": v["width"], "alto": v["height"], "audio": True}


def publicar(conn, pid, publicador, *, canal, fecha, evidencia, ledger, config=CONFIG):
    p = politica(config)
    pieza = conn.execute("SELECT pilar_decision FROM piezas WHERE id=?", (pid,)).fetchone()
    if not pieza or pieza[0] not in p["pilares"] or canal not in p["canales"]:
        raise ValueError("pieza o canal fuera de autorizacion delegada")
    if evidencia.get("juez", {}).get("veredicto") != "OK":
        raise ValueError("narracion sin verificacion satisfactoria")
    if evidencia.get("sha256") != db.sha256_archivo(publicador.ruta_mp4):
        raise ValueError("video distinto del verificado")
    if evidencia.get("tecnica", {}).get("audio") is not True:
        raise ValueError("falta verificacion tecnica")
    # Etapa 1: aqui NO se crea ninguna aprobacion. Sin la de el operador (boton de Telegram),
    # `publicar_pieza` lanza SinAprobacionValida antes de llamar a la plataforma.
    return distribucion.publicar_pieza(conn, pid, publicador=publicador, canal=canal,
                                       fecha=fecha, ledger=ledger)


def facebook(conn, pid, *, ruta, texto, eid, fecha, config=CONFIG, _fb=None, _http=None):
    """Reutiliza upload existente; reserva el intento antes de salir a red."""
    import os
    import requests
    from content_factory.adaptador_fb import construir_publicador
    import importlib.util
    path = RAIZ / "docs/operations/evidencia/publicacion_fb_20261005/publicar_reels_fb.py"
    if _fb is None:
        spec = importlib.util.spec_from_file_location("fb_automatico", path)
        fb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fb)
    else:
        fb = _fb
    requests = _http or requests
    fb.cargar_env()
    anterior = fb.publicados().get(eid)
    if anterior and db.obtener_publicacion(conn, pid, "facebook") is None:
        raise ValueError("pieza ya presente en registro legado; reconciliar antes de publicar")
    page = os.environ.get("FB_PAGE_ID", "").strip()
    token_file = path.parent / "token_pagina.txt"
    token = token_file.read_text().strip() if token_file.is_file() else os.environ.get("FB_PAGE_TOKEN", "").strip()
    if not page or not token:
        raise ValueError("faltan credenciales Facebook")
    token = fb.resolver_token_pagina(page, token)

    def confirmar(vid):
        try:
            r = requests.get(fb.API + "/" + str(vid),
                             params={"fields": "id,published,status", "access_token": token}, timeout=30)
            confirmado = r.status_code == 200 and r.json().get("published") is True
        except Exception:
            raise RuntimeError("no se pudo leer la confirmacion remota; ID conservado") from None
        if confirmado:
            leg = fb.publicados()
            leg[eid] = {"video_id": str(vid), "ts": fecha, "origen": "content_factory"}
            fb.guardar(leg)
        return confirmado

    def enviar(page, token, item, **kw):
        politica(config)  # permite apagar entre producción y envío
        reservado = db.registrar_publicacion(conn, pieza_id=pid, canal="facebook",
                                             publicada_en=fecha, estado_pub=db.PUB_PENDIENTE,
                                             detalle_error="intento reservado antes de red; no reintentar")
        if not reservado:
            raise RuntimeError("intento ya reservado")
        try:
            vid, resp = fb.publicar_uno(page, token, item, **kw)
        except Exception:
            raise RuntimeError("Facebook no confirmó el envío; reconciliación requerida") from None
        # Guardar identificador ANTES de consultar estado. Un timeout no pierde el ID.
        with conn:
            conn.execute("UPDATE publicaciones SET identificador_remoto=?, url=? WHERE pieza_id=? AND canal='facebook'",
                         (str(vid), "https://www.facebook.com/reel/%s/" % vid, pid))
        if not confirmar(vid):
            raise RuntimeError("subido pero publicación pública aún sin confirmar; no reintentar")
        return vid, resp

    pub = construir_publicador(mp4_path=ruta, caption=texto, page_id=page, token=token,
                                publicar_uno=enviar, ia=True)
    pub.confirmar = confirmar
    return pub


def reconciliar_pendientes(conn, fecha, config=CONFIG):
    politica(config)
    filas = conn.execute("SELECT pieza_id,identificador_remoto FROM publicaciones "
                         "WHERE canal='facebook' AND estado='PENDIENTE'").fetchall()
    for pid, vid in filas:
        if not vid:
            continue  # resultado desconocido: nunca repetir upload
        pub = facebook(conn, pid, ruta="", texto="", eid="pieza_%s" % pid, fecha=fecha, config=config)
        if pub.confirmar(vid):
            distribucion.reconciliar(conn, pid, canal="facebook", fecha=fecha,
                identificador_remoto=str(vid), url="https://www.facebook.com/reel/%s/" % vid)
    return conn.execute("SELECT COUNT(*) FROM publicaciones WHERE canal='facebook' AND estado='PENDIENTE'").fetchone()[0]


def registrar_y_publicar(resultado, tema, juez, carpeta, *, fecha, config=CONFIG):
    """Importa el artefacto recién producido con evidencia, sin inventar pasos anteriores.

    `fecha` (ISO con zona) la pone el punto de entrada: el paquete no lee el reloj (RQ-48)."""
    import shutil
    p = politica(config)
    destino = Path(carpeta) / "video_facebook.mp4"
    if destino.exists():
        raise ValueError("no se sobrescribe una entrega")
    shutil.copy2(resultado["mp4"], destino)
    ev = {"juez": juez, "tecnica": comprobar_video(destino, p),
          "sha256": db.sha256_archivo(destino), "fuente": tema,
          "limite": "Juez LLM falible; no equivale a revisión humana ni mide atractivo visual"}
    (Path(carpeta) / "validacion.json").write_text(json.dumps(ev, ensure_ascii=False, indent=2), encoding="utf-8")
    conn = db.conectar(RAIZ / "content_factory/content.db", produccion=True)
    try:
        fila = conn.execute("SELECT id FROM temas WHERE titulo=?", (tema["titulo"],)).fetchone()
        if not fila:
            raise ValueError("tema ausente de la cola real")
        pid, creada = db.registrar_pieza(conn, tema_id=fila[0], formato="reel_python",
            angulo=resultado["tema"], angulo_metodo="ciclo_diario", pilar_decision=tema["pilar"],
            estado="PRODUCIDA", creada_en=fecha, run_id=Path(carpeta).name,
            detalle_nacimiento="Artefacto producido por productor existente; evidencia: %s" % carpeta)
        if not creada:
            raise ValueError("tema con reel registrado previamente; no reemplazar")
        texto = resultado["titulo"] + " — reto práctico de Python.\n#Python #programacion #avil_es"
        (Path(carpeta) / "texto_publicacion.txt").write_text(texto, encoding="utf-8")
        pub = facebook(conn, pid, ruta=destino, texto=texto, eid=resultado["eid"], fecha=fecha, config=config)
        res = publicar(conn, pid, pub, canal="facebook", fecha=fecha, evidencia=ev,
                       ledger=distribucion.Ledger(RAIZ / "content_factory/publicaciones.jsonl"), config=config)
        (Path(carpeta) / "publicacion.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
        return res
    finally:
        conn.close()
