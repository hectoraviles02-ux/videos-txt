# -*- coding: utf-8 -*-
"""Ingestion de las seis fuentes. Bloque 3 del topic engine.

**Ninguna prueba toca la red.** Todas sustituyen el punto de salida del modulo
(`_get`, o `_buscar` en YouTube) por una funcion que devuelve una fixture o
levanta. Si alguna vez una de estas pruebas necesitara Internet, seria un
defecto de diseno del modulo, no de la prueba.

Cubre RQ-07 (fecha de corte, nunca el reloj), RQ-08 (punto unico de red),
RQ-09 (una fuente que falla no aborta la corrida), RQ-11 y RQ-12 (limites
externos) y RQ-41 (senal sin texto no existe).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from content_factory import raw
from content_factory.config import FuenteConfig
from content_factory.http import Respuesta
from content_factory.ingestion import (
    CATEGORIA_CON_RESULTADOS,
    CATEGORIA_FALLIDA,
    CATEGORIA_NO_CONSULTADA,
    CATEGORIA_SIN_RESULTADOS,
    EtagStore,
    ResultadoFuente,
    estado_corrida,
)
from content_factory.ingestion import github_releases, hackernews, reddit, rss, trends_mx, youtube

CRUDOS = Path(__file__).parent / "fixtures" / "content_factory" / "crudo"
FECHA = date(2026, 9, 16)


def fixture(nombre: str) -> str:
    return (CRUDOS / nombre).read_text(encoding="utf-8")


def ok(cuerpo: str, **cabeceras: str) -> Respuesta:
    return Respuesta(status=200, texto=cuerpo, cabeceras=cabeceras)


def cfg(nombre: str, tipo: str, **parametros: str) -> FuenteConfig:
    return FuenteConfig(nombre=nombre, tipo=tipo, pilar_origen="P2",
                        parametros=parametros, activa=True)


# ─── Trends MX ──────────────────────────────────────────────────────────────

def test_trends_parsea_y_no_inventa_url(tmp_path, monkeypatch):
    c = cfg("trends:mx", "trends_mx", url="https://ejemplo/trends")
    monkeypatch.setattr(trends_mx, "_get", lambda u, **k: ok(fixture("trends_mx.xml")))
    senales, res = trends_mx.recolectar(c, FECHA, base_raw=tmp_path)
    assert res.resultado == "ok" and res.n_senales == len(senales) > 0
    assert all(s.url is None for s in senales), "el link de Trends apunta al buscador"
    assert all(s.detectada_en == "2026-09-16" for s in senales), "RQ-07: fecha de corte"


# ─── Reddit ─────────────────────────────────────────────────────────────────

def test_reddit_parsea_atom(tmp_path, monkeypatch):
    c = cfg("reddit:r/LocalLLaMA", "reddit", sub="LocalLLaMA")
    monkeypatch.setattr(reddit, "_get", lambda u, **k: ok(fixture("reddit_r_LocalLLaMA.xml")))
    senales, res = reddit.recolectar(c, FECHA, base_raw=tmp_path, dormir=lambda s: None)
    assert res.resultado == "ok" and len(senales) > 0
    assert all(s.url and "reddit.com" in s.url for s in senales)
    assert res.categoria == CATEGORIA_CON_RESULTADOS


def test_reddit_respeta_el_reset_medido(tmp_path, monkeypatch):
    """La espera sale de `x-ratelimit-reset`, no de un valor fijo inventado."""
    dormido: list[float] = []
    c = cfg("reddit:r/x", "reddit", sub="x")
    monkeypatch.setattr(
        reddit, "_get",
        lambda u, **k: ok(fixture("reddit_r_LocalLLaMA.xml"), **{"x-ratelimit-reset": "55"}),
    )
    reddit.recolectar(c, FECHA, base_raw=tmp_path, dormir=dormido.append)
    assert dormido == [55]


def test_reddit_429_no_reintenta_dentro_de_la_ventana(tmp_path, monkeypatch):
    """Un 429 se anota y se pasa al siguiente; insistir alarga el bloqueo."""
    llamadas: list[str] = []

    def _falso(url, **k):
        llamadas.append(url)
        return Respuesta(status=429, texto="", cabeceras={"x-ratelimit-reset": "55"})

    c = cfg("reddit:r/x", "reddit", sub="x")
    monkeypatch.setattr(reddit, "_get", _falso)
    senales, res = reddit.recolectar(c, FECHA, base_raw=tmp_path, dormir=lambda s: None)
    assert len(llamadas) == 1, "no debe reintentar dentro de la ventana"
    assert senales == [] and res.resultado == "http_429" and "reset=55" in res.detalle
    assert res.categoria == CATEGORIA_FALLIDA


def test_reddit_no_pide_ni_duerme_si_el_crudo_del_dia_existe(tmp_path, monkeypatch):
    """Reejecutar el dia es gratis: ni red ni espera."""
    c = cfg("reddit:r/LocalLLaMA", "reddit", sub="LocalLLaMA")
    raw.guardar_crudo(FECHA, c.nombre, "xml", fixture("reddit_r_LocalLLaMA.xml"), tmp_path)
    dormido: list[float] = []

    def _no_llamar(url, **k):
        raise AssertionError("no debio salir a la red")

    monkeypatch.setattr(reddit, "_get", _no_llamar)
    senales, res = reddit.recolectar(c, FECHA, base_raw=tmp_path, dormir=dormido.append)
    assert res.resultado == "desde_raw" and len(senales) > 0
    assert dormido == []
    assert res.categoria == CATEGORIA_CON_RESULTADOS, "servido de cache SI aporta senales"


# ─── Hacker News ────────────────────────────────────────────────────────────

def test_hackernews_parsea_hits(tmp_path, monkeypatch):
    c = cfg("hackernews:frontpage", "hackernews", url="https://ejemplo/hn", tags="story", hits="50")
    monkeypatch.setattr(hackernews, "_get", lambda u, **k: ok(fixture("hackernews.json")))
    senales, res = hackernews.recolectar(c, FECHA, base_raw=tmp_path)
    assert res.resultado == "ok" and len(senales) > 0
    assert any(s.extra_json and "puntos" in s.extra_json for s in senales)


def test_hackernews_url_lleva_rango_derivado_de_la_fecha_de_corte():
    """El rango sale de `fecha_corte`, no del reloj: eso permite rellenar dias."""
    c = cfg("hackernews:frontpage", "hackernews", url="https://ejemplo/hn")
    url_a = hackernews.url_de(c, date(2026, 9, 16))
    url_b = hackernews.url_de(c, date(2026, 8, 1))
    assert "created_at_i" in url_a
    assert url_a != url_b, "dos fechas de corte distintas deben pedir ventanas distintas"
    assert hackernews.url_de(c, date(2026, 9, 16)) == url_a, "misma fecha, misma URL"


# ─── GitHub ─────────────────────────────────────────────────────────────────

def test_github_parsea_y_descarta_borradores(tmp_path, monkeypatch):
    c = cfg("github:ollama/ollama", "github_releases", repo="ollama/ollama")
    monkeypatch.setattr(
        github_releases, "_get",
        lambda u, **k: ok(fixture("github_ollama_ollama.json"), etag='W/"abc"'),
    )
    senales, res = github_releases.recolectar(c, FECHA, base_raw=tmp_path, etags=EtagStore())
    assert res.resultado == "ok" and len(senales) > 0
    assert all("ollama/ollama" in s.texto for s in senales), "el tag solo no dice nada"


def test_github_guarda_etag_y_el_304_sirve_de_cache(tmp_path, monkeypatch):
    """Un 304 no gasta cuota y SI aporta senales: es resultado, no falla."""
    c = cfg("github:ollama/ollama", "github_releases", repo="ollama/ollama")
    tienda = EtagStore()
    monkeypatch.setattr(
        github_releases, "_get",
        lambda u, **k: ok(fixture("github_ollama_ollama.json"), etag='W/"abc"'),
    )
    github_releases.recolectar(c, FECHA, base_raw=tmp_path, etags=tienda)
    assert tienda.leer(github_releases.url_de(c)) is not None, "el ETag debe sobrevivir"

    enviados: list[str | None] = []

    def _con_304(url, *, etag=None, **k):
        enviados.append(etag)
        return Respuesta(status=304, texto=None, cabeceras={})

    monkeypatch.setattr(github_releases, "_get", _con_304)
    otra = date(2026, 9, 17)
    senales, res = github_releases.recolectar(c, otra, base_raw=tmp_path, etags=tienda)
    assert enviados == ['W/"abc"'], "debe mandar If-None-Match con el ETag guardado"
    assert res.resultado == "http_304" and len(senales) > 0
    assert res.categoria == CATEGORIA_CON_RESULTADOS


def test_github_corta_por_cuota_baja_sin_esperar(tmp_path, monkeypatch):
    """Con la cuota casi agotada no se pregunta, y la corrida sigue."""
    c = cfg("github:otro/repo", "github_releases", repo="otro/repo")
    tienda = EtagStore()
    tienda.remaining = 2

    def _no_llamar(url, **k):
        raise AssertionError("no debio consumir cuota")

    monkeypatch.setattr(github_releases, "_get", _no_llamar)
    senales, res = github_releases.recolectar(c, FECHA, base_raw=tmp_path, etags=tienda)
    assert senales == [] and res.resultado == "omitida_por_cuota"
    assert res.categoria == CATEGORIA_NO_CONSULTADA, "no preguntar no es fallar"


# ─── RSS y Atom ─────────────────────────────────────────────────────────────

def test_rss_parsea_rss20(tmp_path, monkeypatch):
    c = cfg("rss:xataka", "rss", url="https://ejemplo/rss")
    monkeypatch.setattr(rss, "_get", lambda u, **k: ok(fixture("rss_generico.xml")))
    senales, res = rss.recolectar(c, FECHA, base_raw=tmp_path)
    assert res.resultado == "ok" and len(senales) > 0
    assert all(s.url for s in senales)


def test_rss_tambien_lee_atom(tmp_path, monkeypatch):
    """El mismo parser atiende Atom: difieren tres etiquetas, no la logica."""
    c = cfg("rss:atom", "rss", url="https://ejemplo/atom")
    monkeypatch.setattr(rss, "_get", lambda u, **k: ok(fixture("reddit_r_LocalLLaMA.xml")))
    senales, res = rss.recolectar(c, FECHA, base_raw=tmp_path)
    assert res.resultado == "ok" and len(senales) > 0
    assert all(s.url for s in senales), "en Atom la URL esta en el atributo href"


# ─── YouTube ────────────────────────────────────────────────────────────────

def test_youtube_parsea_tabulado_y_no_inventa_fecha(tmp_path, monkeypatch):
    c = cfg("youtube:ejemplo", "youtube", consulta="ia local", sp="EgIIBQ%3D%3D", limite="10")
    monkeypatch.setattr(youtube, "_buscar", lambda cfg_: (fixture("youtube_sp_mes.txt"), None))
    senales, res = youtube.recolectar(c, FECHA, base_raw=tmp_path)
    assert res.resultado == "ok" and len(senales) == 3
    assert all(s.publicada_en is None for s in senales), "upload_date viene NA en modo plano"
    assert all("watch?v=" in (s.url or "") for s in senales)


def test_youtube_url_lleva_el_filtro_sp_no_ytsearchdate():
    """`ytsearchdate` no existe: el filtro por fecha va en la URL."""
    c = cfg("youtube:ejemplo", "youtube", consulta="ia local", sp="EgIIBQ%3D%3D")
    url = youtube.url_de(c)
    assert "sp=EgIIBQ%3D%3D" in url and "ytsearchdate" not in url


def test_youtube_fallo_de_la_herramienta_no_aborta(tmp_path, monkeypatch):
    c = cfg("youtube:ejemplo", "youtube", consulta="x")
    monkeypatch.setattr(youtube, "_buscar", lambda cfg_: (None, "yt-dlp salio con 1"))
    senales, res = youtube.recolectar(c, FECHA, base_raw=tmp_path)
    assert senales == [] and res.resultado == "error_red"


# ─── RQ-09 · una fuente que falla nunca aborta la corrida ───────────────────

@pytest.mark.parametrize(
    "status,esperado",
    [(429, "http_429"), (403, "http_403"), (500, "error_red"), (0, "error_red")],
)
def test_ninguna_respuesta_mala_levanta(tmp_path, monkeypatch, status, esperado):
    c = cfg("rss:x", "rss", url="https://ejemplo/rss")
    monkeypatch.setattr(
        rss, "_get",
        lambda u, **k: Respuesta(status=status, texto=None, cabeceras={}, error="fallo"),
    )
    senales, res = rss.recolectar(c, FECHA, base_raw=tmp_path)
    assert senales == [] and res.resultado == esperado


def test_crudo_malformado_es_parse_error_no_error_de_red(tmp_path, monkeypatch):
    """La red funciono; lo que vino roto fue el documento. No son lo mismo."""
    c = cfg("rss:x", "rss", url="https://ejemplo/rss")
    monkeypatch.setattr(rss, "_get", lambda u, **k: ok(fixture("malformado.xml")))
    senales, res = rss.recolectar(c, FECHA, base_raw=tmp_path)
    assert senales == [] and res.resultado == "parse_error"
    assert res.categoria == CATEGORIA_FALLIDA


def test_crudo_vacio_es_vacia_no_fallida(tmp_path, monkeypatch):
    """Preguntar y que no haya nada es un estado sano, no una falla."""
    c = cfg("rss:x", "rss", url="https://ejemplo/rss")
    monkeypatch.setattr(rss, "_get", lambda u, **k: ok(fixture("vacio.xml")))
    senales, res = rss.recolectar(c, FECHA, base_raw=tmp_path)
    assert senales == [] and res.resultado == "vacia"
    assert res.categoria == CATEGORIA_SIN_RESULTADOS


# ─── Las cinco categorias auditables ────────────────────────────────────────

def test_las_cinco_categorias_son_distinguibles():
    """Los cinco casos que pidió el operador, separados y sin solaparse."""
    casos = {
        "ok": CATEGORIA_CON_RESULTADOS,
        "desde_raw": CATEGORIA_CON_RESULTADOS,
        "http_304": CATEGORIA_CON_RESULTADOS,
        "vacia": CATEGORIA_SIN_RESULTADOS,
        "http_429": CATEGORIA_FALLIDA,
        "parse_error": CATEGORIA_FALLIDA,
        "desactivada": CATEGORIA_NO_CONSULTADA,
        "omitida_por_cuota": CATEGORIA_NO_CONSULTADA,
    }
    for resultado, categoria in casos.items():
        assert ResultadoFuente("f", resultado).categoria == categoria


def test_quinta_categoria_la_corrida_incompleta():
    """Solo una fuente FALLIDA vuelve incompleta la corrida."""
    sanas = [ResultadoFuente("a", "ok", 3), ResultadoFuente("b", "vacia"),
             ResultadoFuente("c", "desactivada")]
    assert estado_corrida(sanas) == "completa", "vacia y no consultada son estados sanos"
    assert estado_corrida(sanas + [ResultadoFuente("d", "http_429")]) == "incompleta"


def test_resultado_fuera_de_la_taxonomia_se_rechaza():
    with pytest.raises(ValueError):
        ResultadoFuente("f", "mas_o_menos")
