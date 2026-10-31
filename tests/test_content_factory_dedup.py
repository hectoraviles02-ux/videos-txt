# -*- coding: utf-8 -*-
"""Deduplicacion y consolidacion en temas. Tarea 3.12 del bloque 3.

Cubre RQ-13 (consolidacion), RQ-14 (llave natural sha256 completo), RQ-15
(idempotencia) y RQ-37 (orden estable, cierre 5 de no-determinismo).
"""

from __future__ import annotations

from datetime import date

from content_factory.db import conectar, crear_esquema, insertar_senales
from content_factory.ingestion.base import construir, hash_senal
from content_factory.topics.discovery import (
    consolidar,
    deduplicar,
    hash_entradas,
    hash_titulo,
)

FECHA = date(2026, 9, 16)


def senal(fuente: str, texto: str, url: str | None = None, publicada: str | None = None):
    return construir(fuente, texto, url=url, publicada_en=publicada, fecha_corte=FECHA)


# ─── RQ-14 · la llave natural ───────────────────────────────────────────────

def test_hash_es_sha256_completo_nunca_recortado():
    """64 caracteres hexadecimales. `scripts.comun.calcular_hash` recorta a 12."""
    h = hash_senal("f", "https://ejemplo/a", "titulo", None)
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


def test_misma_url_con_distinto_utm_es_la_misma_senal():
    """El rastreo de campaña no cambia el contenido, y no debe duplicarlo."""
    a = senal("rss:x", "Titulo", "https://ejemplo.com/nota?utm_source=feed&utm_medium=rss")
    b = senal("rss:x", "Titulo", "https://ejemplo.com/nota?utm_campaign=otra")
    assert a.hash == b.hash
    assert len(deduplicar([a, b])) == 1


def test_el_mismo_enlace_visto_por_dos_fuentes_se_deduplica():
    """La preimagen con URL NO incluye la fuente: es la misma senal."""
    a = senal("hackernews:frontpage", "Ollama 0.14", "https://github.com/ollama/ollama/releases")
    b = senal("rss:xataka", "Otro titulo", "https://github.com/ollama/ollama/releases")
    assert a.hash == b.hash


def test_trends_sin_url_usa_fuente_titulo_fecha():
    """Sin URL, la preimagen es fuente|titulo normalizado|fecha."""
    a = senal("trends:mx", "Inter de Milán", None, "Mon, 14 Sep 2026")
    b = senal("trends:mx", "inter de milan", None, "Mon, 14 Sep 2026")
    assert a.hash == b.hash, "acentos y mayusculas no hacen dos senales"

    c = senal("trends:mx", "Inter de Milán", None, "Tue, 15 Sep 2026")
    assert a.hash != c.hash, "otro dia si es otra senal"

    d = senal("otra:fuente", "Inter de Milán", None, "Mon, 14 Sep 2026")
    assert a.hash != d.hash, "sin URL, la fuente si distingue"


# ─── RQ-13 · consolidacion en temas ─────────────────────────────────────────

def test_tres_senales_del_mismo_hecho_dan_un_solo_tema():
    """Tres URL distintas que cuentan lo mismo no pueden llenar tres huecos."""
    senales = [
        senal("hackernews:frontpage", "Ollama 0.14 adds structured outputs", "https://a/1"),
        senal("github:ollama/ollama", "Ollama 0.14 adds structured outputs", "https://b/2"),
        senal("rss:xataka", "ollama 0.14 adds structured outputs!", "https://c/3"),
    ]
    temas = consolidar(senales, FECHA)
    assert len(temas) == 1
    assert temas[0].n_senales == 3
    assert temas[0].fuentes == ("github:ollama/ollama", "hackernews:frontpage", "rss:xataka")
    assert len(temas[0].urls) == 3


def test_hash_titulo_ignora_acentos_puntuacion_y_mayusculas():
    assert hash_titulo("¿Cómo correr un modelo LOCAL?") == hash_titulo("como correr un modelo local")
    assert len(hash_titulo("x")) == 64


def test_temas_distintos_no_se_funden():
    senales = [
        senal("a", "Ollama 0.14 sale hoy", "https://a/1"),
        senal("b", "Reforma fiscal 2027 en Mexico", "https://b/2"),
    ]
    assert len(consolidar(senales, FECHA)) == 2


# ─── RQ-37 · orden estable, pase lo que pase ────────────────────────────────

def test_el_orden_de_llegada_de_las_fuentes_no_cambia_el_resultado():
    """Que Reddit conteste antes o despues que GitHub no puede alterar nada."""
    senales = [
        senal("reddit:r/x", "Tema A", "https://a/1"),
        senal("github:y/z", "Tema B", "https://b/2"),
        senal("rss:w", "Tema C", "https://c/3"),
    ]
    en_orden = [t.hash_titulo for t in consolidar(senales, FECHA)]
    al_reves = [t.hash_titulo for t in consolidar(list(reversed(senales)), FECHA)]
    revuelto = [t.hash_titulo for t in consolidar([senales[1], senales[2], senales[0]], FECHA)]
    assert en_orden == al_reves == revuelto
    assert en_orden == sorted(en_orden), "los temas salen por hash_titulo ASC"


def test_el_titulo_conservado_no_depende_del_orden_de_llegada():
    """Se conserva el de menor hash, no el primero que llego."""
    a = senal("f1", "Ollama 0.14 sale hoy", "https://a/1")
    b = senal("f2", "ollama 0.14 SALE hoy", "https://b/2")
    uno = consolidar([a, b], FECHA)[0].titulo
    otro = consolidar([b, a], FECHA)[0].titulo
    assert uno == otro


def test_senales_dentro_del_tema_van_ordenadas_por_hash():
    senales = [senal("f", "Mismo tema", "https://ejemplo/%d" % i) for i in range(5)]
    tema = consolidar(senales, FECHA)[0]
    hashes = [s.hash for s in tema.senales]
    assert hashes == sorted(hashes)


def test_hash_entradas_no_depende_del_orden_ni_de_repeticiones():
    """Si dos corridas tienen el mismo hash_entradas y distinto TOP, hay defecto."""
    senales = [senal("f", "T%d" % i, "https://ejemplo/%d" % i) for i in range(4)]
    assert hash_entradas(senales) == hash_entradas(list(reversed(senales)))
    assert hash_entradas(senales) == hash_entradas(senales + [senales[0]])
    assert hash_entradas(senales) != hash_entradas(senales[:3])


# ─── RQ-15 · idempotencia contra la base ────────────────────────────────────

def test_reingerir_el_mismo_dia_no_duplica_filas(tmp_path):
    conn = conectar(tmp_path / "d.db")
    crear_esquema(conn)
    senales = [senal("rss:x", "Nota %d" % i, "https://ejemplo/%d" % i) for i in range(3)]
    filas = [
        {"hash": s.hash, "fuente": s.fuente, "texto": s.texto, "url": s.url,
         "publicada_en": s.publicada_en, "detectada_en": s.detectada_en,
         "extra": {}, "raw_path": "2026-09-16/rss_x.xml"}
        for s in senales
    ]
    assert insertar_senales(conn, filas) == 3
    assert insertar_senales(conn, filas) == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM senales").fetchone()["n"] == 3
    conn.close()


def test_lectura_de_senales_siempre_ordenada_por_hash(tmp_path):
    conn = conectar(tmp_path / "d.db")
    crear_esquema(conn)
    senales = [senal("f", "T%d" % i, "https://ejemplo/%d" % i) for i in range(6)]
    insertar_senales(conn, [
        {"hash": s.hash, "fuente": s.fuente, "texto": s.texto, "url": s.url,
         "publicada_en": None, "detectada_en": s.detectada_en, "extra": {},
         "raw_path": "x"} for s in senales])
    leidos = [f["hash"] for f in conn.execute("SELECT hash FROM senales ORDER BY hash ASC")]
    assert leidos == sorted(leidos)
    conn.close()
