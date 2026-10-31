# -*- coding: utf-8 -*-
"""Scoring: las tres medidas, la ventana y el redondeo. Bloque 5.

**Candado del operador para este bloque:** el scoring implementado tiene que
corresponder EXACTAMENTE a las reglas de diseno, sin otra logica escondida. Por
eso hay pruebas de dos clases distintas:

1. Las que comprueban la **formula declarada** (RQ-20, RQ-21, RQ-22, RQ-29).
2. Las que fijan por escrito las **convenciones de V0** que el diseno no fijo
   con numero, para que no puedan cambiarse en silencio mas adelante:
   `relevancia = 0`, `historico = 0`, `novedad` por recencia y no por similitud,
   y el hecho de que solo dos de las seis fuentes aportan metrica de demanda.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

from content_factory.config import Pesos, cargar_config
from content_factory.db import crear_esquema, upsert_score
from content_factory.topics import scorer
from content_factory.topics.scorer import (
    DECLARACIONES_V0,
    DEMANDA_SIN_METRICA,
    Componentes,
    combinar,
    componentes_de,
    demanda_de,
    metrica_de_fuente,
    novedad_de,
    percentil_en_ventana,
    produccion_de,
    puntuar_tema,
    redondear,
    valores_para_persistir,
)

FECHA = date(2026, 9, 16)
PESOS = Pesos(w_dem=0.5, w_est=0.5, k_saturacion=1.0, decimales=6)


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    # Mismas dos lineas que `conectar`: sin `row_factory` el esquema ni siquiera
    # se crea, y sin la pragma las llaves foraneas no se validan.
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    crear_esquema(c)
    yield c
    c.close()


def _tema(conn, titulo: str, creado_en: str = str(FECHA)) -> int:
    cur = conn.execute(
        "INSERT INTO temas (hash_titulo, titulo, pilar, intent, creado_en, estado, "
        "metodo_clasificacion) VALUES (?,?,?,?,?,?,?)",
        ("h-" + titulo, titulo, "P2", "noticia", creado_en, "DESCUBIERTA", "lexico"),
    )
    conn.commit()
    return cur.lastrowid


def _senal(conn, tema_id, fuente, extra, *, url="https://ej.mx/1",
           publicada_en=None, detectada_en=str(FECHA), hash_=None):
    cur = conn.execute(
        "INSERT INTO senales (hash, fuente, texto, url, publicada_en, detectada_en, "
        "extra_json, raw_path) VALUES (?,?,?,?,?,?,?,?)",
        (hash_ or ("s-%s-%s" % (fuente, json.dumps(extra, sort_keys=True))),
         fuente, "texto", url, publicada_en, detectada_en,
         json.dumps(extra, sort_keys=True), "raw/x"),
    )
    conn.execute("INSERT INTO tema_senal (tema_id, senal_id) VALUES (?,?)",
                 (tema_id, cur.lastrowid))
    conn.commit()
    return cur.lastrowid


# ─── RQ-22 · percentil dentro de la ventana ─────────────────────────────────

def test_percentil_en_ventana():
    """Rango percentil de mediana, acotado a [0,1] y definido en los bordes."""
    poblacion = [60.0, 120.0, 300.0]
    assert percentil_en_ventana(60.0, poblacion) == pytest.approx(1 / 6)
    assert percentil_en_ventana(120.0, poblacion) == pytest.approx(0.5)
    assert percentil_en_ventana(300.0, poblacion) == pytest.approx(5 / 6)
    for v in poblacion:
        assert 0.0 <= percentil_en_ventana(v, poblacion) <= 1.0


def test_percentil_sin_dispersion_no_regala_demanda_maxima():
    """n=1 y poblacion toda igual -> 0.5, no 1.0.

    Con una sola observacion no hay con que comparar. Devolver 1.0 seria afirmar
    demanda maxima a partir de n=1, que es justo lo que prohibe P7 del framework.
    """
    assert percentil_en_ventana(500.0, [500.0]) == 0.5
    assert percentil_en_ventana(7.0, [7.0, 7.0, 7.0]) == 0.5
    assert percentil_en_ventana(1.0, []) == 0.0


def test_senal_maxima_de_su_fuente(conn):
    """RQ-22: de tres senales de Hacker News, la de 300 puntos saca el valor mas alto."""
    ids = []
    for puntos in (60, 120, 300):
        t = _tema(conn, "hn-%d" % puntos)
        _senal(conn, t, "hackernews", {"puntos": puntos}, hash_="hn-%d" % puntos)
        ids.append((puntos, t))
    valores = {p: demanda_de(conn, t, FECHA)[0] for p, t in ids}
    assert valores[300] > valores[120] > valores[60]
    assert all(0.0 <= v <= 1.0 for v in valores.values())


def test_la_ventana_usa_la_fecha_de_la_fuente_y_si_no_la_de_deteccion(conn):
    """RQ-13: `publicada_en = None` con `detectada_en = D` cae dentro de la ventana."""
    dentro = _tema(conn, "dentro")
    _senal(conn, dentro, "hackernews", {"puntos": 10}, publicada_en=None, hash_="d")
    fuera = _tema(conn, "fuera")
    _senal(conn, fuera, "hackernews", {"puntos": 999},
           publicada_en="2026-01-01", detectada_en="2026-01-01", hash_="f")

    assert demanda_de(conn, dentro, FECHA)[1] is True
    # La senal de enero queda fuera de los 30 dias: no aporta ni a su tema ni a
    # la poblacion contra la que se normaliza.
    assert demanda_de(conn, fuera, FECHA) == (DEMANDA_SIN_METRICA, False)
    assert scorer.poblacion_de_fuente(conn, "hackernews", FECHA) == [10.0]


def test_solo_dos_fuentes_traen_metrica_de_demanda():
    """Desvio declarado respecto de RQ-22, fijado por escrito.

    RQ-22 nombra cuatro fuentes con metrica. En los hechos la ingestion solo
    guarda un numero comparable en dos. Si alguien agrega la metrica que falta,
    esta prueba se cae y obliga a actualizar la declaracion del informe en vez
    de dejarla mintiendo.
    """
    assert metrica_de_fuente("hackernews") == "puntos"
    assert metrica_de_fuente("youtube:ia local") == "vistas"
    for sin_metrica in ("reddit:r/LocalLLaMA", "trends-mx", "github:ollama/ollama",
                        "rss:xataka-mexico"):
        assert metrica_de_fuente(sin_metrica) is None


def test_ausencia_de_metrica_no_es_demanda_cero(conn):
    """Un tema solo de Reddit recibe el valor NEUTRO, no cero.

    Candado del operador: el scoring no puede interpretar "no tengo votos" como
    "demanda = 0". Eso castigaria a Reddit, Trends, GitHub y los RSS por una
    carencia del instrumento y no del tema, y dejaria el TOP convertido en un
    ranking de Hacker News y YouTube.
    """
    marca, _f = cargar_config()
    t = _tema(conn, "tema de reddit")
    _senal(conn, t, "reddit:r/LocalLLaMA", {"sub": "LocalLLaMA"}, hash_="r1")

    demanda, hubo = demanda_de(conn, t, FECHA)
    assert hubo is False
    assert demanda == DEMANDA_SIN_METRICA
    assert demanda != 0.0, "ausencia de metrica se colapso a demanda cero"


def test_el_tema_sin_metrica_no_pierde_contra_el_medido_mas_bajo(conn):
    """La consecuencia que importa: el no medido no queda debajo del peor medido.

    Con `0.0` por ausencia, cualquier senal de Hacker News, por floja que fuera,
    le ganaba siempre a todo tema de Reddit. Esta prueba es la que se cae si
    alguien revierte la regla.
    """
    peor_hn = _tema(conn, "hn flojo")
    _senal(conn, peor_hn, "hackernews", {"puntos": 1}, hash_="hn-bajo")
    for i, p in enumerate((500, 900, 1200)):
        otro = _tema(conn, "hn alto %d" % p)
        _senal(conn, otro, "hackernews", {"puntos": p}, hash_="hn-alto-%d" % i)
    solo_reddit = _tema(conn, "solo reddit")
    _senal(conn, solo_reddit, "reddit:r/LocalLLaMA", {"sub": "x"}, hash_="rd-1")

    d_hn, _ = demanda_de(conn, peor_hn, FECHA)
    d_rd, _ = demanda_de(conn, solo_reddit, FECHA)
    assert d_hn < d_rd, "el tema no medido quedo por debajo del peor medido"


def test_la_ausencia_de_metrica_queda_anotada_en_eventos(conn):
    """El valor neutro es visible, no silencioso.

    Es el precedente "Existencia confundida con suficiencia": sin la anotacion,
    el selector heredaria el hueco sin enterarse y leeria un 0.5 asignado como
    si fuera un 0.5 medido.
    """
    marca, _f = cargar_config()
    t = _tema(conn, "tema de github")
    _senal(conn, t, "github:ollama/ollama", {"repo": "ollama/ollama"}, hash_="g1")

    puntuar_tema(conn, t, FECHA, marca, PESOS, "v0.1+prueba")
    detalles = [f[0] for f in conn.execute(
        "SELECT detalle FROM eventos WHERE pieza_id IS NULL").fetchall()]
    assert any("sin metrica de demanda" in d for d in detalles)
    assert any("NO es una medicion" in d for d in detalles)
    assert any("valor neutro" in d for d in DECLARACIONES_V0) or any(
        "NEUTRO" in d for d in DECLARACIONES_V0)


# ─── Componentes ────────────────────────────────────────────────────────────

def test_historico_cero_declarado(conn):
    """RQ-23: en V0 `historico` vale 0 para todos, y se declara en el informe."""
    marca, _f = cargar_config()
    t = _tema(conn, "cualquiera")
    _senal(conn, t, "hackernews", {"puntos": 5}, hash_="h1")
    c = componentes_de(t, conn, FECHA, marca)
    assert c.historico == 0.0
    assert any("historico = 0.0" in d for d in DECLARACIONES_V0)


def test_relevancia_cero_y_declarada_no_inventada(conn):
    """No hay objetivos en la capa 0: no se simula relevancia (RQ-23, regla 2).

    Tambien fija que `cuota_base` NO se usa aqui: `marca.yaml` la reserva para
    `pen_saturacion` (RQ-25) y es el numero disponible mas tentador.
    """
    marca, _f = cargar_config()
    t = _tema(conn, "otro")
    _senal(conn, t, "hackernews", {"puntos": 5}, hash_="h2")
    c = componentes_de(t, conn, FECHA, marca)
    assert c.relevancia == 0.0
    assert c.relevancia != marca.pilares["P2"].cuota_base
    assert any("relevancia = 0.0" in d for d in DECLARACIONES_V0)


def test_novedad_por_recencia_no_por_similitud():
    """`novedad` decae con la edad del tema; no es `1 - similitud`.

    Si fuera `1 - similitud` estaria contando dos veces lo mismo que
    `pen_repeticion` (RQ-24): sumando en `m_demanda` y restando en
    `m_diversidad`.
    """
    assert novedad_de(str(FECHA), FECHA) == 1.0
    assert novedad_de("2026-09-01", FECHA) == pytest.approx(1 - 15 / 30)
    assert novedad_de("2026-08-17", FECHA) == 0.0        # 30 dias justos
    assert novedad_de("2025-01-01", FECHA) == 0.0
    assert novedad_de("no es fecha", FECHA) == 0.0       # no se rellena
    assert any("recencia del tema" in d for d in DECLARACIONES_V0)


def test_produccion_mide_evidencia_disponible(conn):
    """Fraccion de senales con URL verificable. La otra mitad no se mide y se dice."""
    t = _tema(conn, "con evidencia")
    _senal(conn, t, "hackernews", {"puntos": 1}, url="https://ej.mx/a", hash_="p1")
    _senal(conn, t, "hackernews", {"puntos": 2}, url=None, hash_="p2")
    assert produccion_de(conn, t, FECHA) == 0.5

    vacio = _tema(conn, "sin senales")
    assert produccion_de(conn, vacio, FECHA) == 0.0


def test_componentes_en_rango(conn):
    """RQ-20: los cinco componentes acotados a [0,1] antes de componerse."""
    marca, _f = cargar_config()
    t = _tema(conn, "rango")
    _senal(conn, t, "hackernews", {"puntos": 42}, hash_="r-1")
    _senal(conn, t, "youtube:ia local", {"vistas": 99999}, hash_="r-2")
    c = componentes_de(t, conn, FECHA, marca)
    for nombre in ("demanda", "novedad", "relevancia", "produccion", "historico"):
        v = getattr(c, nombre)
        assert 0.0 <= v <= 1.0, "%s fuera de rango: %r" % (nombre, v)
    # dificultad = 1 - produccion (RQ-31) tambien queda en rango
    assert 0.0 <= 1 - c.produccion <= 1.0


def test_penalizaciones_fuera_de_rango_se_acotan_antes_de_componer():
    """Una penalizacion mal calculada no puede sacar a las medidas de su rango."""
    c = Componentes(demanda=2.0, novedad=-1.0, relevancia=0.0,
                    produccion=0.5, historico=0.0)
    m = combinar(c, pen_repeticion=5.0, pen_saturacion=-3.0, pesos=PESOS)
    assert m.m_demanda == pytest.approx((1.0 + 0.0) / 2)
    assert m.m_diversidad == pytest.approx(1.0)


# ─── RQ-21 · la formula ─────────────────────────────────────────────────────

def test_formula_es_exactamente_la_del_diseno():
    """m_demanda, m_estrategia, m_diversidad y final, sin nada mas en medio."""
    c = Componentes(demanda=0.8, novedad=0.6, relevancia=0.0,
                    produccion=0.9, historico=0.0)
    m = combinar(c, pen_repeticion=0.1, pen_saturacion=0.2, pesos=PESOS)
    assert m.m_demanda == pytest.approx((0.8 + 0.6) / 2)
    assert m.m_estrategia == pytest.approx((0.0 + 0.9 + 0.0) / 3)
    assert m.m_diversidad == pytest.approx(0.1 + 0.2)
    assert m.final == pytest.approx(0.5 * m.m_demanda + 0.5 * m.m_estrategia - 0.3)


def test_final_puede_ser_negativo_y_no_se_recorta():
    """`m_diversidad` llega a 2.0 y la parte positiva no pasa de 1.0.

    Recortar `final` a cero volveria empatados a dos temas malos distintos y
    mandaria la decision al desempate por hash. Se deja como dice la formula.
    """
    c = Componentes(demanda=0.0, novedad=0.0, relevancia=0.0,
                    produccion=0.0, historico=0.0)
    m = combinar(c, pen_repeticion=1.0, pen_saturacion=1.0, pesos=PESOS)
    assert m.final == pytest.approx(-2.0)


def test_cambiar_peso_cambia_final_de_forma_predecible():
    """RQ-21: pasar `w_dem` de 0.5 a 1.0 mueve `final` exactamente 0.5 * m_demanda."""
    c = Componentes(demanda=0.8, novedad=0.6, relevancia=0.0,
                    produccion=0.9, historico=0.0)
    base = combinar(c, 0.1, 0.2, PESOS)
    doble = combinar(c, 0.1, 0.2,
                     Pesos(w_dem=1.0, w_est=0.5, k_saturacion=1.0, decimales=6))
    assert doble.final - base.final == pytest.approx(0.5 * base.m_demanda)


# ─── AC-5 · tres columnas distintas ─────────────────────────────────────────

def test_tres_medidas_en_columnas_distintas(conn):
    """AC-5: `m_demanda`, `m_diversidad` y `m_estrategia` se persisten por separado."""
    marca, _f = cargar_config()
    t = _tema(conn, "tres medidas")
    _senal(conn, t, "hackernews", {"puntos": 300}, hash_="t-1")
    puntuar_tema(conn, t, FECHA, marca, PESOS, "v0.1+prueba",
                 pen_repeticion=0.1, pen_saturacion=0.2)

    fila = conn.execute(
        "SELECT m_demanda, m_diversidad, m_estrategia, final, version_reglas, "
        "demanda, novedad, relevancia, produccion, historico, "
        "pen_repeticion, pen_saturacion FROM scores WHERE tema_id = ?", (t,)
    ).fetchone()
    assert fila is not None
    m_dem, m_div, m_est, final, version = fila[0], fila[1], fila[2], fila[3], fila[4]
    assert m_div == pytest.approx(0.3)
    assert final == pytest.approx(0.5 * m_dem + 0.5 * m_est - m_div, abs=1e-6)
    assert version == "v0.1+prueba"
    assert len({"m_demanda", "m_diversidad", "m_estrategia"}) == 3


# ─── RQ-29 / D-05 · redondeo AL PERSISTIR ───────────────────────────────────

def test_redondeo_a_6_decimales_al_persistir(conn):
    """D-05: lo que compara `ORDER BY` es lo mismo que quedo en disco."""
    t = _tema(conn, "redondeo")
    upsert_score(conn, t, str(FECHA),
                 {"final": 0.1234567891, "m_demanda": 0.9999999},
                 "v0.1+prueba", 6)
    final, m_dem = conn.execute(
        "SELECT final, m_demanda FROM scores WHERE tema_id = ?", (t,)).fetchone()
    assert final == 0.123457
    assert m_dem == 1.0
    assert redondear(0.1234567891, 6) == 0.123457


def test_diferencia_bajo_el_redondeo(conn):
    """RQ-28/RQ-29: si difieren por debajo del ultimo decimal, son empate.

    El desempate lo resuelve `hash_titulo ASC` (D-04), no el residuo flotante.
    """
    a = _tema(conn, "aaa")
    b = _tema(conn, "bbb")
    upsert_score(conn, a, str(FECHA), {"final": 0.5000001}, "v0.1+prueba", 6)
    upsert_score(conn, b, str(FECHA), {"final": 0.50000014}, "v0.1+prueba", 6)
    finales = [f[0] for f in conn.execute(
        "SELECT final FROM scores WHERE fecha = ?", (str(FECHA),)).fetchall()]
    assert finales[0] == finales[1] == 0.5

    orden = [f[0] for f in conn.execute(
        "SELECT t.titulo FROM scores s JOIN temas t ON t.id = s.tema_id "
        "WHERE s.fecha = ? ORDER BY s.final DESC, t.hash_titulo ASC",
        (str(FECHA),)).fetchall()]
    assert orden == ["aaa", "bbb"]


# ─── Determinismo y honestidad del arranque ─────────────────────────────────

def test_dos_corridas_dan_el_mismo_puntaje(conn):
    """Sin LLM y sin reloj: recalcular el mismo dia da la misma fila."""
    marca, _f = cargar_config()
    t = _tema(conn, "determinista")
    _senal(conn, t, "hackernews", {"puntos": 120}, hash_="det-1")
    _senal(conn, t, "youtube:ia local", {"vistas": 5000}, hash_="det-2")
    primero = combinar(componentes_de(t, conn, FECHA, marca), 0.0, 0.0, PESOS)
    for _ in range(5):
        assert combinar(componentes_de(t, conn, FECHA, marca), 0.0, 0.0, PESOS) == primero


def test_valores_para_persistir_trae_las_doce_columnas():
    c = Componentes(0.1, 0.2, 0.0, 0.3, 0.0)
    m = combinar(c, 0.05, 0.06, PESOS)
    v = valores_para_persistir(c, 0.05, 0.06, m)
    assert set(v) == {"demanda", "novedad", "relevancia", "produccion", "historico",
                      "pen_repeticion", "pen_saturacion", "m_demanda", "m_diversidad",
                      "m_estrategia", "final"}
    assert all(isinstance(x, float) for x in v.values())


def test_el_informe_tiene_que_declarar_lo_que_no_discrimina():
    """RQ-20/RQ-23: no se puede presentar solo `final` como si midiera tres cosas."""
    assert len(DECLARACIONES_V0) >= 5
    texto = " ".join(DECLARACIONES_V0)
    for obligatorio in ("historico", "relevancia", "novedad", "Hacker News"):
        assert obligatorio in texto
