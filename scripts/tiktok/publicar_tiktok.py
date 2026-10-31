# -*- coding: utf-8 -*-
"""Publicador de TikTok (Content Posting API, app «AVILES Publicador», 17-oct-2026).

  python publicar_tiktok.py autorizar            # una vez: abre el navegador, operador acepta, guarda token.json
  python publicar_tiktok.py info                 # qué opciones de privacidad permite TikTok a esta cuenta
  python publicar_tiktok.py subir --id e92 --file <mp4> --titulo "..." [--borrador]

- Credenciales: TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET en C:\\VIDEOS-TXT\\.env (nunca se imprimen).
- Token del usuario: docs/operations/evidencia/publicacion_tiktok_20261017/token.json (gitignored: **/token.json).
- Mientras la app NO esté auditada, TikTok solo permite publicar en privado (SELF_ONLY); `--borrador` manda
  el video a la bandeja de TikTok para que operador lo publique desde el celular.
- Idempotente por --id en _tiktok_publicados.json (no re-sube). NO se publica nada sin orden explícita."""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import secrets
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[2]
EVID = REPO / "docs/operations/evidencia/publicacion_tiktok_20261017"
TOKEN = EVID / "token.json"
LOG = EVID / "_tiktok_publicados.json"
REDIRECT = "http://localhost:8765/callback/"
SCOPES = "user.info.basic,video.publish,video.upload"
API = "https://open.tiktokapis.com/v2"


def env():
    for l in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in l and not l.lstrip().startswith("#"):
            k, v = l.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"'))
    ck, cs = os.environ.get("TIKTOK_CLIENT_KEY", ""), os.environ.get("TIKTOK_CLIENT_SECRET", "")
    if not ck or not cs:
        sys.exit("Faltan TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET en C:\\VIDEOS-TXT\\.env")
    return ck, cs


def guardar_token(t):
    EVID.mkdir(parents=True, exist_ok=True)
    t["obtenido"] = int(time.time())
    TOKEN.write_text(json.dumps(t, indent=1), encoding="utf-8")


def autorizar():
    ck, cs = env()
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = hashlib.sha256(verifier.encode()).hexdigest()  # TikTok desktop: SHA256 en HEX
    state = secrets.token_urlsafe(16)
    url = "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode({
        "client_key": ck, "response_type": "code", "scope": SCOPES, "redirect_uri": REDIRECT,
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
    res = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            res.update(q)
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
            ok = "code" in q and q.get("state") == state
            self.wfile.write(("<h2>%s</h2>" % ("Listo, ya puedes cerrar esta pestaña." if ok else "Algo falló: %s"
                                                % q.get("error_description", q.get("error", "")))).encode("utf-8"))

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("localhost", 8765), H)
    print("Abriendo TikTok para autorizar… (si no se abre, copia esta dirección en el navegador)")
    print(url.split("client_key=")[0] + "client_key=…")  # sin imprimir la clave completa
    if os.environ.get("TIKTOK_URL_FILE"):  # para abrirla en una pestaña concreta (la clave de cliente no es secreta)
        Path(os.environ["TIKTOK_URL_FILE"]).write_text(url, encoding="utf-8")
    else:
        webbrowser.open(url)
    while "code" not in res and "error" not in res:
        srv.handle_request()
    if res.get("state") != state or "code" not in res:
        sys.exit("Autorización fallida: %s" % res.get("error_description", res.get("error", "state distinto")))
    r = requests.post(API + "/oauth/token/", headers={"Content-Type": "application/x-www-form-urlencoded"}, data={
        "client_key": ck, "client_secret": cs, "code": res["code"], "grant_type": "authorization_code",
        "redirect_uri": REDIRECT, "code_verifier": verifier}, timeout=30).json()
    if "access_token" not in r:
        sys.exit("No se obtuvo token: %s" % {k: v for k, v in r.items() if "token" not in k})
    guardar_token(r)
    print("AUTORIZADO. scopes:", r.get("scope"), "| open_id guardado | vence en", r.get("expires_in"), "s")


def token():
    if not TOKEN.is_file():
        sys.exit("Primero: python publicar_tiktok.py autorizar")
    t = json.loads(TOKEN.read_text(encoding="utf-8"))
    if time.time() > t["obtenido"] + t.get("expires_in", 0) - 300:
        ck, cs = env()
        r = requests.post(API + "/oauth/token/", headers={"Content-Type": "application/x-www-form-urlencoded"},
                          data={"client_key": ck, "client_secret": cs, "grant_type": "refresh_token",
                                "refresh_token": t["refresh_token"]}, timeout=30).json()
        if "access_token" not in r:
            sys.exit("No se pudo renovar el token; corre de nuevo «autorizar». %s" % r.get("error_description", ""))
        guardar_token(r); t = r
    return t["access_token"]


def _post(path, body):
    r = requests.post(API + path, headers={"Authorization": "Bearer " + token(),
                                           "Content-Type": "application/json; charset=UTF-8"}, json=body, timeout=60)
    j = r.json()
    if j.get("error", {}).get("code") not in (None, "ok"):
        raise RuntimeError("%s: %s" % (j["error"].get("code"), j["error"].get("message")))
    return j.get("data", {})


def info():
    d = _post("/post/publish/creator_info/query/", {})
    print(json.dumps({k: d.get(k) for k in ("creator_username", "privacy_level_options", "max_video_post_duration_sec",
                                             "comment_disabled", "duet_disabled", "stitch_disabled")}, ensure_ascii=False))
    return d


def _ruta_bd_compuerta():
    return None   # None = la cola real (content_factory/content.db)


def _compuerta(ruta, texto, canal, programada_para=None):
    """Etapa 1 (21-oct-2026): no se sube nada sin aprobacion vigente de operador en la cola
    (SHA-256 del archivo + texto + canal + horario). Lanza PublicacionBloqueada."""
    import sys as _sys
    from datetime import datetime as _dt
    _raiz = str(Path(__file__).resolve().parents[2])
    if _raiz not in _sys.path:
        _sys.path.insert(0, _raiz)
    from content_factory import guardia_publicacion
    return guardia_publicacion.exigir(ruta, texto, canal, programada_para=programada_para,
                                      en=_dt.now().astimezone().isoformat(), ruta_bd=_ruta_bd_compuerta())


def subir(eid, archivo, titulo, borrador):
    pub = json.loads(LOG.read_text(encoding="utf-8")) if LOG.is_file() else {}
    if eid in pub:
        print("YA SUBIDO", eid, pub[eid]); return
    _compuerta(archivo, titulo, "tiktok")
    f = Path(archivo); n = f.stat().st_size
    fuente = {"source": "FILE_UPLOAD", "video_size": n, "chunk_size": n, "total_chunk_count": 1}
    if borrador:
        d = _post("/post/publish/inbox/video/init/", {"source_info": fuente}); privacidad = "BORRADOR"
    else:
        opciones = info().get("privacy_level_options", [])
        privacidad = "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in opciones else "SELF_ONLY"
        d = _post("/post/publish/video/init/", {"post_info": {"title": titulo[:2200], "privacy_level": privacidad,
                                                               "disable_comment": False, "disable_duet": False,
                                                               "disable_stitch": False},
                                                 "source_info": fuente})
    with open(f, "rb") as fh:
        r = requests.put(d["upload_url"], data=fh.read(), timeout=600, headers={
            "Content-Type": "video/mp4", "Content-Length": str(n), "Content-Range": "bytes 0-%d/%d" % (n - 1, n)})
    if r.status_code not in (200, 201):
        sys.exit("Falló la subida del archivo: HTTP %s" % r.status_code)
    estado = "?"
    for _ in range(30):
        time.sleep(5)
        estado = _post("/post/publish/status/fetch/", {"publish_id": d["publish_id"]}).get("status", "?")
        if estado in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX", "FAILED"):
            break
    pub[eid] = {"publish_id": d["publish_id"], "privacidad": privacidad, "estado": estado, "file": f.name,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}
    EVID.mkdir(parents=True, exist_ok=True)
    LOG.write_text(json.dumps(pub, ensure_ascii=False, indent=1), encoding="utf-8")
    print("SUBIDO", eid, "| privacidad:", privacidad, "| estado:", estado)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("accion", choices=["autorizar", "info", "subir"])
    ap.add_argument("--id"); ap.add_argument("--file"); ap.add_argument("--titulo", default="")
    ap.add_argument("--borrador", action="store_true")
    a = ap.parse_args()
    if a.accion == "autorizar":
        autorizar()
    elif a.accion == "info":
        info()
    else:
        subir(a.id, a.file, a.titulo, a.borrador)
