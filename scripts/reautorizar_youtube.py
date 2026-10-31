# -*- coding: utf-8 -*-
"""Vuelve a autorizar el canal de YouTube cuando el token caducó o fue revocado (22-oct-2026).

Abre el navegador para que operador inicie sesión con la cuenta del canal y acepte el MISMO permiso de siempre
(solo subir videos). Respalda el token viejo antes de escribir el nuevo. Lo corre operador, no un agente:
requiere su contraseña. No imprime el token.

Uso:  python scripts/reautorizar_youtube.py
"""
import shutil
import time
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

D = Path(__file__).resolve().parent.parent / "docs/operations/evidencia/publicacion_youtube_20261005"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]   # el mismo de subir_youtube.py
token = D / "token.json"
if token.is_file():
    respaldo = D / ("token_vencido_%s.json" % time.strftime("%Y%m%d_%H%M"))
    shutil.copyfile(token, respaldo)
    print("token viejo respaldado en", respaldo)
creds = InstalledAppFlow.from_client_secrets_file(str(D / "client_secret.json"), SCOPES).run_local_server(port=0)
token.write_text(creds.to_json(), encoding="utf-8")
print("LISTO: YouTube autorizado de nuevo. Token guardado en", token)
