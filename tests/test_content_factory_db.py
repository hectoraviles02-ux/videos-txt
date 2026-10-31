# -*- coding: utf-8 -*-
"""Persistencia y guarda de produccion. Bloque 2 del topic engine.

Cubre RQ-04 (esquema), RQ-05 y RQ-47 (guarda de produccion, AC-7), RQ-15 y
RQ-29 (idempotencia y redondeo al persistir, AC-6), RQ-24 y RQ-53 (el paquete
no arrastra imports pesados) y RQ-06 (artefactos locales ignorados por git).
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from content_factory import db

RAIZ = Path(__file__).resolve().parent.parent

TABLAS = (
    "senales", "temas", "tema_senal", "scores", "piezas", "eventos", "corridas",
    "ingestas", "cache_llm", "cache_http", "claims", "publicaciones", "metricas",
    "esquema_version",
)


@pytest.fixture()
def conn(tmp_path):
    c = db.conectar(tmp_path / "prueba.db")
    db.crear_esquema(c)
    yield c
    c.close()


def _senal(h: str, fuente: str = "reddit:r/x", fecha: str = "2026-09-16") -> dict:
    return {
        "hash": h, "fuente": fuente, "texto": "titulo " + h, "url": "https://ejemplo/" + h,
        "publicada_en": None, "detectada_en": fecha, "extra": {}, "raw_path": "raw/%s/x.json" % fecha,
    }


def _tema(h: str, pilar: str = "P2", fecha: str = "2026-09-16") -> dict:
    return {
        "hash_titulo": h, "titulo": "tema " + h, "pilar": pilar, "intent": "tutorial",
        "creado_en": fecha, "estado": "CANDIDATA", "metodo_clasificacion": "lexico",
        "evidencia": [],
    }


# ─── RQ-04 · esquema ────────────────────────────────────────────────────────

def test_esquema_creado_idempotente(conn, tmp_path):
    """Las 14 tablas existen y volver a crear el esquema no rompe ni duplica."""
    nombres = {
        f["name"]
        for f in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert set(TABLAS) <= nombres
    db.crear_esquema(conn)  # segunda vez
    versiones = conn.execute("SELECT COUNT(*) AS n FROM esquema_version").fetchone()["n"]
    assert versiones == 1
    # v2 anade `piezas.pilar_decision`. La version sube porque una base creada
    # con v1 NO puede migrarse sin inventar el pilar de decisiones pasadas.
    # v3 anade `senales.publicada_canonica` y retira `ix_senales_ventana`
    # (frente de senales, D-18 sustituida). El valor va LITERAL a proposito:
    # una subida de version no intencionada tiene que poner esto en rojo.
    assert conn.execute("SELECT version FROM esquema_version").fetchone()["version"] == 6   # v6: aprobacion solo por codigo de Telegram (Etapa 1, 21-oct)


def test_foreign_key_check_vacio(conn):
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_pragma_foreign_keys_on(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_estado_corrida_existe_y_esta_acotado(conn):
    """`estado_corrida` es adicion declarada (hueco H-3) y solo admite dos valores."""
    columnas = {f["name"] for f in conn.execute("PRAGMA table_info(corridas)").fetchall()}
    assert "estado_corrida" in columnas
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO corridas (fecha_corte, version_reglas, hash_entradas, top10_json,"
            " creada_en, sin_llm, estado_corrida) VALUES ('d','v','h','[]','d',0,'a medias')"
        )


def test_el_indice_de_d18_no_se_crea_porque_d18_fue_sustituida(conn):
    """Esta prueba antes afirmaba lo CONTRARIO, y se deja dicho aqui.

    Se llamaba `test_indice_de_ventana_sobre_expresion` y exigia que existiera
    `ix_senales_ventana`, el indice de expresion de D-18. D-18 quedo SUSTITUIDA
    por evidencia empirica, por tres premisas falsadas y medidas:

    1. la expresion `COALESCE(publicada_en, detectada_en)` no representa bien el
       instante: 5 de 15 valores son RFC-822 y la comparacion textual los deja
       siempre fuera;
    2. las consultas productivas no usan esa expresion sino `substr(...,1,10)`,
       y `EXPLAIN QUERY PLAN` da `SCAN senales` en las dos: el indice nunca
       sirvio a ninguna;
    3. el coste que D-18 queria evitar -sincronizar una columna "en cada
       UPSERT"- no existe: `insertar_senales` es `INSERT OR IGNORE` y una
       senal no se modifica nunca despues de insertarse.

    Se conserva la prueba con el contrato invertido en vez de borrarla: borrarla
    haria parecer que D-18 nunca existio.
    """
    indices = {
        f["name"] for f in conn.execute("PRAGMA index_list(senales)").fetchall()
    }
    assert "ix_senales_ventana" not in indices


# ─── RQ-05, RQ-47 · guarda de produccion (AC-7) ─────────────────────────────

def test_sin_bandera_escribe_en_test_db():
    """Sin argumentos, la base por omision es la de prueba, nunca la real."""
    assert db.RUTA_PRUEBA.name == "content.test.db"
    assert db.RUTA_PRODUCCION.name == "content.db"
    assert db.RUTA_PRUEBA != db.RUTA_PRODUCCION


def test_db_produccion_sin_bandera_falla(tmp_path):
    """Apuntar a content.db sin --produccion levanta; no se escribe nada."""
    with pytest.raises(db.GuardaDeProduccion) as err:
        db.conectar(db.RUTA_PRODUCCION)
    assert "produccion" in str(err.value)


def test_bandera_sin_la_ruta_de_produccion_tambien_falla(tmp_path):
    """La bandera sola tampoco basta: las dos cosas deben coincidir."""
    with pytest.raises(db.GuardaDeProduccion):
        db.conectar(tmp_path / "otra.db", produccion=True)


def test_guarda_produccion_desde_subproceso_sale_distinto_de_cero(tmp_path):
    """AC-7 pide codigo de salida distinto de cero, no solo una excepcion."""
    codigo = (
        "import sys; sys.path.insert(0, r'%s');"
        "from content_factory import db; db.conectar(db.RUTA_PRODUCCION)" % RAIZ
    )
    r = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True)
    assert r.returncode != 0
    assert "GuardaDeProduccion" in r.stderr


# ─── RQ-15, RQ-29 · idempotencia y redondeo (AC-6) ──────────────────────────

def test_upserts_idempotentes(conn):
    """Reejecutar la misma ingestion no duplica ni acumula."""
    senales = [_senal("a" * 64), _senal("b" * 64)]
    assert db.insertar_senales(conn, senales) == 2
    assert db.insertar_senales(conn, senales) == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM senales").fetchone()["n"] == 2

    tid1 = db.upsert_tema(conn, _tema("c" * 64))
    tid2 = db.upsert_tema(conn, _tema("c" * 64))
    assert tid1 == tid2
    assert conn.execute("SELECT COUNT(*) AS n FROM temas").fetchone()["n"] == 1


def test_score_se_redondea_al_persistir(conn):
    """D-05: lo que compara ORDER BY debe ser exactamente lo guardado."""
    tid = db.upsert_tema(conn, _tema("d" * 64))
    db.upsert_score(
        conn, tid, "2026-09-16",
        {"demanda": 0.1234567891, "final": 0.9876543219, "m_demanda": 0.5,
         "m_diversidad": 0.5, "m_estrategia": 0.5, "novedad": 0.0,
         "relevancia": 0.0, "produccion": 0.0},
        "v0.1+abc", decimales=6,
    )
    fila = conn.execute("SELECT demanda, final FROM scores WHERE tema_id = ?", (tid,)).fetchone()
    assert fila["demanda"] == 0.123457
    assert fila["final"] == 0.987654


def test_upsert_score_reemplaza_no_acumula(conn):
    tid = db.upsert_tema(conn, _tema("e" * 64))
    base = {"demanda": 0.1, "final": 0.1, "m_demanda": 0.1, "m_diversidad": 0.1,
            "m_estrategia": 0.1, "novedad": 0.0, "relevancia": 0.0, "produccion": 0.0}
    db.upsert_score(conn, tid, "2026-09-16", base, "v0.1", 6)
    db.upsert_score(conn, tid, "2026-09-16", {**base, "final": 0.9}, "v0.1", 6)
    filas = conn.execute("SELECT final FROM scores WHERE tema_id = ?", (tid,)).fetchall()
    assert len(filas) == 1 and filas[0]["final"] == 0.9


def test_reemplazar_corrida_no_acumula(conn):
    """D-11: UNIQUE(fecha_corte); reejecutar reemplaza y las piezas caen en cascada."""
    fila = {"version_reglas": "v0.1", "hash_entradas": "h1", "top10_json": "[]",
            "fuentes_json": "[]", "creada_en": "2026-09-16", "estado_corrida": "completa"}
    cid = db.reemplazar_corrida(conn, "2026-09-16", False, fila)

    tid = db.upsert_tema(conn, _tema("f" * 64))
    conn.execute(
        "INSERT INTO piezas (tema_id, formato, angulo, angulo_metodo, estado, corrida_id,"
        " creada_en, pilar_decision)"
        " VALUES (?, 'corto', 'un angulo', 'plantilla', 'SELECCIONADA', ?, '2026-09-16', 'P1')",
        (tid, cid),
    )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) AS n FROM piezas").fetchone()["n"] == 1

    db.reemplazar_corrida(conn, "2026-09-16", False, {**fila, "hash_entradas": "h2"})
    assert conn.execute("SELECT COUNT(*) AS n FROM corridas").fetchone()["n"] == 1
    assert conn.execute("SELECT hash_entradas FROM corridas").fetchone()["hash_entradas"] == "h2"
    # Las piezas de la corrida vieja no quedan huerfanas: caen por CASCADE.
    assert conn.execute("SELECT COUNT(*) AS n FROM piezas").fetchone()["n"] == 0


def test_upsert_ingesta_idempotente(conn):
    r = {"fuente": "reddit:r/x", "resultado": "http_429", "n_senales": 0, "detalle": "reset=55"}
    db.upsert_ingesta(conn, "2026-09-16", r)
    db.upsert_ingesta(conn, "2026-09-16", {**r, "resultado": "ok", "n_senales": 7})
    filas = conn.execute("SELECT resultado, n_senales FROM ingestas").fetchall()
    assert len(filas) == 1 and filas[0]["resultado"] == "ok" and filas[0]["n_senales"] == 7


# ─── D-13 · evidencia obligatoria por transicion ────────────────────────────

def _pieza(conn) -> int:
    tid = db.upsert_tema(conn, _tema("9" * 64))
    cur = conn.execute(
        "INSERT INTO piezas (tema_id, formato, angulo, angulo_metodo, estado, creada_en,"
        " pilar_decision)"
        " VALUES (?, 'corto', 'un angulo', 'plantilla', 'SELECCIONADA', '2026-09-16', 'P1')",
        (tid,),
    )
    conn.commit()
    return int(cur.lastrowid)


def test_transicion_exige_detalle(conn):
    pid = _pieza(conn)
    with pytest.raises(db.TransicionInvalida) as err:
        db.transicionar(conn, pid, "SELECCIONADA", "INVESTIGANDO", "2026-09-16", "   ")
    assert "evidencia" in str(err.value)


def test_transicion_prohibida_levanta(conn):
    """Saltar etapas hacia adelante no se permite."""
    pid = _pieza(conn)
    with pytest.raises(db.TransicionInvalida) as err:
        db.transicionar(conn, pid, "SELECCIONADA", "PUBLICADA", "2026-09-16", "salto")
    assert "prohibida" in str(err.value)


def test_no_se_sale_de_fallida_permanente(conn):
    pid = _pieza(conn)
    with pytest.raises(db.TransicionInvalida):
        db.transicionar(conn, pid, "FALLIDA_PERMANENTE", "REINTENTO", "2026-09-16", "insistir")


def test_transicion_valida_escribe_evento_y_estado(conn):
    pid = _pieza(conn)
    db.transicionar(conn, pid, "SELECCIONADA", "INVESTIGANDO", "2026-09-16", "arranca fase 2")
    evento = conn.execute("SELECT * FROM eventos WHERE pieza_id = ?", (pid,)).fetchone()
    assert evento["a_estado"] == "INVESTIGANDO" and evento["detalle"] == "arranca fase 2"
    assert conn.execute("SELECT estado FROM piezas WHERE id = ?", (pid,)).fetchone()["estado"] == "INVESTIGANDO"


def test_evento_de_corrida_sin_pieza(conn):
    """Un hecho de corrida se registra sin fingir que es una transicion de estado."""
    db.registrar_evento(conn, "2026-09-16", "fuente_caida: reddit 429, reset=55")
    fila = conn.execute("SELECT * FROM eventos WHERE pieza_id IS NULL").fetchone()
    assert "fuente_caida" in fila["detalle"]
    assert fila["de_estado"] is None and fila["a_estado"] is None


def test_evento_de_corrida_exige_detalle(conn):
    with pytest.raises(db.TransicionInvalida):
        db.registrar_evento(conn, "2026-09-16", "  ")


def test_reintento_solo_vuelve_a_donde_fallo(conn):
    """Desde REINTENTO no se puede aterrizar en cualquier etapa (D-12).

    Sin esta comprobacion, un reintento podria saltar hasta APRENDIDA y
    saltarse todo el camino intermedio.
    """
    pid = _pieza(conn)
    db.transicionar(conn, pid, "SELECCIONADA", "INVESTIGANDO", "2026-09-16", "arranca")
    db.transicionar(conn, pid, "INVESTIGANDO", "FALLIDA", "2026-09-16", "sin fuentes")
    db.transicionar(conn, pid, "FALLIDA", "REINTENTO", "2026-09-16", "se reintenta")

    with pytest.raises(db.TransicionInvalida) as err:
        db.transicionar(conn, pid, "REINTENTO", "APRENDIDA", "2026-09-16", "salto")
    assert "INVESTIGANDO" in str(err.value)

    # Volver a donde fallo si se permite, y abandonar tambien.
    db.transicionar(conn, pid, "REINTENTO", "INVESTIGANDO", "2026-09-16", "segunda vuelta")
    assert conn.execute(
        "SELECT estado FROM piezas WHERE id = ?", (pid,)
    ).fetchone()["estado"] == "INVESTIGANDO"


# ─── RQ-24, RQ-53 · el paquete no arrastra imports pesados ──────────────────

def test_paquete_no_arrastra_torch():
    """Importar content_factory no puede costar los 53 s de MiniLM."""
    codigo = (
        "import sys; sys.path.insert(0, r'%s');"
        "import content_factory, content_factory.db, content_factory.config;"
        "pesados = [m for m in ('torch','sentence_transformers','yt_dlp') if m in sys.modules];"
        "print(','.join(pesados))" % RAIZ
    )
    r = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == ""


# ─── RQ-06 · artefactos locales fuera de git ────────────────────────────────

@pytest.mark.parametrize(
    "ruta",
    [
        "content_factory/content.db",
        "content_factory/content.test.db",
        "content_factory/raw/2026-09-16/reddit.xml",
        "content_factory/content.db-wal",
        "content_factory/content.db-shm",
    ],
)
def test_artefactos_locales_ignorados(ruta):
    r = subprocess.run(
        ["git", "check-ignore", "-q", ruta], cwd=RAIZ, capture_output=True, text=True
    )
    assert r.returncode == 0, "%s NO esta ignorado por git" % ruta


# ─── H-1 y H-2 · recuperación tras fallo ────────────────────────────────────
#
# Contrato cerrado por el operador el 2026-09-17, en
# `openspec/changes/content-factory-v0-topic-engine/registro-epistemico.md` §7:
#
#   H-1  REINTENTO sin evidencia suficiente -> error explícito. La ausencia de
#        evidencia NO autoriza una transición. No se infiere ni se elige un
#        valor por omisión.
#   H-2  el origen declarado se valida contra el estado persistido. El llamador
#        no tiene autoridad para declarar un pasado que la base no respalda.
#
# **DEUDA DE CABLEADO, declarada y no disimulada.** Aquí se prueba el CONTRATO
# de `transicionar`, no su alcanzabilidad desde producción: hoy tiene **cero
# llamadores productivos**, así que una prueba de cableado no cablearía nada. El
# primer cambio que introduzca un llamador productivo deberá añadir una prueba
# de integración que demuestre: punto de entrada -> `transicionar` -> guardas.


def _pieza_como_produccion(conn, estado="SELECCIONADA"):
    """Crea la pieza EXACTAMENTE como lo hace el motor: inserción directa.

    Es la diferencia que dejó pasar el defecto. Las nueve pruebas anteriores
    nacían por `transicionar`, lo que dejaba accidentalmente un evento con
    destino normal y hacía funcionar al lector equivocado.
    """
    conn.execute(
        "INSERT INTO temas (hash_titulo,titulo,pilar,intent,creado_en,estado,"
        "metodo_clasificacion) VALUES ('h-r','tema','P1','noticia','2026-09-16',"
        "'DESCUBIERTA','lexico')")
    conn.execute(
        "INSERT INTO corridas (fecha_corte,version_reglas,hash_entradas,top10_json,"
        "creada_en,sin_llm,estado_corrida) VALUES ('2026-09-16','v','h','{}',"
        "'2026-09-16',0,'completa')")
    cur = conn.execute(
        "INSERT INTO piezas (tema_id,formato,angulo,angulo_metodo,estado,corrida_id,"
        "creada_en,pilar_decision) VALUES (1,'corto','a','plantilla',?,1,'2026-09-16','P1')", (estado,))
    conn.commit()
    return cur.lastrowid


def test_reintento_vuelve_al_origen_con_nacimiento_productivo(conn):
    """1 · un solo ciclo, con la pieza nacida como en producción.

    ANTES del parche el lector devolvía `None` y la guarda no actuaba, aunque el
    origen estuviera registrado. Medido: esperado SELECCIONADA, obtenido None.
    """
    pid = _pieza_como_produccion(conn)
    db.transicionar(conn, pid, "SELECCIONADA", "FALLIDA", "2026-09-16", "se cayo")
    db.transicionar(conn, pid, "FALLIDA", "REINTENTO", "2026-09-16", "se reintenta")
    db.transicionar(conn, pid, "REINTENTO", "SELECCIONADA", "2026-09-16", "vuelve")
    assert conn.execute("SELECT estado FROM piezas WHERE id = ?",
                        (pid,)).fetchone()["estado"] == "SELECCIONADA"


def test_reintento_no_puede_saltar_a_otro_estado(conn):
    """2 · el salto se rechaza. ANTES quedaba permitido con un solo ciclo."""
    pid = _pieza_como_produccion(conn)
    db.transicionar(conn, pid, "SELECCIONADA", "FALLIDA", "2026-09-16", "se cayo")
    db.transicionar(conn, pid, "FALLIDA", "REINTENTO", "2026-09-16", "se reintenta")
    with pytest.raises(db.TransicionInvalida):
        db.transicionar(conn, pid, "REINTENTO", "INVESTIGANDO", "2026-09-16", "salto")
    assert conn.execute("SELECT estado FROM piezas WHERE id = ?",
                        (pid,)).fetchone()["estado"] == "REINTENTO"


def test_dos_ciclos_usan_el_origen_del_segundo_fallo(conn):
    """3 · se conserva porque HOY pasaba por la razón equivocada.

    Con dos ciclos el lector viejo acertaba de casualidad: el retorno del primer
    reintento dejaba un destino normal que satisfacía su consulta. Ahora acierta
    por construcción, y esta prueba lo sigue vigilando.
    """
    pid = _pieza_como_produccion(conn)
    for de, a in (("SELECCIONADA", "FALLIDA"), ("FALLIDA", "REINTENTO"),
                  ("REINTENTO", "SELECCIONADA"), ("SELECCIONADA", "INVESTIGANDO"),
                  ("INVESTIGANDO", "INVESTIGADA"), ("INVESTIGADA", "GUIONADA"),
                  ("GUIONADA", "FALLIDA"), ("FALLIDA", "REINTENTO")):
        db.transicionar(conn, pid, de, a, "2026-09-16", "paso")
    with pytest.raises(db.TransicionInvalida):
        db.transicionar(conn, pid, "REINTENTO", "SELECCIONADA", "2026-09-16", "primer origen")
    db.transicionar(conn, pid, "REINTENTO", "GUIONADA", "2026-09-16", "segundo origen")
    assert conn.execute("SELECT estado FROM piezas WHERE id = ?",
                        (pid,)).fetchone()["estado"] == "GUIONADA"


def test_origen_declarado_distinto_del_persistido_se_rechaza(conn):
    """4 · H-2, la prueba más discriminante junto con la 1.

    ANTES se aceptaba: una pieza en SELECCIONADA podía declarar origen GUIONADA,
    pasar todas las validaciones y acabar en GUIONADA, saltándose investigación
    y verificación, con todas las transiciones formalmente legales.
    """
    pid = _pieza_como_produccion(conn)
    with pytest.raises(db.TransicionInvalida) as exc:
        db.transicionar(conn, pid, "GUIONADA", "FALLIDA", "2026-09-16", "origen falso")
    assert "no en GUIONADA" in str(exc.value)
    # Rechazo sin NINGÚN efecto persistente: ni estado ni evento falso.
    assert conn.execute("SELECT estado FROM piezas WHERE id = ?",
                        (pid,)).fetchone()["estado"] == "SELECCIONADA"
    assert conn.execute("SELECT COUNT(*) AS n FROM eventos WHERE pieza_id = ?",
                        (pid,)).fetchone()["n"] == 0


def test_reintento_sin_historia_falla_explicitamente(conn):
    """5 · H-1. La ausencia de evidencia NO autoriza una transición.

    Y el error es DISTINGUIBLE: `HistorialInsuficiente`, subclase de
    `TransicionInvalida`, para que quien ya capturaba la general siga
    funcionando y quien necesite el caso pueda separarlo.
    """
    pid = _pieza_como_produccion(conn, estado="REINTENTO")
    with pytest.raises(db.HistorialInsuficiente):
        db.transicionar(conn, pid, "REINTENTO", "PUBLICADA", "2026-09-16", "sin historia")
    assert conn.execute("SELECT estado FROM piezas WHERE id = ?",
                        (pid,)).fetchone()["estado"] == "REINTENTO"
    assert conn.execute("SELECT COUNT(*) AS n FROM eventos WHERE pieza_id = ?",
                        (pid,)).fetchone()["n"] == 0


def test_el_error_de_historial_sigue_siendo_transicion_invalida(conn):
    """Quien capturaba la excepción general no se entera del cambio."""
    pid = _pieza_como_produccion(conn, estado="REINTENTO")
    with pytest.raises(db.TransicionInvalida):
        db.transicionar(conn, pid, "REINTENTO", "PUBLICADA", "2026-09-16", "sin historia")


def test_el_nacimiento_queda_fuera_de_alcance(conn):
    """6 · `de = None` no se toca. Pertenece al frente de D-12.

    Validar el nacimiento obliga a decidir cómo debe registrarse, y eso sigue
    abierto. Se declara como limitación conocida, no se resuelve en silencio.
    """
    pid = _pieza_como_produccion(conn, estado="DESCUBIERTA")
    db.transicionar(conn, pid, None, "SELECCIONADA", "2026-09-16", "nacimiento")
    assert conn.execute("SELECT estado FROM piezas WHERE id = ?",
                        (pid,)).fetchone()["estado"] == "SELECCIONADA"


def test_una_base_v1_se_rechaza_en_vez_de_rellenarse(tmp_path):
    """No se inventa `pilar_decision` para piezas ya escritas.

    Una base creada antes de v2 tiene `piezas` sin la columna. `CREATE TABLE IF
    NOT EXISTS` no la anadiria, y un `ALTER ... NOT NULL` exigiria un valor por
    omision que seria historia inventada. Se rechaza con un mensaje que dice por
    que, en vez de fallar mas tarde con un error que no explica nada.
    """
    ruta = tmp_path / "vieja.db"
    vieja = sqlite3.connect(str(ruta))
    vieja.execute(
        "CREATE TABLE piezas (id INTEGER PRIMARY KEY, tema_id INTEGER, formato TEXT,"
        " angulo TEXT, angulo_metodo TEXT, estado TEXT, creada_en TEXT)")
    vieja.commit()
    vieja.close()

    conn = sqlite3.connect(str(ruta))
    conn.row_factory = sqlite3.Row
    with pytest.raises(db.EsquemaDesactualizado) as exc:
        db.crear_esquema(conn)
    assert "pilar_decision" in str(exc.value)
    assert "inventar historia" in str(exc.value)
    conn.close()


# ─── Frente de senales · esquema v3 ─────────────────────────────────────────
#
# v3 anade `senales.publicada_canonica` y retira `ix_senales_ventana`.
#
# El indice se retira porque D-18 quedo SUSTITUIDA por evidencia: las dos
# consultas productivas hacen `SCAN senales` -filtran por
# `substr(COALESCE(...),1,10)`, que el indice no sirve- asi que nunca se uso.
#
# Y NO hay migracion implicita: una base v2 se rechaza SIN TOCARLA. Por eso
# tampoco hay un `DROP INDEX` automatico: una base nueva simplemente nace sin el
# indice, y una vieja no se modifica antes de rechazarla.

def _indices(conn) -> set[str]:
    return {f[0] for f in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'")}


def _columnas(conn, tabla) -> set[str]:
    return {f[1] for f in conn.execute("PRAGMA table_info(%s)" % tabla)}


def test_una_base_nueva_nace_v4_con_la_columna_y_sin_el_indice_muerto(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "nueva.db"))
    conn.row_factory = sqlite3.Row
    db.crear_esquema(conn)

    assert db.ESQUEMA_VERSION == 6
    assert conn.execute("SELECT MAX(version) FROM esquema_version").fetchone()[0] == 6
    assert "publicada_canonica" in _columnas(conn, "senales")
    assert "ix_senales_ventana" not in _indices(conn), (
        "el indice de D-18 no sirve a ninguna consulta productiva y no debe recrearse")
    conn.close()


def test_una_base_v2_se_rechaza_sin_modificarla(tmp_path):
    """Fail-closed de version: se rechaza y NO se toca ni un byte.

    La base v2 se simula con fidelidad: una v3 nueva a la que se le quita la
    columna y se le devuelve el indice viejo. Asi tiene exactamente la forma de
    una base anterior a este cambio.

    **La prueba de cero mutacion es por bytes del archivo**, no por conteo de
    tablas: si el rechazo llegara despues de cualquier sentencia de esquema -un
    `DROP INDEX`, un `CREATE`- el archivo cambiaria.
    """
    import hashlib

    ruta = tmp_path / "v2.db"
    conn = sqlite3.connect(str(ruta))
    conn.row_factory = sqlite3.Row
    db.crear_esquema(conn)
    conn.execute("ALTER TABLE senales DROP COLUMN publicada_canonica")
    conn.execute("CREATE INDEX ix_senales_ventana ON senales(COALESCE(publicada_en, detectada_en))")
    conn.execute("UPDATE esquema_version SET version = 2")
    conn.commit()
    conn.close()

    antes = hashlib.sha256(ruta.read_bytes()).hexdigest()

    conn = sqlite3.connect(str(ruta))
    conn.row_factory = sqlite3.Row
    esquema_antes = conn.execute("PRAGMA schema_version").fetchone()[0]
    with pytest.raises(db.EsquemaDesactualizado) as exc:
        db.crear_esquema(conn)
    esquema_despues = conn.execute("PRAGMA schema_version").fetchone()[0]
    indices = _indices(conn)
    conn.close()

    assert "publicada_canonica" in str(exc.value)
    assert esquema_despues == esquema_antes, "el rechazo llego despues de tocar el esquema"
    assert "ix_senales_ventana" in indices, "se retiro el indice de una base que debia rechazarse intacta"
    assert hashlib.sha256(ruta.read_bytes()).hexdigest() == antes, (
        "el archivo de la base v2 cambio: hubo una escritura antes del rechazo")
