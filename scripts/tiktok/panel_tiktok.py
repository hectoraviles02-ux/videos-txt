# -*- coding: utf-8 -*-
"""Panel local «AVILES Publicador» para publicar en TikTok cumpliendo las Content Sharing Guidelines
(https://developers.tiktok.com/doc/content-sharing-guidelines): cuenta visible, título editable, privacidad sin
valor por defecto (solo las opciones que da creator_info), interacciones apagadas por defecto, declaración de
contenido comercial, consentimiento de música, vista previa, y estado de la publicación.

Uso: python panel_tiktok.py   → abre http://localhost:8766  (solo escucha en esta computadora)
Reutiliza el token y las llamadas de publicar_tiktok.py. No publica nada sin el clic del usuario."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import requests
from flask import Flask, abort, jsonify, request, send_file

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
import publicar_tiktok as pt  # noqa: E402

REPO = pt.REPO
CARPETAS = [REPO / "docs/operations/evidencia/poc_terminal_shorts_20261005/reels_v2_yt", REPO / "guiones"]
app = Flask(__name__)


def permitido(p: Path) -> bool:
    p = p.resolve()
    return p.suffix.lower() == ".mp4" and p.is_file() and any(c.resolve() in p.parents for c in CARPETAS)


def duracion(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def candidatos():
    pub = json.loads(pt.LOG.read_text(encoding="utf-8")) if pt.LOG.is_file() else {}
    hechos = {v.get("file") for v in pub.values()}
    vids = sorted(CARPETAS[0].glob("e*_v2yt.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)[:15]
    vids += sorted(CARPETAS[1].glob("*/*/*_YT.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)[:6]
    return [{"ruta": str(v), "nombre": v.name, "publicado": v.name in hechos} for v in vids]


ESTILO = """<style>
:root{--bg:#0b0f16;--panel:#141a24;--txt:#e8e8e8;--mut:#8a93a3;--cian:#50dcf0;--amar:#f0da50;--rojo:#fe2c55}
body{margin:0;font-family:system-ui,Segoe UI,sans-serif;background:var(--bg);color:var(--txt)}
header{padding:14px 20px;border-bottom:1px solid #222b38;display:flex;gap:10px;align-items:center}
header b{color:var(--cian)} main{max-width:1000px;margin:0 auto;padding:20px}
a{color:var(--cian)} .lista a{display:block;padding:10px;border-radius:8px;background:var(--panel);margin:6px 0;text-decoration:none}
.lista small{color:var(--mut)} .grid{display:grid;grid-template-columns:300px 1fr;gap:24px}
video{width:300px;border-radius:12px;background:#000} label{display:block;margin:14px 0 6px;font-weight:600}
textarea,select{width:100%;box-sizing:border-box;background:var(--panel);color:var(--txt);border:1px solid #2a3443;border-radius:8px;padding:10px}
.fila{display:flex;align-items:center;gap:10px;margin:8px 0} .gris{opacity:.4}
.etiqueta{display:inline-block;background:#2a3443;color:var(--amar);border-radius:6px;padding:3px 8px;font-size:13px;margin-top:8px}
button{background:var(--rojo);color:#fff;border:0;border-radius:8px;padding:12px 22px;font-size:16px;cursor:pointer}
button:disabled{background:#4a2530;cursor:not-allowed} .aviso{color:var(--mut);font-size:13px}
#estado{margin-top:16px;padding:12px;border-radius:8px;background:var(--panel);display:none}
</style>"""


@app.get("/")
def inicio():
    filas = "".join('<a href="/publicar?f=%s">%s %s<br><small>%s</small></a>' % (
        requests.utils.quote(c["ruta"]), c["nombre"], "✅" if c["publicado"] else "", c["ruta"]) for c in candidatos())
    return ("<!doctype html><meta charset=utf-8><title>AVILES Publicador</title>%s<header><b>AVILES Publicador</b>"
            "· publicar en TikTok</header><main><h2>Elige un video</h2><div class=lista>%s</div></main>") % (ESTILO, filas)


@app.get("/video")
def video():
    p = Path(request.args.get("f", ""))
    if not permitido(p):
        abort(404)
    return send_file(p, mimetype="video/mp4")


@app.get("/publicar")
def publicar():
    p = Path(request.args.get("f", ""))
    if not permitido(p):
        abort(404)
    info = pt._post("/post/publish/creator_info/query/", {})  # se consulta CADA vez que se abre la página
    d = duracion(p)
    maxd = info.get("max_video_post_duration_sec", 0)
    opciones = "".join('<option value="%s">%s</option>' % (o, {"PUBLIC_TO_EVERYONE": "Todos (público)",
                                                               "MUTUAL_FOLLOW_FRIENDS": "Amigos",
                                                               "FOLLOWER_OF_CREATOR": "Seguidores",
                                                               "SELF_ONLY": "Solo yo (privado)"}.get(o, o))
                       for o in info.get("privacy_level_options", []))
    bloqueo = "" if d <= maxd else "<p style='color:var(--rojo)'>Este video dura %.0f s y tu cuenta permite %s s. No se puede publicar.</p>" % (d, maxd)
    inter = "".join('<div class="fila %s"><input type=checkbox id=%s %s><label for=%s style="margin:0;font-weight:400">%s</label></div>' % (
        "gris" if info.get(k + "_disabled") else "", k, "disabled" if info.get(k + "_disabled") else "", k, t)
        for k, t in (("comment", "Permitir comentarios"), ("duet", "Permitir dúo"), ("stitch", "Permitir stitch")))
    return """<!doctype html><meta charset=utf-8><title>Publicar en TikTok</title>%s
<header><b>AVILES Publicador</b>· publicar en TikTok</header><main>
<p>Se publicará en la cuenta: <b>%s</b> (@%s)</p>%s
<div class=grid><div><video src="/video?f=%s" controls></video><p class=aviso>Vista previa · %.1f s</p></div>
<div>
<label for=titulo>Título (puedes editarlo todo, incluidos los hashtags)</label>
<textarea id=titulo rows=3 maxlength=2200></textarea>
<label for=priv>¿Quién puede ver este video?</label>
<select id=priv><option value="" selected disabled>Elige una opción…</option>%s</select>
<label>Interacciones</label>%s
<label>Contenido comercial</label>
<div class=fila><input type=checkbox id=com><label for=com style="margin:0;font-weight:400">Este video promociona una marca, producto o servicio</label></div>
<div id=comopc style="display:none;margin-left:26px">
 <div class=fila><input type=checkbox id=tumarca><label for=tumarca style="margin:0;font-weight:400">Tu marca</label></div>
 <div class=fila><input type=checkbox id=marcaterc><label for=marcaterc style="margin:0;font-weight:400">Contenido de marca (de un tercero)</label></div>
 <div id=etq></div>
</div>
<p id=consent class=aviso></p>
<span id=pista class=aviso></span><br>
<button id=btn disabled>Publicar en TikTok</button>
<div id=estado></div>
</div></div></main>
<script>
const F=%s, $=id=>document.getElementById(id);
$('titulo').value=%s;
function actualizar(){
  const com=$('com').checked, tm=$('tumarca').checked, mt=$('marcaterc').checked;
  $('comopc').style.display=com?'block':'none';
  const priv=$('priv'), solo=[...priv.options].find(o=>o.value==='SELF_ONLY');
  if(solo){solo.disabled=com&&mt; solo.title=(com&&mt)?'El contenido de marca no puede ser privado':'';
    if(com&&mt&&priv.value==='SELF_ONLY'){priv.value='';}}
  let et=''; if(com&&tm&&!mt) et='Promocional'; if(com&&mt) et='Colaboración pagada';
  $('etq').innerHTML=et?'<span class=etiqueta>Tu video se etiquetará como «'+et+'»</span>':'';
  const musica='<a href="https://www.tiktok.com/legal/page/global/music-usage-confirmation/en" target=_blank>Confirmación de uso de música</a>';
  const marca='<a href="https://www.tiktok.com/legal/page/global/bc-policy/en" target=_blank>Política de contenido de marca</a>';
  $('consent').innerHTML='By posting, you agree to TikTok\\'s '+(com&&mt?marca+' y '+musica:musica)+'. (Al publicar, aceptas lo anterior.)';
  let ok=!!priv.value && %s, pista='';
  if(!priv.value) pista='Elige quién puede ver el video.';
  if(com&&!tm&&!mt){ok=false;pista='Si activas contenido comercial, marca al menos una opción.';}
  $('btn').disabled=!ok; $('btn').title=pista; $('pista').textContent=pista;
}
['com','tumarca','marcaterc','priv'].forEach(i=>$(i).addEventListener('change',actualizar)); actualizar();
$('btn').onclick=async()=>{
  $('btn').disabled=true; const e=$('estado'); e.style.display='block'; e.textContent='Subiendo a TikTok…';
  const r=await fetch('/api/publicar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    f:F,titulo:$('titulo').value,priv:$('priv').value,comment:$('comment').checked,duet:$('duet').checked,
    stitch:$('stitch').checked,tumarca:$('com').checked&&$('tumarca').checked,marcaterc:$('com').checked&&$('marcaterc').checked})});
  const j=await r.json(); if(!j.publish_id){e.textContent='Error: '+(j.error||'desconocido');return;}
  e.innerHTML='Enviado. TikTok puede tardar unos minutos en procesarlo antes de que aparezca en tu perfil.<br>Estado: <b id=st>…</b>';
  for(let i=0;i<40;i++){await new Promise(s=>setTimeout(s,4000));
    const s=await (await fetch('/api/estado?id='+encodeURIComponent(j.publish_id))).json(); $('st').textContent=s.estado;
    if(['PUBLISH_COMPLETE','FAILED','SEND_TO_USER_INBOX'].includes(s.estado))break;}
};
</script>""" % (ESTILO, info.get("creator_nickname", ""), info.get("creator_username", ""), bloqueo,
                requests.utils.quote(str(p)), d, opciones, inter, json.dumps(str(p)), json.dumps(""),
                "true" if d <= maxd else "false")


def _ruta_bd_compuerta():
    return None   # None = la cola real (content_factory/content.db)


def _compuerta(ruta, texto, canal, programada_para=None):
    """Etapa 1 (21-oct-2026): no se sube nada sin aprobacion vigente de el operador en la cola
    (SHA-256 del archivo + texto + canal + horario). Lanza PublicacionBloqueada."""
    import sys as _sys
    from datetime import datetime as _dt
    _raiz = str(Path(__file__).resolve().parents[2])
    if _raiz not in _sys.path:
        _sys.path.insert(0, _raiz)
    from content_factory import guardia_publicacion
    return guardia_publicacion.exigir(ruta, texto, canal, programada_para=programada_para,
                                      en=_dt.now().astimezone().isoformat(), ruta_bd=_ruta_bd_compuerta())


@app.post("/api/publicar")
def api_publicar():
    j = request.get_json()
    p = Path(j.get("f", ""))
    if not permitido(p):
        return jsonify(error="archivo no permitido"), 400
    info = pt._post("/post/publish/creator_info/query/", {})
    if j.get("priv") not in info.get("privacy_level_options", []):
        return jsonify(error="elige una privacidad válida"), 400
    try:
        _compuerta(p, (j.get("titulo") or "")[:2200], "tiktok")
    except Exception as e:  # noqa: BLE001 - PublicacionBloqueada u otro: no se sube
        return jsonify(error=str(e)[:300]), 403
    n = p.stat().st_size
    try:
        d = pt._post("/post/publish/video/init/", {
            "post_info": {"title": (j.get("titulo") or "")[:2200], "privacy_level": j["priv"],
                          "disable_comment": not j.get("comment"), "disable_duet": not j.get("duet"),
                          "disable_stitch": not j.get("stitch"), "brand_organic_toggle": bool(j.get("tumarca")),
                          "brand_content_toggle": bool(j.get("marcaterc"))},
            "source_info": {"source": "FILE_UPLOAD", "video_size": n, "chunk_size": n, "total_chunk_count": 1}})
        with open(p, "rb") as fh:
            r = requests.put(d["upload_url"], data=fh.read(), timeout=600, headers={
                "Content-Type": "video/mp4", "Content-Length": str(n), "Content-Range": "bytes 0-%d/%d" % (n - 1, n)})
        if r.status_code not in (200, 201):
            return jsonify(error="falló la subida (HTTP %s)" % r.status_code), 502
    except Exception as e:  # noqa: BLE001
        return jsonify(error=str(e)[:200]), 502
    pub = json.loads(pt.LOG.read_text(encoding="utf-8")) if pt.LOG.is_file() else {}
    pub["panel_" + p.stem] = {"publish_id": d["publish_id"], "privacidad": j["priv"], "file": p.name,
                              "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "via": "panel"}
    pt.LOG.write_text(json.dumps(pub, ensure_ascii=False, indent=1), encoding="utf-8")
    return jsonify(publish_id=d["publish_id"])


@app.get("/api/estado")
def api_estado():
    try:
        return jsonify(estado=pt._post("/post/publish/status/fetch/", {"publish_id": request.args["id"]}).get("status", "?"))
    except Exception as e:  # noqa: BLE001
        return jsonify(estado="error: " + str(e)[:120])


if __name__ == "__main__":
    pt.env()
    app.run(host="127.0.0.1", port=8766)
