# -*- coding: utf-8 -*-
"""Diversidad: las dos penalizaciones. Bloque 6.

**Ninguna prueba de este archivo carga MiniLM.** Donde hace falta un modelo se
inyecta un cargador de mentira con vectores fijos, y en varias se inyecta uno
que LANZA: asi la afirmacion "no se carga el modelo" queda demostrada por el
arbol de pruebas y no por una promesa del docstring.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from datetime import date

import pytest

from content_factory.config import cargar_config
from content_factory.db import crear_esquema
from content_factory.topics.diversity import (
    ESTADOS_DE_LA_VENTANA,
    MODELO,
    MODELO_CANDIDATO,
    asegurar_embeddings,
    coseno,
    desempaquetar,
    embedding_de,
    empaquetar,
    pen_repeticion,
    pen_saturacion,
    share_de_ventana,
    share_por_pilar,
)

FECHA = date(2026, 9, 16)


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    crear_esquema(c)
    yield c
    c.close()


class ModeloFalso:
    """Devuelve un vector fijo por titulo. Nunca toca la red ni torch."""

    def __init__(self, tabla):
        self.tabla = tabla
        self.llamadas = 0

    def encode(self, texto):
        self.llamadas += 1
        return self.tabla[texto]


def cargador_que_lanza():
    raise AssertionError("se intento cargar MiniLM y no debia")


def _tema(conn, titulo, pilar="P2", creado_en=str(FECHA), embedding=None):
    cur = conn.execute(
        "INSERT INTO temas (hash_titulo, titulo, pilar, intent, creado_en, estado, "
        "metodo_clasificacion, embedding) VALUES (?,?,?,?,?,?,?,?)",
        ("h-" + titulo, titulo, pilar, "noticia", creado_en, "DESCUBIERTA",
         "lexico", embedding),
    )
    conn.commit()
    return cur.lastrowid


def _pieza(conn, tema_id, creada_en, estado="SELECCIONADA", formato="corto"):
    cur = conn.execute(
        "INSERT INTO piezas (tema_id, formato, angulo, angulo_metodo, estado, creada_en, "
        " pilar_decision) VALUES (?,?,?,?,?,?,?)",
        # El pilar decisorio se toma del tema AL CREAR la pieza: es lo que hace
        # produccion. Despues el tema puede cambiar y la pieza no le sigue.
        (tema_id, formato, "angulo", "plantilla", estado, creada_en,
         conn.execute("SELECT pilar FROM temas WHERE id = ?", (tema_id,)).fetchone()[0]),
    )
    conn.commit()
    return cur.lastrowid


# ─── RQ-24 · el paquete no puede arrastrar torch ────────────────────────────

def test_paquete_no_arrastra_torch():
    """Importar `content_factory` NO debe cargar torch (D-15).

    Se comprueba en un interprete NUEVO, no en este: aqui torch ya podria estar
    cargado por otra prueba, y entonces la comprobacion no mediria nada. Es la
    diferencia entre medir la propiedad y medir el estado del proceso.
    """
    codigo = (
        "import sys; import content_factory; import content_factory.topics.diversity; "
        "malos = [m for m in ('torch', 'sentence_transformers') if m in sys.modules]; "
        "print(','.join(malos))"
    )
    r = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", "el paquete cargo: " + r.stdout.strip()


def test_los_imports_pesados_viven_dentro_de_cargar_modelo():
    """El import perezoso es la regla, no un detalle: queda fijado por escrito."""
    import inspect

    from content_factory.topics import diversity

    fuente = inspect.getsource(diversity)
    cabecera = fuente.split("def cargar_modelo")[0]
    assert "import torch" not in cabecera
    assert "from sentence_transformers" not in cabecera
    assert "from sentence_transformers" in inspect.getsource(diversity.cargar_modelo)
    assert "cpu" in inspect.getsource(diversity.cargar_modelo)


def test_el_modelo_por_omision_es_el_que_esta_en_disco():
    """La corrida nocturna es desatendida: no puede estrenarse descargando.

    Se elige la variante que YA esta en la maquina y cuyo costo de carga esta
    medido. La deuda queda abierta y nombrada, no escondida: los titulos son en
    espanol y esta variante esta entrenada en ingles.
    """
    assert MODELO == "sentence-transformers/all-MiniLM-L6-v2"
    assert "multilingual" in MODELO_CANDIDATO
    assert MODELO != MODELO_CANDIDATO


# ─── Empaquetado del embedding ──────────────────────────────────────────────

def test_empaquetar_y_desempaquetar_son_inversos():
    v = [0.5, -0.25, 0.125, 0.0]
    assert desempaquetar(empaquetar(v)) == pytest.approx(v)
    assert len(empaquetar(v)) == 16          # float32: 4 bytes por valor
    assert desempaquetar(b"") == []


def test_el_blob_no_depende_de_la_maquina():
    """Little-endian fijado a mano: el replay lee el mismo byte en otro equipo."""
    assert empaquetar([1.0]) == b"\x00\x00\x80\x3f"


# ─── Coseno ─────────────────────────────────────────────────────────────────

def test_coseno_casos_de_borde():
    assert coseno([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert coseno([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert coseno([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)
    # Vector nulo y tamanos distintos no revientan: dejan de aportar parecido.
    assert coseno([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert coseno([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0
    assert coseno([], [1.0]) == 0.0


# ─── RQ-24 · pen_repeticion ─────────────────────────────────────────────────

def test_pen_repeticion_con_embeddings_sustituidos(conn):
    """El casi duplicado pena mas que el tema ajeno, y MiniLM no se carga."""
    _tema(conn, "original", embedding=empaquetar([1.0, 0.0, 0.0]))
    casi = _tema(conn, "casi duplicado", embedding=empaquetar([0.98, 0.20, 0.0]))
    ajeno = _tema(conn, "tema ajeno", embedding=empaquetar([0.0, 0.0, 1.0]))

    p_casi = pen_repeticion(casi, conn, FECHA, cargador=cargador_que_lanza)
    p_ajeno = pen_repeticion(ajeno, conn, FECHA, cargador=cargador_que_lanza)

    assert p_casi > p_ajeno
    assert 0.0 <= p_ajeno <= p_casi <= 1.0


def test_pen_repeticion_nunca_es_negativa(conn):
    """Un coseno negativo se recorta: "menos que nada parecido" no es un premio."""
    _tema(conn, "uno", embedding=empaquetar([1.0, 0.0]))
    opuesto = _tema(conn, "opuesto", embedding=empaquetar([-1.0, 0.0]))
    assert pen_repeticion(opuesto, conn, FECHA, cargador=cargador_que_lanza) == 0.0


def test_tema_solo_en_la_base_no_se_penaliza(conn):
    """Sin con quien compararse, la penalizacion es cero, no un valor inventado."""
    unico = _tema(conn, "unico", embedding=empaquetar([1.0, 0.0]))
    assert pen_repeticion(unico, conn, FECHA, cargador=cargador_que_lanza) == 0.0


def test_solo_cuenta_lo_que_cae_en_la_ventana(conn):
    """Un tema identico de hace un ano no debe penalizar hoy."""
    viejo_identico = empaquetar([1.0, 0.0])
    _tema(conn, "identico viejo", creado_en="2025-01-05", embedding=viejo_identico)
    hoy = _tema(conn, "identico hoy", embedding=viejo_identico)
    assert pen_repeticion(hoy, conn, FECHA, cargador=cargador_que_lanza) == 0.0


def test_el_embedding_se_calcula_una_sola_vez_y_se_persiste(conn):
    """RQ-24: una vez por tema. La segunda llamada ya no invoca al cargador."""
    falso = ModeloFalso({"tema nuevo": [0.6, 0.8]})
    t = _tema(conn, "tema nuevo", embedding=None)

    pen_repeticion(t, conn, FECHA, cargador=lambda: falso)
    assert falso.llamadas == 1
    guardado = conn.execute("SELECT embedding FROM temas WHERE id = ?", (t,)).fetchone()[0]
    assert desempaquetar(guardado) == pytest.approx([0.6, 0.8])

    # Ya persistido: el cargador no se vuelve a invocar. Es lo que permite el
    # replay sin MiniLM, asi que se comprueba con uno que lanza.
    pen_repeticion(t, conn, FECHA, cargador=cargador_que_lanza)
    assert falso.llamadas == 1


def test_la_penalizacion_depende_del_orden_si_no_se_materializa_antes(conn):
    """El defecto que hace falta evitar, demostrado antes de la correccion.

    Si los embeddings se calculan al vuelo, el primer tema en puntuarse no tiene
    contra quien compararse y el ultimo se compara contra todos. La penalizacion
    acaba dependiendo del ORDEN del recorrido, que es justo lo que AC-1 prohibe.
    """
    vectores = {"alfa": [1.0, 0.0], "beta": [1.0, 0.0], "gama": [1.0, 0.0]}
    ids = {n: _tema(conn, n, embedding=None) for n in vectores}
    falso = ModeloFalso(vectores)

    # Sin materializar: el primero sale con 0.0 y el ultimo con 1.0, siendo
    # los tres el mismo vector. Eso es la dependencia del orden.
    primero = pen_repeticion(ids["alfa"], conn, FECHA, cargador=lambda: falso)
    ultimo = pen_repeticion(ids["gama"], conn, FECHA, cargador=lambda: falso)
    assert primero == 0.0 and ultimo == pytest.approx(1.0)


def test_materializar_antes_vuelve_la_penalizacion_independiente_del_orden(conn):
    """Con `asegurar_embeddings` primero, los tres temas iguales penan igual."""
    vectores = {"alfa": [1.0, 0.0], "beta": [1.0, 0.0], "gama": [1.0, 0.0]}
    ids = {n: _tema(conn, n, embedding=None) for n in vectores}
    falso = ModeloFalso(vectores)

    calculados = asegurar_embeddings(conn, FECHA, cargador=lambda: falso)
    assert calculados == 3

    # Ya materializados: el cargador no se vuelve a tocar y el resultado no
    # depende de por cual se empiece.
    valores = [pen_repeticion(ids[n], conn, FECHA, cargador=cargador_que_lanza)
               for n in ("gama", "alfa", "beta")]
    assert valores == pytest.approx([1.0, 1.0, 1.0])


def test_asegurar_embeddings_no_carga_el_modelo_si_no_falta_ninguno(conn):
    """Con todo persistido, el cargador no se invoca: es lo que permite el replay."""
    _tema(conn, "ya tiene", embedding=empaquetar([1.0, 0.0]))
    assert asegurar_embeddings(conn, FECHA, cargador=cargador_que_lanza) == 0


def test_asegurar_embeddings_recorre_en_orden_estable(conn):
    """Orden por `hash_titulo`: una interrupcion a la mitad deja el mismo estado."""
    vistos = []

    class Espia(ModeloFalso):
        def encode(self, texto):
            vistos.append(texto)
            return [1.0, 0.0]

    for n in ("zeta", "alfa", "media"):
        _tema(conn, n, embedding=None)
    asegurar_embeddings(conn, FECHA, cargador=lambda: Espia({}))
    assert vistos == sorted(vistos)


def test_embedding_de_usa_el_cargador_inyectado():
    falso = ModeloFalso({"hola": [1.0, 2.0]})
    assert desempaquetar(embedding_de("hola", lambda: falso)) == pytest.approx([1.0, 2.0])


# ─── RQ-25 · pen_saturacion ─────────────────────────────────────────────────

def test_pen_saturacion_proporcional_al_exceso():
    """`max(0, share - cuota) * k`, tal cual, sin nada mas en medio."""
    marca, _f = cargar_config()
    cuota = marca.pilares["P2"].cuota_base
    k = marca.pesos.k_saturacion
    assert pen_saturacion("P2", {"P2": 0.5}, marca) == pytest.approx((0.5 - cuota) * k)
    assert pen_saturacion("P2", {"P2": 0.3}, marca) == pytest.approx((0.3 - cuota) * k)


def test_pilar_bajo_la_cuota_sin_penalizacion():
    """Por debajo de la cuota se paga exactamente cero, no un poquito."""
    marca, _f = cargar_config()
    assert pen_saturacion("P2", {"P2": 0.1}, marca) == 0.0
    assert pen_saturacion("P2", {}, marca) == 0.0
    # Justo en la cuota tampoco hay exceso.
    assert pen_saturacion("P2", {"P2": marca.pilares["P2"].cuota_base}, marca) == 0.0


def test_pilar_que_rinde_no_es_bloqueado():
    """RQ-25: la cuota es tope DECLARADO, no mandato.

    Un pilar con 4 de las ultimas 10 piezas paga penalizacion, pero si su
    candidato trae el `final` mas alto conserva el primer lugar. La compuerta
    dura es otra cosa y vive en el selector.
    """
    marca, _f = cargar_config()
    pen = pen_saturacion("P2", {"P2": 0.4}, marca)
    assert pen > 0.0

    final_p2 = 0.90 - pen
    final_otro = 0.50
    assert final_p2 > final_otro, "la penalizacion bloqueo a un pilar que rinde"


def test_p7_no_recibe_compuerta_propia_por_su_cuota():
    """Cierre de hueco de RQ-25: P7 paga antes, pero no queda bloqueado antes."""
    marca, _f = cargar_config()
    assert marca.pilares["P7"].cuota_base < marca.pilares["P1"].cuota_base
    pen_p7 = pen_saturacion("P7", {"P7": 0.3}, marca)
    pen_p1 = pen_saturacion("P1", {"P1": 0.3}, marca)
    assert pen_p7 > pen_p1
    assert pen_p7 <= 1.0


def test_pilar_desconocido_no_paga(conn):
    """Sin cuota declarada no hay exceso que medir; no se fabrica politica."""
    marca, _f = cargar_config()
    assert pen_saturacion("P99", {"P99": 0.9}, marca) == 0.0


def test_pen_saturacion_acotada_a_uno():
    """RQ-20 pide las penalizaciones en [0,1] antes de componerse."""
    marca, _f = cargar_config()
    assert pen_saturacion("P7", {"P7": 1.0}, marca) <= 1.0


# ─── RQ-26 · la ventana de reparto, definicion unica ────────────────────────

def test_share_por_pilar_usa_las_diez_mas_recientes(conn):
    """La ventana son 10 piezas, no todo el historial."""
    for i in range(12):
        t = _tema(conn, "viejo %d" % i, pilar="P1")
        _pieza(conn, t, "2026-09-0%d" % (i % 9 + 1))
    recientes = []
    for i in range(10):
        t = _tema(conn, "reciente %d" % i, pilar="P2")
        recientes.append(_pieza(conn, t, "2026-09-16"))

    shares = share_por_pilar(conn, FECHA)
    assert shares.get("P2") == 1.0
    assert "P1" not in shares


def test_el_denominador_son_las_piezas_que_hay_no_diez(conn):
    """Con 4 piezas, un pilar con 2 tiene 50 %, no 20 %.

    Dividir siempre entre 10 haria que ningun pilar pareciera saturado durante
    las primeras nueve noches, justo cuando la base es facil de desbalancear.
    """
    for i in range(2):
        _pieza(conn, _tema(conn, "a%d" % i, pilar="P2"), "2026-09-16")
    for i in range(2):
        _pieza(conn, _tema(conn, "b%d" % i, pilar="P4"), "2026-09-16")
    shares = share_por_pilar(conn, FECHA)
    assert shares["P2"] == pytest.approx(0.5)
    assert shares["P4"] == pytest.approx(0.5)


def test_base_vacia_no_reparte_nada(conn):
    assert share_por_pilar(conn, FECHA) == {}


def test_solo_cuentan_los_estados_declarados(conn):
    """RQ-26: lista explicita de estados, nunca "o posterior".

    El orden de los estados no esta definido en ninguna parte, asi que
    "posterior" no se puede escribir como consulta.
    """
    assert ESTADOS_DE_LA_VENTANA == ("SELECCIONADA",)
    _pieza(conn, _tema(conn, "elegida", pilar="P2"), "2026-09-16", estado="SELECCIONADA")
    _pieza(conn, _tema(conn, "otra", pilar="P4"), "2026-09-16", estado="DESCUBIERTA")
    shares = share_por_pilar(conn, FECHA)
    assert shares == {"P2": 1.0}


def test_el_desempate_de_la_ventana_es_estable(conn):
    """Dos piezas de la misma fecha: gana la de id mayor, no el orden de lectura."""
    piezas = []
    for i in range(11):
        t = _tema(conn, "igual %d" % i, pilar="P2" if i else "P1")
        piezas.append(_pieza(conn, t, "2026-09-16"))
    primera = share_por_pilar(conn, FECHA)
    for _ in range(3):
        assert share_por_pilar(conn, FECHA) == primera
    # La mas antigua por id queda fuera de las 10: es la unica de P1.
    assert "P1" not in primera


# ─── 1b · corte temporal en el reparto por pilar ────────────────────────────
#
# Hermana de 1a. `historial_pilares` alimenta la compuerta dura de RQ-26 y
# `share_por_pilar` la penalizacion continua: son mecanismos distintos que leen
# LA MISMA ventana, y RQ-26 exige que esa ventana tenga una sola definicion.
# 1a puso el corte en el primero y dejo declarada la divergencia; esto la cierra.

def test_share_por_pilar_excluye_piezas_posteriores_y_conserva_las_validas(conn):
    """El reparto de `D` no observa piezas decididas despues de `D`.

    Con control positivo: las piezas validas anteriores SIGUEN contando. Sin
    esa mitad, un corte demasiado agresivo pasaria la prueba dejando el reparto
    vacio, que es el otro modo de falla.
    """
    for i in range(3):
        _pieza(conn, _tema(conn, "valida %d" % i, pilar="P4"), "2026-09-14")

    assert share_por_pilar(conn, date(2026, 9, 14)) == {"P4": 1.0}, \
        "control positivo: el pasado valido debe contar"

    for i in range(3):
        _pieza(conn, _tema(conn, "futura %d" % i, pilar="P2"), "2026-09-17")

    con_corte = share_por_pilar(conn, date(2026, 9, 14))
    sin_corte = share_por_pilar(conn, date(2026, 9, 17))

    assert con_corte == {"P4": 1.0}, \
        "entraron piezas posteriores a D en el reparto: %r" % con_corte
    assert sin_corte["P2"] == pytest.approx(0.5), \
        "el contraste no se produjo: la sonda no demuestra nada"


def test_el_reparto_y_la_compuerta_leen_la_MISMA_ventana(conn):
    """RQ-26, definicion unica. Es lo que 1a dejo declarado como divergencia.

    Los dos lectores tienen mecanismos distintos —uno decide un tope duro, el
    otro penaliza de forma continua— pero la ventana que observan tiene que ser
    la misma. Si divergieran, el motor podria cumplir la compuerta y romper la
    penalizacion, o al reves, y nadie lo notaria.
    """
    from content_factory.topics.selector import historial_pilares

    for i in range(2):
        _pieza(conn, _tema(conn, "vieja %d" % i, pilar="P4"), "2026-09-13")
    for i in range(2):
        _pieza(conn, _tema(conn, "nueva %d" % i, pilar="P2"), "2026-09-18")

    for corte in (date(2026, 9, 13), date(2026, 9, 18)):
        desde_compuerta = share_de_ventana(historial_pilares(conn, corte))
        desde_reparto = share_por_pilar(conn, corte)
        assert desde_compuerta == desde_reparto, (
            "los dos lectores divergen en %s: compuerta=%r reparto=%r"
            % (corte, desde_compuerta, desde_reparto))
