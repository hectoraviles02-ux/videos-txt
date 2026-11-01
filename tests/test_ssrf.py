"""31-oct-2026 (auditoría P1-SSRF): la extracción no debe llegar a la red interna.

Antes: verificar_url, procesar_texto, investigar_web y yt-dlp llegaban a 127.0.0.1 y seguían un 302 hacia otro
servicio interno; el texto interno quedaba en procesado/ como EXTRAIDO. Ahora todo pasa por
comun.destino_seguro / comun.obtener_seguro. Pruebas con un servidor propio en 127.0.0.1 (contamos si recibe
algo) y con respuestas simuladas para las redirecciones. No se toca ningún servicio real.
"""
from __future__ import annotations

import http.server
import socket
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import comun  # noqa: E402


@pytest.fixture
def servidor_interno():
    """Servidor en 127.0.0.1 que cuenta las peticiones que recibe."""
    recibidas = []

    class H(http.server.BaseHTTPRequestHandler):
        def _ok(self):
            recibidas.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(b"<html><title>SECRETO</title><body><p>texto interno</p></body></html>")
        do_GET = do_HEAD = _ok

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    hilo = threading.Thread(target=srv.serve_forever, daemon=True)
    hilo.start()
    yield srv.server_address[1], recibidas
    srv.shutdown()


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_verificar_url_no_llega_a_la_red_interna(servidor_interno, host):
    puerto, recibidas = servidor_interno
    v = comun.verificar_url("http://%s:%d/" % (host, puerto))
    assert v["resuelve"] is False and "bloqueada" in v["razon"]
    assert recibidas == []


@pytest.mark.parametrize("url", ["file:///C:/Windows/win.ini", "ftp://example.com/x", "http://10.0.0.1/",
                                 "http://192.168.1.1/admin", "http://169.254.169.254/latest/meta-data",
                                 "http://[::1]/"])
def test_destinos_no_publicos_o_esquemas_raros_se_rechazan(url):
    ok, motivo = comun.destino_seguro(url)
    assert ok is False and motivo


def test_procesar_texto_no_extrae_texto_interno(servidor_interno, tmp_path):
    import procesar_texto
    puerto, recibidas = servidor_interno
    with pytest.raises(comun.DestinoNoPermitido):
        procesar_texto.procesar("http://127.0.0.1:%d/" % puerto, tmp_path, tmp_path)
    assert recibidas == [] and not list(tmp_path.glob("*.json"))


def test_procesar_video_no_manda_yt_dlp_a_la_red_interna(servidor_interno, tmp_path):
    import procesar_video
    puerto, recibidas = servidor_interno
    with pytest.raises(comun.DestinoNoPermitido):
        procesar_video.procesar("http://127.0.0.1:%d/v.mp4" % puerto, tmp_path, tmp_path)
    assert recibidas == []


class _Resp:
    def __init__(self, status, location=None, texto="ok"):
        self.status_code, self.text = status, texto
        self.headers = {"location": location} if location else {}


def _dns_falso(monkeypatch):
    """example.com resuelve a una IP pública de documentación y cualquier otro host a 127.0.0.1."""
    real = socket.getaddrinfo

    def falso(host, puerto, *a, **k):
        if host == "publico.example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", puerto))]
        if host in ("127.0.0.1", "localhost"):
            return real(host, puerto, *a, **k)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", puerto))]
    monkeypatch.setattr(socket, "getaddrinfo", falso)


def test_redireccion_de_publico_a_interno_se_corta_en_el_salto(monkeypatch):
    import requests
    _dns_falso(monkeypatch)
    pedidas = []

    def falso_request(metodo, url, **kw):
        pedidas.append(url)
        assert kw.get("allow_redirects") is False      # las redirecciones nunca las sigue requests solo
        return _Resp(302, location="http://127.0.0.1:8080/admin")
    monkeypatch.setattr(requests, "request", falso_request)
    with pytest.raises(comun.DestinoNoPermitido):
        comun.obtener_seguro("http://publico.example.com/articulo")
    assert pedidas == ["http://publico.example.com/articulo"]   # el salto interno ni se intentó


def test_redireccion_entre_destinos_publicos_si_funciona(monkeypatch):
    import requests
    _dns_falso(monkeypatch)
    respuestas = iter([_Resp(301, location="/nuevo"), _Resp(200, texto="contenido")])
    monkeypatch.setattr(requests, "request", lambda m, u, **kw: next(respuestas))
    r = comun.obtener_seguro("http://publico.example.com/viejo")
    assert r.text == "contenido" and r.url_final == "http://publico.example.com/nuevo"


def test_demasiadas_redirecciones_se_cortan(monkeypatch):
    import requests
    _dns_falso(monkeypatch)
    monkeypatch.setattr(requests, "request", lambda m, u, **kw: _Resp(302, location="/otra"))
    with pytest.raises(comun.DestinoNoPermitido):
        comun.obtener_seguro("http://publico.example.com/bucle", max_saltos=3)
