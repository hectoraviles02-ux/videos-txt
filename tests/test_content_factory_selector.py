# -*- coding: utf-8 -*-
"""Selector, angulo y volcado de derivados. Bloque 6, segunda mitad.

La compuerta de este bloque es **AC-3**: el caso adverso de diez candidatos del
mismo pilar debe tomar exactamente tres. Y el desempate tiene que ser estable en
los dos ordenes de insercion, no solo en el que salio primero.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

from content_factory import angulo
from content_factory.config import cargar_config
from content_factory.db import crear_esquema, upsert_score
from content_factory.topics.scorer import Componentes, Medidas
from content_factory.topics.selector import (
    MOTIVO_CUPO,
    TOPE_TOP,
    Candidato,
    candidatos,
    estado_corrida,
    formato_de,
    hash_entradas,
    historial_pilares,
    registrar_corrida,
    seleccionar,
    serializar,
)

FECHA = date(2026, 9, 16)          # miercoles -> cadencia "corto"


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    crear_esquema(c)
    yield c
    c.close()


@pytest.fixture()
def marca():
    m, _f = cargar_config()
    return m


def _cand(n, pilar="P2", final=0.5, intent="noticia", titulo=None):
    titulo = titulo or ("tema %s" % n)
    return Candidato(
        tema_id=n, hash_titulo="h%03d" % n, titulo=titulo, pilar=pilar,
        intent=intent,
        componentes=Componentes(demanda=0.5, novedad=1.0, relevancia=0.0,
                                produccion=0.8, historico=0.0),
        medidas=Medidas(m_demanda=0.75, m_diversidad=0.0,
                        m_estrategia=0.266667, final=final),
    )


def _tema_en_db(conn, cand, creado_en=str(FECHA)):
    conn.execute(
        "INSERT INTO temas (id, hash_titulo, titulo, pilar, intent, creado_en, "
        " estado, metodo_clasificacion) VALUES (?,?,?,?,?,?,?,?)",
        (cand.tema_id, cand.hash_titulo, cand.titulo, cand.pilar, cand.intent,
         creado_en, "DESCUBIERTA", "lexico"),
    )
    conn.commit()


# ─── D-06 · el formato sale de fecha_corte, no del reloj ────────────────────

def test_formato_deriva_de_fecha_corte(marca):
    """Un replay de un martes corrido en jueves debe dar formato de martes."""
    assert formato_de(date(2026, 9, 15), marca) == "largo"      # martes
    assert formato_de(date(2026, 9, 16), marca) == "corto"      # miercoles
    assert formato_de(date(2026, 9, 17), marca) == "podcast"    # jueves
    assert formato_de(date(2026, 9, 19), marca) == "experimento"  # sabado


def test_el_formato_no_mira_el_reloj(marca):
    """Se corre dos veces la misma fecha: el resultado no depende de hoy."""
    import datetime as dt
    assert formato_de(date(2026, 9, 17), marca) == "podcast"
    assert formato_de(dt.date(2026, 9, 17), marca) == "podcast"


# ─── AC-3 · la compuerta dura ───────────────────────────────────────────────

def test_caso_adverso_10_del_mismo_pilar_toma_3(marca):
    """El caso que define el bloque: diez de P2 arriba, otros pilares debajo."""
    cands = [_cand(i, "P2", final=0.9 - i * 0.001) for i in range(10)]
    cands += [_cand(100 + i, p, final=0.5 - i * 0.001)
              for i, p in enumerate(("P1", "P3", "P4", "P5", "P6", "P7", "P1"))]

    elegidos, descartados = seleccionar(cands, [], marca)

    de_p2 = [c for c in elegidos if c.pilar == "P2"]
    assert len(de_p2) == 3
    assert len(elegidos) == TOPE_TOP
    assert all(m == MOTIVO_CUPO for _c, m in descartados)
    assert len([c for c in elegidos if c.pilar != "P2"]) == 7


def test_compuerta_activa_en_la_primera_corrida(marca):
    """RQ-26: activa desde el dia uno, sin esperar a acumular 10 piezas."""
    cands = [_cand(i, "P2", final=0.9 - i * 0.001) for i in range(10)]
    elegidos, _d = seleccionar(cands, [], marca)
    assert len(elegidos) == 3


def test_cuarta_pieza_del_pilar_rechazada(marca):
    """El cuarto candidato del pilar se descarta con motivo, no se ignora."""
    cands = [_cand(i, "P2", final=0.9 - i * 0.001) for i in range(4)]
    elegidos, descartados = seleccionar(cands, [], marca)
    assert len(elegidos) == 3
    assert len(descartados) == 1
    cand, motivo = descartados[0]
    assert motivo == MOTIVO_CUPO and cand.tema_id == 3


def test_la_restriccion_vale_para_cada_pilar_no_solo_para_uno(marca):
    """No basta con frenar a P2: ningun pilar puede pasar de 3."""
    cands = []
    for i, p in enumerate(("P1", "P2", "P3", "P4", "P5", "P6", "P7")):
        for j in range(5):
            cands.append(_cand(i * 10 + j, p, final=0.9 - (i * 10 + j) * 0.001))
    elegidos, _d = seleccionar(cands, [], marca)
    conteo = {}
    for c in elegidos:
        conteo[c.pilar] = conteo.get(c.pilar, 0) + 1
    assert max(conteo.values()) <= 3
    assert len(elegidos) == TOPE_TOP


def test_top_incompleto_antes_que_compuerta_relajada(marca):
    """RQ-27: con candidatos de solo dos pilares salen 6, no 10."""
    cands = [_cand(i, "P2", final=0.9 - i * 0.001) for i in range(8)]
    cands += [_cand(50 + i, "P4", final=0.5 - i * 0.001) for i in range(8)]
    elegidos, descartados = seleccionar(cands, [], marca)
    assert len(elegidos) == 6
    assert len([c for c in elegidos if c.pilar == "P2"]) == 3
    assert len([c for c in elegidos if c.pilar == "P4"]) == 3
    assert descartados, "los que no cupieron deben quedar con motivo"


def test_top_nunca_mayor_que_10(marca):
    """RQ-54: exactamente 10, salvo el caso de RQ-27. Nunca mas."""
    cands = []
    for i, p in enumerate(("P1", "P2", "P3", "P4", "P5", "P6", "P7")):
        for j in range(3):
            cands.append(_cand(i * 10 + j, p, final=0.9 - (i * 10 + j) * 0.001))
    elegidos, _d = seleccionar(cands, [], marca)
    assert len(elegidos) == TOPE_TOP


def test_la_ventana_es_deslizante_no_acumulativa(marca):
    """Decision declarada: la lista combinada se recorta a las 10 mas recientes.

    Leer "concatenadas" sin recortar dejaria al dia siguiente sin poder elegir
    casi nada, porque cada pilar llegaria con su cupo gastado desde ayer. Con
    ventana deslizante, una corrida completa de 10 deja la ventana igual a las
    10 de hoy y la compuerta se vuelve a cumplir por si sola.
    """
    historial = ["P2"] * 10          # ayer se lleno con P2
    cands = [_cand(i, "P2", final=0.9 - i * 0.001) for i in range(10)]
    elegidos, _d = seleccionar(cands, historial, marca)
    # Los tres primeros de hoy empujan a los mas viejos fuera de la ventana,
    # pero el cupo de P2 se agota igual: no se relaja nada.
    assert len(elegidos) <= 3

    # Con historial de otro pilar, hoy si puede meter sus tres.
    elegidos2, _d2 = seleccionar(cands, ["P4"] * 10, marca)
    assert len([c for c in elegidos2 if c.pilar == "P2"]) == 3


def test_la_compuerta_no_reordena_para_acomodar(marca):
    """Se respeta el orden por `final`: la compuerta descarta, no optimiza."""
    cands = [_cand(0, "P2", final=0.9), _cand(1, "P2", final=0.8),
             _cand(2, "P2", final=0.7), _cand(3, "P2", final=0.6),
             _cand(4, "P4", final=0.1)]
    elegidos, _d = seleccionar(cands, [], marca)
    assert [c.tema_id for c in elegidos] == [0, 1, 2, 4]


# ─── D-04 · desempate total ─────────────────────────────────────────────────

def test_desempate_por_hash_titulo_en_ambos_ordenes_de_insercion(conn):
    """Dos `final` identicos: el orden lo decide `hash_titulo`, no la insercion.

    Se prueba en los DOS ordenes. Probar uno solo no distingue un desempate
    real de una coincidencia con el orden de llegada.
    """
    def montar(orden):
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        crear_esquema(c)
        for n in orden:
            cand = _cand(n, "P2", final=0.5)
            _tema_en_db(c, cand)
            upsert_score(c, n, str(FECHA), {"final": 0.5}, "v0.1", 6)
        return [x.hash_titulo for x in candidatos(c, FECHA)]

    assert montar([1, 2]) == montar([2, 1]) == ["h001", "h002"]


def test_candidatos_ordenan_por_final_descendente(conn):
    for n, final in ((1, 0.2), (2, 0.9), (3, 0.5)):
        cand = _cand(n, "P2", final=final)
        _tema_en_db(conn, cand)
        upsert_score(conn, n, str(FECHA), {"final": final}, "v0.1", 6)
    assert [c.tema_id for c in candidatos(conn, FECHA)] == [2, 3, 1]


# ─── hash_entradas ──────────────────────────────────────────────────────────

def test_hash_entradas_incluye_version_reglas():
    """Cambiar los pesos DEBE cambiar el hash, o comparar corridas mentiria."""
    a = hash_entradas("v0.1+aaa", FECHA, ["s1", "s2"])
    b = hash_entradas("v0.1+bbb", FECHA, ["s1", "s2"])
    assert a != b


def test_hash_entradas_no_depende_del_orden_de_las_senales():
    """Las fuentes responden en cualquier orden: el hash no puede depender de eso."""
    assert hash_entradas("v", FECHA, ["b", "a", "c"]) == \
        hash_entradas("v", FECHA, ["a", "c", "b"])


def test_hash_entradas_depende_de_la_fecha():
    assert hash_entradas("v", FECHA, ["a"]) != \
        hash_entradas("v", date(2026, 9, 15), ["a"])


def test_serializacion_es_canonica():
    """Mismo objeto, mismo byte: sin espacios, llaves ordenadas, acentos vivos."""
    a = serializar({"b": 1, "a": "canción"})
    assert a == '{"a":"canción","b":1}'
    assert serializar({"a": "canción", "b": 1}) == a


# ─── H-3 · estado_corrida ───────────────────────────────────────────────────

def test_estado_corrida_incompleta_por_fuente_caida():
    assert estado_corrida(["ok", "fallida"], 10) == "incompleta"
    assert estado_corrida(["ok", "desactivada"], 10) == "incompleta"
    assert estado_corrida(["ok", "omitida_por_cuota"], 10) == "incompleta"


def test_estado_corrida_incompleta_por_top_menor_a_10():
    assert estado_corrida(["ok"], 6) == "incompleta"
    assert estado_corrida(["ok"], 10) == "completa"


def test_reejecutar_senales_no_cambia_estado_corrida():
    """`desde_raw` y `http_304` NO incompletan la corrida, y es deliberado.

    Si lo hicieran, reejecutar la etapa de senales el mismo dia cambiaria el
    `top10_json` SIN cambiar `hash_entradas`, y RQ-15 con AC-6 quedarian rotos.
    """
    assert estado_corrida(["ok", "desde_raw", "http_304"], 10) == "completa"


def test_estado_corrida_computable_desde_el_manifiesto(tmp_path):
    """La misma funcion sirve para la base y para `raw/D/_manifiesto.json`.

    Es lo que mantiene honesta la segunda igualdad de AC-1: el replay
    reconstruye el estado desde el manifiesto y tiene que dar lo mismo.
    """
    manifiesto = tmp_path / "_manifiesto.json"
    manifiesto.write_text(serializar(
        {"fuentes": [{"fuente": "hackernews", "resultado": "ok"},
                     {"fuente": "reddit", "resultado": "fallida"}]}),
        encoding="utf-8")
    datos = json.loads(manifiesto.read_text(encoding="utf-8"))
    desde_manifiesto = [f["resultado"] for f in datos["fuentes"]]
    assert estado_corrida(desde_manifiesto, 10) == \
        estado_corrida(["ok", "fallida"], 10) == "incompleta"


# ─── 6.6 · derivados antes del COMMIT ───────────────────────────────────────

def test_derivados_escrito_antes_del_commit(conn, marca, tmp_path):
    """Al terminar existen las dos cosas: la fila de `corridas` y el archivo."""
    cands = [_cand(i, ("P1", "P2", "P3")[i % 3], final=0.9 - i * 0.01)
             for i in range(3)]
    for c in cands:
        _tema_en_db(conn, c)
    por_tema = {c.hash_titulo: {"embedding_b64": "AAA", "pen_repeticion": 0.25,
                               "evidencia": [{"url": "https://ej.mx"}],
                               "angulo": "un angulo", "angulo_metodo": "plantilla"}
                for c in cands}

    top10_json = registrar_corrida(
        conn, FECHA, cands, marca=marca, version_reglas="v0.1+x", sin_llm=True,
        resultados_fuente=["ok"], hashes_senales=["s1"], por_tema=por_tema,
        raiz_raw=tmp_path)

    archivo = tmp_path / str(FECHA) / "_derivados.json"
    assert archivo.exists()
    datos = json.loads(archivo.read_text(encoding="utf-8"))
    assert set(datos["por_tema"]) == {c.hash_titulo for c in cands}
    # Solo lo que el replay NO puede recalcular desde `raw/`. Los cuatro
    # campos se ganaron uno a uno corriendo el replay y viendo que fallaba:
    # sin `creado_en`, un tema visto tres dias antes parecia nuevo y su
    # `novedad` salia 1.0 en vez de 0.966667.
    assert set(datos["por_tema"][cands[0].hash_titulo]) == {
        "embedding_b64", "pen_repeticion", "evidencia", "creado_en"}
    # La ventana de esa noche tambien va: la seleccion depende de las piezas de
    # noches anteriores, y eso no vive en `raw/D/`.
    assert "ventana" in datos

    fila = conn.execute("SELECT top10_json, estado_corrida FROM corridas").fetchone()
    assert fila[0] == top10_json
    assert fila[1] == "incompleta"       # 3 piezas, menos de 10
    assert conn.execute("SELECT COUNT(*) FROM piezas").fetchone()[0] == 3


def test_fallo_al_volcar_derivados_hace_rollback_de_la_corrida(conn, marca, tmp_path):
    """Si el volcado revienta, NO puede quedar una corrida sin sus derivados."""
    cands = [_cand(0, "P1", final=0.9)]
    _tema_en_db(conn, cands[0])

    # Una ruta imposible: `raiz_raw` apunta a un archivo, no a un directorio.
    bloqueo = tmp_path / "soy_un_archivo"
    bloqueo.write_text("x", encoding="utf-8")

    with pytest.raises(Exception):
        registrar_corrida(
            conn, FECHA, cands, marca=marca, version_reglas="v0.1+x",
            sin_llm=True, resultados_fuente=["ok"], hashes_senales=["s1"],
            por_tema={}, raiz_raw=bloqueo)

    assert conn.execute("SELECT COUNT(*) FROM corridas").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM piezas").fetchone()[0] == 0


def test_el_escritor_de_derivados_es_unico_y_atomico(tmp_path):
    """Un solo escritor, en `raw.py`, y el intercambio no deja archivo a medias.

    Tener dos escritores fue un defecto real de esta sesion: el selector escribia
    con una forma y `raw.py` con otra, asi que el replay habria leido en el nivel
    equivocado sin que ninguna prueba lo notara.
    """
    from content_factory import raw
    from content_factory.topics import selector as sel

    assert not hasattr(sel, "volcar_derivados"), "reaparecio un segundo escritor"

    raw.escribir_derivados(FECHA, {"a": {"pen_repeticion": 0.1}}, base=tmp_path)
    raw.escribir_derivados(FECHA, {"b": {"pen_repeticion": 0.2}}, base=tmp_path)

    # Lo que se escribe es lo que se lee, ya desenvuelto.
    assert raw.leer_derivados(FECHA, base=tmp_path) == {"b": {"pen_repeticion": 0.2}}
    assert list((tmp_path / str(FECHA)).glob("*.tmp")) == []


def test_reejecutar_el_dia_reemplaza_no_acumula(conn, marca, tmp_path):
    """D-11: una fila por dia; las piezas viejas caen por CASCADE."""
    cands = [_cand(i, ("P1", "P2")[i % 2], final=0.9 - i * 0.01) for i in range(2)]
    for c in cands:
        _tema_en_db(conn, c)
    comun = dict(marca=marca, version_reglas="v0.1+x", sin_llm=True,
                 resultados_fuente=["ok"], hashes_senales=["s1"],
                 por_tema={}, raiz_raw=tmp_path)
    registrar_corrida(conn, FECHA, cands, **comun)
    registrar_corrida(conn, FECHA, cands[:1], **comun)

    assert conn.execute("SELECT COUNT(*) FROM corridas").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM piezas").fetchone()[0] == 1


def test_la_ventana_cumple_la_compuerta_despues_de_registrar(conn, marca, tmp_path):
    """RQ-26: la consulta de verificacion agrupada por pilar no pasa de 3.

    Es la comprobacion que exige el escenario "verificacion por una sola
    consulta": se corre contra la base ya escrita, no contra la lista en memoria.
    """
    cands = [_cand(i, "P2", final=0.9 - i * 0.001) for i in range(10)]
    cands += [_cand(100 + i, p, final=0.5 - i * 0.001)
              for i, p in enumerate(("P1", "P3", "P4", "P5", "P6", "P7", "P1"))]
    for c in cands:
        _tema_en_db(conn, c)
    elegidos, _d = seleccionar(cands, historial_pilares(conn, FECHA), marca)
    registrar_corrida(conn, FECHA, elegidos, marca=marca, version_reglas="v0.1+x",
                      sin_llm=True, resultados_fuente=["ok"], hashes_senales=["s1"],
                      por_tema={}, raiz_raw=tmp_path)

    filas = conn.execute(
        "SELECT t.pilar, COUNT(*) FROM ("
        "  SELECT * FROM piezas WHERE estado = 'SELECCIONADA' "
        "  ORDER BY creada_en DESC, id DESC LIMIT 10) p "
        "JOIN temas t ON t.id = p.tema_id GROUP BY t.pilar").fetchall()
    assert filas and max(f[1] for f in filas) <= 3


# ─── Reproducibilidad del conjunto ──────────────────────────────────────────

def test_la_seleccion_no_depende_del_orden_de_llegada(marca):
    """Mismo insumo desordenado, mismo resultado. Conecta con el defecto de 6.1."""
    base = [_cand(i, ("P1", "P2", "P3", "P4")[i % 4], final=0.9 - i * 0.01)
            for i in range(16)]
    esperado = [c.tema_id for c in seleccionar(base, [], marca)[0]]
    for corte in (3, 7, 11):
        revuelto = base[corte:] + base[:corte]
        # El llamador siempre ordena por `final DESC, hash ASC` antes de
        # seleccionar: es lo que hace `candidatos`. Aqui se reproduce.
        revuelto.sort(key=lambda c: (-c.medidas.final, c.hash_titulo))
        assert [c.tema_id for c in seleccionar(revuelto, [], marca)[0]] == esperado


def test_dos_corridas_identicas_dan_el_mismo_json(conn, marca, tmp_path):
    cands = [_cand(i, ("P1", "P2", "P3")[i % 3], final=0.9 - i * 0.01)
             for i in range(3)]
    for c in cands:
        _tema_en_db(conn, c)
    comun = dict(marca=marca, version_reglas="v0.1+x", sin_llm=True,
                 resultados_fuente=["ok"], hashes_senales=["s2", "s1"],
                 por_tema={}, raiz_raw=tmp_path)
    a = registrar_corrida(conn, FECHA, cands, **comun)
    b = registrar_corrida(conn, FECHA, cands, **comun)
    assert a == b


# ─── 6.4 · angulo determinista ──────────────────────────────────────────────

def test_angulo_plantilla_nunca_null(marca):
    """D-07: `piezas.angulo` no puede ser NULL bajo ninguna circunstancia."""
    for intent in ("tutorial", "noticia", "critica", "intent_que_no_existe"):
        texto = angulo.plantilla(_cand(1, intent=intent), marca)
        assert texto and texto.strip()


def test_angulo_es_determinista(marca):
    cand = _cand(1, intent="tutorial", titulo="Como instalar Ollama")
    primero = angulo.plantilla(cand, marca)
    for _ in range(5):
        assert angulo.plantilla(cand, marca) == primero


def test_angulo_cambia_con_el_formato_del_dia(marca):
    cand = _cand(1, intent="tutorial")
    assert angulo.plantilla(cand, marca, "corto") != \
        angulo.plantilla(cand, marca, "podcast")


def test_angulo_servido_de_la_cache_sin_red(conn, marca):
    """Con acierto de cache se sirve lo guardado, y nadie llama a la red."""
    cand = _cand(1, titulo="Un titulo cualquiera")
    llave = angulo.hash_entrada("modelo-x", angulo.PROMPT_VERSION, cand.titulo)
    conn.execute(
        "INSERT INTO cache_llm (hash_entrada, modelo, prompt_version, salida_json, "
        " creada_en) VALUES (?,?,?,?,?)",
        (llave, "modelo-x", angulo.PROMPT_VERSION, "angulo guardado", str(FECHA)))
    conn.commit()

    def pedir_que_lanza(_p):
        raise AssertionError("se llamo a la red y no debia")

    texto, metodo = angulo.proponer(cand, conn, marca, sin_llm=False,
                                    modelo="modelo-x", pedir=pedir_que_lanza)
    assert (texto, metodo) == ("angulo guardado", "cache_llm")


def test_sin_llm_ignora_la_cache(conn, marca):
    """El replay `--sin-llm` debe dar lo mismo en una base recien creada."""
    cand = _cand(1, titulo="Un titulo cualquiera")
    llave = angulo.hash_entrada("modelo-x", angulo.PROMPT_VERSION, cand.titulo)
    conn.execute(
        "INSERT INTO cache_llm (hash_entrada, modelo, prompt_version, salida_json, "
        " creada_en) VALUES (?,?,?,?,?)",
        (llave, "modelo-x", angulo.PROMPT_VERSION, "angulo guardado", str(FECHA)))
    conn.commit()
    texto, metodo = angulo.proponer(cand, conn, marca, sin_llm=True, modelo="modelo-x")
    assert metodo == "plantilla" and texto != "angulo guardado"


def test_sin_acierto_de_cache_cae_a_plantilla(conn, marca):
    cand = _cand(1)
    texto, metodo = angulo.proponer(cand, conn, marca, sin_llm=False, modelo="m")
    assert metodo == "plantilla" and texto


def test_la_llave_de_cache_separa_modelo_y_version():
    """Un angulo de otro modelo o de otro prompt no puede servirse como el de ahora."""
    a = angulo.hash_entrada("m1", "a1", "texto")
    assert a != angulo.hash_entrada("m2", "a1", "texto")
    assert a != angulo.hash_entrada("m1", "a2", "texto")
    assert a != angulo.hash_entrada("m1", "a1", "otro")


def test_el_modulo_de_angulo_no_importa_red():
    """RQ-33: ninguna rama de este modulo puede tocar la red."""
    import inspect
    fuente = inspect.getsource(angulo)
    for prohibido in ("import requests", "urlopen", "http.solicitar", "ollama"):
        assert prohibido not in fuente, prohibido


# ─── AC-4 · los nueve campos del plan ───────────────────────────────────────

def test_el_top10_json_trae_los_nueve_campos(conn, marca, tmp_path):
    cands = [_cand(0, "P1", final=0.9)]
    _tema_en_db(conn, cands[0])
    por_tema = {cands[0].hash_titulo: {
        "angulo": "un angulo", "angulo_metodo": "plantilla",
        "pen_repeticion": 0.1, "pen_saturacion": 0.2,
        "evidencia": [{"url": "https://ej.mx", "fuente": "hackernews"}]}}
    crudo = registrar_corrida(
        conn, FECHA, cands, marca=marca, version_reglas="v0.1+x", sin_llm=True,
        resultados_fuente=["ok"], hashes_senales=["s1"], por_tema=por_tema,
        raiz_raw=tmp_path)
    tema = json.loads(crudo)["temas"][0]
    for campo in ("demanda", "novedad", "dificultad", "potencial", "score",
                  "penalizaciones", "angulo_propuesto", "formato", "evidencia"):
        assert campo in tema, campo
    # dificultad y potencial son DERIVADOS, no columnas.
    assert tema["dificultad"] == pytest.approx(1 - 0.8)
    assert tema["potencial"] == pytest.approx(0.266667)


def test_el_top_incompleto_se_declara_en_los_avisos(conn, marca, tmp_path):
    """RQ-27: el informe debe declarar el faltante y su causa."""
    cands = [_cand(0, "P1", final=0.9)]
    _tema_en_db(conn, cands[0])
    crudo = registrar_corrida(
        conn, FECHA, cands, marca=marca, version_reglas="v0.1+x", sin_llm=True,
        resultados_fuente=["ok"], hashes_senales=["s1"], por_tema={},
        raiz_raw=tmp_path)
    avisos = json.loads(crudo)["avisos"]
    assert any("1 de 10" in a and "compuerta de diversidad" in a for a in avisos)


# ─── Identidad temporal del pilar · el defecto causal ───────────────────────
#
# `tema.pilar` es ESTADO PRESENTE y puede cambiar con evidencia legítima.
# `piezas.pilar_decision` es el HECHO HISTÓRICO: el pilar que la compuerta
# consumió al materializar esa pieza. Las dos verdades deben coexistir.
#
# Medido antes del arreglo: dos piezas publicadas como P4 pasaban a contarse
# como P2 al reclasificarse el tema. El conteo pasaba de P2 3 / P4 4 a
# P2 5 / P4 2, cambiando qué pilar estaba saturado, y la compuerta dejó pasar
# una pieza que debía bloquear.


def test_la_pieza_conserva_el_pilar_que_consumio_la_decision(conn, marca, tmp_path):
    """El presente cambia y el pasado no le sigue."""
    cand = _cand(1, "P4", final=0.9)
    _tema_en_db(conn, cand)
    registrar_corrida(conn, FECHA, [cand], marca=marca, version_reglas="v0.1+x",
                      sin_llm=True, resultados_fuente=["ok"], hashes_senales=["s1"],
                      por_tema={}, raiz_raw=tmp_path)

    guardado = conn.execute(
        "SELECT pilar_decision FROM piezas WHERE tema_id = ?", (cand.tema_id,)
    ).fetchone()[0]
    assert guardado == "P4"

    # Llega evidencia legítima y el tema cambia de pilar. Es correcto.
    conn.execute("UPDATE temas SET pilar = 'P2' WHERE id = ?", (cand.tema_id,))
    conn.commit()

    assert conn.execute("SELECT pilar FROM temas WHERE id = ?",
                        (cand.tema_id,)).fetchone()[0] == "P2"
    assert conn.execute("SELECT pilar_decision FROM piezas WHERE tema_id = ?",
                        (cand.tema_id,)).fetchone()[0] == "P4"


def test_la_ventana_cuenta_el_pilar_historico_no_el_actual(conn, marca, tmp_path):
    """La compuerta decide sobre hechos, no sobre la clasificación de hoy.

    Es el corazón del defecto: con el JOIN al tema, reclasificar reescribía la
    ventana y cambiaba qué pilar estaba saturado.
    """
    cands = [_cand(i, "P4", final=0.9 - i * 0.01) for i in range(3)]
    for c in cands:
        _tema_en_db(conn, c)
    registrar_corrida(conn, FECHA, cands, marca=marca, version_reglas="v0.1+x",
                      sin_llm=True, resultados_fuente=["ok"], hashes_senales=["s1"],
                      por_tema={}, raiz_raw=tmp_path)
    assert historial_pilares(conn, FECHA) == ["P4", "P4", "P4"]

    conn.execute("UPDATE temas SET pilar = 'P2'")
    conn.commit()

    assert historial_pilares(conn, FECHA) == ["P4", "P4", "P4"], \
        "la ventana siguió al tema en vez de al hecho histórico"


def test_el_reparto_por_pilar_tambien_usa_el_historico(conn, marca, tmp_path):
    """El segundo lector histórico, el de `pen_saturacion`."""
    from content_factory.topics.diversity import share_por_pilar

    cands = [_cand(i, "P4", final=0.9 - i * 0.01) for i in range(2)]
    for c in cands:
        _tema_en_db(conn, c)
    registrar_corrida(conn, FECHA, cands, marca=marca, version_reglas="v0.1+x",
                      sin_llm=True, resultados_fuente=["ok"], hashes_senales=["s1"],
                      por_tema={}, raiz_raw=tmp_path)
    assert share_por_pilar(conn, FECHA) == {"P4": 1.0}

    conn.execute("UPDATE temas SET pilar = 'P2'")
    conn.commit()
    assert share_por_pilar(conn, FECHA) == {"P4": 1.0}, "el reparto siguió al tema"


# ─── 1a · corte temporal en historial_pilares ───────────────────────────────

def _corrida_de_una_pieza(conn, marca, tmp_path, fecha, tema_id, pilar):
    """Materializa una corrida con UNA pieza, por el camino real
    (`registrar_corrida`), en el dia `fecha`."""
    cand = _cand(tema_id, pilar, final=0.5)
    _tema_en_db(conn, cand)
    registrar_corrida(conn, fecha, [cand], marca=marca, version_reglas="v0.1+x",
                      sin_llm=True, resultados_fuente=["ok"], hashes_senales=["s1"],
                      por_tema={}, raiz_raw=tmp_path)


def test_historial_pilares_excluye_piezas_posteriores_y_conserva_las_validas(
    conn, marca, tmp_path
):
    """1a: la ventana de `D` no puede ver piezas decididas despues de `D`.

    Se materializan tres piezas validas (P4, creadas ANTES de `FECHA`) y tres
    posteriores (P2, creadas DESPUES de `FECHA`), como dejaria una base de
    produccion que ya corrio dias posteriores al reconstruir una noche vieja.

    El corte tiene que hacer las DOS cosas a la vez:
      * futuro (`creada_en > FECHA`)  -> NO entra;
      * pasado valido (`creada_en <= FECHA`) -> SI entra (control positivo).
    La consulta SIN corte (el estado defectuoso) lee las futuras: es lo que la
    prueba debe detectar si el corte se retira.
    """
    for i, (dia, pilar) in enumerate((
            (date(2026, 9, 10), "P4"), (date(2026, 9, 11), "P4"),
            (date(2026, 9, 12), "P4"))):
        _corrida_de_una_pieza(conn, marca, tmp_path, dia, i + 1, pilar)
    for i, (dia, pilar) in enumerate((
            (date(2026, 9, 17), "P2"), (date(2026, 9, 18), "P2"),
            (date(2026, 9, 19), "P2")), start=11):
        _corrida_de_una_pieza(conn, marca, tmp_path, dia, i + 1, pilar)

    assert historial_pilares(conn, FECHA) == ["P4", "P4", "P4"], \
        "el corte excluyo piezas validas o dejo entrar piezas posteriores"

    con_corte = conn.execute(
        "SELECT p.pilar_decision FROM piezas p "
        "WHERE p.estado IN ('SELECCIONADA') AND p.creada_en <= ? "
        "ORDER BY p.creada_en DESC, p.id DESC LIMIT ?",
        (str(FECHA), TOPE_TOP)).fetchall()
    assert [r[0] for r in con_corte] == ["P4", "P4", "P4"]

    sin_corte = conn.execute(
        "SELECT p.pilar_decision FROM piezas p "
        "WHERE p.estado IN ('SELECCIONADA') "
        "ORDER BY p.creada_en DESC, p.id DESC LIMIT ?",
        (TOPE_TOP,)).fetchall()
    assert [r[0] for r in sin_corte] == ["P2", "P2", "P2", "P4", "P4", "P4"], \
        "sin el corte las piezas futuras entran en la ventana: el defecto"


# ─── D-19 etapa 2A · P1 · la materializacion conserva su firma ──────────────

def test_p1_la_corrida_conserva_en_raw_la_firma_con_la_que_se_decidio(
        conn, marca, tmp_path):
    """El artefacto historico de `D` guarda la identidad de configuracion.

    **Es la AUTORIDAD que D-19 etapa 2A necesitaba y no existia.** Vive junto a
    los crudos y no en la base, porque el replay no abre la base productiva a
    proposito: si la identidad viviera solo alli, no seria alcanzable desde
    donde hay que compararla.

    Se afirma que el valor guardado es EXACTAMENTE el que la corrida uso, no uno
    recalculado: `escribir_derivados` recibe el mismo `version_reglas` que se
    escribio en `corridas`. Si volviera a llamar a `cargar_config` por su
    cuenta, la firma guardada podria no ser la que decidio.
    """
    from content_factory import raw as raw_mod

    firma = "v0.1+decidioconesta"
    cands = [_cand(i, ("P1", "P2")[i % 2], final=0.9 - i * 0.01) for i in range(2)]
    for c in cands:
        _tema_en_db(conn, c)
    registrar_corrida(conn, FECHA, cands, marca=marca, version_reglas=firma,
                      sin_llm=True, resultados_fuente=["ok"], hashes_senales=["s1"],
                      por_tema={}, raiz_raw=tmp_path)

    assert raw_mod.leer_version_reglas(FECHA, tmp_path) == firma
    guardada = conn.execute(
        "SELECT version_reglas FROM corridas WHERE fecha_corte = ?",
        (str(FECHA),)).fetchone()[0]
    assert guardada == firma, "la firma del artefacto y la de la base divergen"


def test_p1b_un_dia_sin_firma_no_recibe_una_inventada(conn, marca, tmp_path):
    """AUSENTE y VACIA tienen que ser distinguibles.

    `escribir_derivados` omite la llave cuando no le dan valor, en vez de
    escribirla en blanco. Un archivo sin la llave es legacy; uno con la llave
    vacia seria una firma fabricada, y fabricarla es exactamente lo que este
    contrato existe para impedir.
    """
    from content_factory import raw as raw_mod

    raw_mod.escribir_derivados(FECHA, {}, base=tmp_path, ventana=[])
    assert raw_mod.leer_version_reglas(FECHA, tmp_path) is None
