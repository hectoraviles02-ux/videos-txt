# -*- coding: utf-8 -*-
"""La autoridad para sustituir una decision ya materializada. D-11.

**Por que existe este archivo.** El contrato y el comportamiento de la
sustitucion estaban demostrados por sondas congeladas que construian la llamada
a mano. La suite no mencionaba `corregir`, `DecisionYaMaterializada` ni
`CorreccionInvalida` en ninguna linea, y el parser no exponia las banderas: la
ruta existia y era inalcanzable desde afuera. Peor: **la guarda de reejeccion
ordinaria no tenia regresion**, asi que borrarla entera dejaba la suite en
verde. Es la segunda ocurrencia de la deuda de cableado, la misma que
`transicionar` ya tenia declarada.

**Las dos fronteras se prueban por separado, y no son la misma prueba.**

1. `cmd_seleccionar` es la frontera de autoridad: ahi SI se exige el tipo de
   excepcion, porque ese es su contrato.
2. `main` es el llamador, y **que observa el llamador sigue INDETERMINADO**.
   Hoy la excepcion se propaga porque `main` no la captura, y eso es estado de
   implementacion, no contrato; se resuelve cuando exista el contrato de la
   tarea programada (OP-4). Por eso las pruebas de nivel CLI no exigen que la
   excepcion se propague: `rechazo_del_dominio` acepta igual que el llamador la
   traduzca en un codigo de retorno distinto de cero.

**Lo que esta agnosticidad NO alcanza, dicho aqui en vez de callarse.** Toda
propiedad que exija "la invocacion no paso en silencio" descarta una de las
resoluciones posibles de OP-4: **devolver la decision existente con codigo 0**.
No es evitable —quitar esa exigencia dejaria pasar un exito silencioso, que es
peor— asi que queda excluida POR DECLARACION y no por accidente. Si OP-4 elige
esa salida, estas dos pruebas deben cambiar, y este parrafo es el aviso de que
cambiarlas entonces no sera ceder ante el rojo.

**Y lo que la agnosticidad no debe costar** es distinguir la guarda de un fallo
cualquiera. Se midio: con la guarda lanzando un `RuntimeError` ajeno en vez de
su tipo, la version anterior de estas pruebas seguia en verde. Por eso
`rechazo_del_dominio` recibe el tipo esperado.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from content_factory import raw
from content_factory.cli import (
    DecisionYaMaterializada,
    _cargador_que_lanza,
    _pedir_que_lanza,
    _reconstruir_ingestas,
    cmd_clasificar,
    cmd_score,
    cmd_seleccionar,
    cmd_senales,
    construir_parser,
    main,
)
from content_factory.db import CorreccionInvalida, crear_esquema
from content_factory.topics.selector import identidad_de_decision

DIAS = Path(__file__).parent / "fixtures" / "content_factory" / "dias"
DIA = "2026-09-14"
MOTIVO = "la fuente de la posicion 2 publico una correccion"


# ─── utilidades de estado ───────────────────────────────────────────────────

def _abrir(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


def decision(db: Path) -> str:
    """El `top10_json` tal cual quedo en la base. Bytes, no resumen."""
    conn = _abrir(db)
    fila = conn.execute(
        "SELECT top10_json FROM corridas WHERE fecha_corte = ?", (DIA,)).fetchone()
    conn.close()
    assert fila is not None, "no hay decision materializada para %s" % DIA
    return fila[0]


def identidad(db: Path) -> str:
    return identidad_de_decision(decision(db))


def conteos(db: Path) -> tuple[int, int]:
    conn = _abrir(db)
    n_cor = conn.execute("SELECT COUNT(*) FROM corridas").fetchone()[0]
    n_pz = conn.execute("SELECT COUNT(*) FROM piezas").fetchone()[0]
    conn.close()
    return int(n_cor), int(n_pz)


def huerfanas(db: Path) -> int:
    """Piezas cuya corrida ya no existe. Con CASCADE viva siempre son cero.

    `conteos` no las ve: una pieza huerfana sigue contando en `COUNT(*)`. Sin
    esta consulta, una CASCADE rota tras una correccion pasaria inadvertida.
    """
    conn = _abrir(db)
    n = conn.execute(
        "SELECT COUNT(*) FROM piezas p LEFT JOIN corridas r ON r.id = p.corrida_id "
        "WHERE r.id IS NULL").fetchone()[0]
    conn.close()
    return int(n)


def receipts(db: Path) -> list[sqlite3.Row]:
    """Los receipts de correccion. `motivo` no nulo es lo que los distingue."""
    conn = _abrir(db)
    filas = conn.execute(
        "SELECT identidad_anterior, identidad_nueva, motivo FROM eventos "
        "WHERE motivo IS NOT NULL ORDER BY id ASC").fetchall()
    conn.close()
    return list(filas)


class Invocacion:
    """Lo que de verdad hizo una llamada: codigo de retorno y excepcion.

    Se registra la excepcion en vez de dejarla salir porque el nivel CLI no
    afirma que se propague. Ver el encabezado del modulo.
    """

    def __init__(self, codigo, excepcion):
        self.codigo, self.excepcion = codigo, excepcion

    def rechazo_del_dominio(self, tipo) -> bool:
        """El rechazo vino del dominio, sin decidir COMO lo observa el llamador.

        Tres ramas, y cada una esta por una medicion:

        1. `SystemExit` -> False. Es argparse matando la linea antes de que la
           invocacion llegue al dominio. Sin esta rama, la prueba de correccion
           sin motivo pasaba aunque `--corregir` no existiera en el parser:
           medido, con las banderas retiradas seguia en verde mientras las otras
           tres caian.
        2. Excepcion de OTRO tipo -> False. Sin esto, un fallo cualquiera se
           confundia con la guarda: medido, cambiando el tipo lanzado por un
           `RuntimeError` ajeno, la version anterior de estas pruebas seguia en
           verde y solo caia la de nivel etapa.
        3. Sin excepcion -> se exige codigo distinto de cero. Es la rama que
           deja viva la resolucion de OP-4 en la que el llamador captura y
           traduce. La que NO deja viva es devolver la decision existente con
           codigo 0, y eso esta declarado en el encabezado.
        """
        if isinstance(self.excepcion, SystemExit):
            return False
        if self.excepcion is not None:
            return isinstance(self.excepcion, tipo)
        return self.codigo not in (0, None)


def invocar_cli(*argv) -> Invocacion:
    codigo, exc = None, None
    try:
        codigo = main(list(argv))
    except BaseException as e:                      # se registra, no se oculta
        exc = e
    return Invocacion(codigo, exc)


# ─── la noche montada ───────────────────────────────────────────────────────

@pytest.fixture()
def noche(tmp_path, monkeypatch):
    """Un dia real llevado hasta `score`, con base que PERSISTE entre llamadas.

    Los crudos se copian a `tmp_path`: `cmd_seleccionar` escribe
    `_derivados.json` en la raiz de crudos, y correr esto sobre los fixtures
    versionados los mutaria y romperia AC-1 en silencio.

    `_cargador_que_lanza` y `_pedir_que_lanza` no son decoracion: si alguno
    salta, los vectores no vinieron de `_derivados.json` y estas pruebas
    estarian ejercitando un camino que no es el de produccion.
    """
    raiz = tmp_path / "dias"
    shutil.copytree(DIAS, raiz)

    db = tmp_path / "content.db"
    conn = _abrir(db)
    conn.execute("PRAGMA foreign_keys = ON")
    crear_esquema(conn)
    conn.close()

    args = argparse.Namespace(
        fecha_corte=DIA, db=str(db), produccion=False, raw=str(raiz),
        desde_raw=True, refrescar=False, fuente=None)

    cmd_senales(args)
    _reconstruir_ingestas(db, date.fromisoformat(DIA), raiz)
    cmd_clasificar(args)
    cmd_score(args, cargador=_cargador_que_lanza)

    # La CLI de `seleccionar` no tiene `--raw` a proposito: es una bandera de
    # replay (D-11). Para las pruebas de nivel CLI se redirige la raiz de
    # crudos, no se agrega superficie de produccion.
    monkeypatch.setattr(raw, "_base", lambda _=None: raiz)
    return args, db, raiz


def seleccionar(args, **extra):
    """Una etapa 4 con las sustituciones que mantienen la prueba offline."""
    llamada = argparse.Namespace(**{**vars(args), **extra})
    return cmd_seleccionar(llamada, sin_llm=True, pedir=_pedir_que_lanza)


def cambiar_el_orden(db: Path) -> None:
    """Reordena por `scores.final` para que la decision nueva DIFIERA.

    Sin esto, la correccion dejaria un receipt con A igual a B: existiria el
    recibo y no demostraria ninguna sustitucion. Medido: con esta mutacion la
    identidad cambia.
    """
    conn = _abrir(db)
    filas = conn.execute(
        "SELECT tema_id FROM scores WHERE fecha = ? ORDER BY final DESC",
        (DIA,)).fetchall()
    for i, fila in enumerate(filas):
        conn.execute("UPDATE scores SET final = ? WHERE tema_id = ? AND fecha = ?",
                     (0.1 + 0.01 * i, fila[0], DIA))
    conn.commit()
    conn.close()


# ─── Pieza 2 · la guarda tiene regresion, en las dos fronteras ──────────────

def test_la_reejecucion_ordinaria_no_sustituye_la_decision(noche):
    """La frontera de autoridad. Aqui SI se exige el tipo de excepcion.

    Esta es la prueba que faltaba: sin ella se podia borrar el bloque entero de
    la guarda en `cmd_seleccionar` y la suite seguia en verde.
    """
    args, db, _raiz = noche
    seleccionar(args)
    antes, conteo_antes = decision(db), conteos(db)
    assert conteo_antes[1] > 0, "la noche no materializo ninguna pieza"

    with pytest.raises(DecisionYaMaterializada):
        seleccionar(args)

    assert decision(db) == antes, "la reejecucion sustituyo la decision"
    assert conteos(db) == conteo_antes, "la reejecucion borro o duplico filas"
    assert receipts(db) == [], "una reejecucion ordinaria no es una correccion"


def test_desde_la_cli_la_reejecucion_ordinaria_no_sustituye(noche, monkeypatch):
    """El mismo invariante, alcanzado como lo alcanzaria un operador.

    **No se afirma que la excepcion se propague.** Eso es implementacion. Se
    afirma que la decision no cambio y que el rechazo vino de la guarda, no de
    argparse ni de un fallo cualquiera.
    """
    args, db, _raiz = noche
    monkeypatch.setattr("content_factory.cli.angulo.proponer",
                        lambda *a, **k: ("angulo de prueba", "plantilla"))

    primera = invocar_cli("seleccionar", "--fecha-corte", DIA, "--db", str(db))
    assert primera.codigo == 0 and primera.excepcion is None
    antes, conteo_antes = decision(db), conteos(db)

    segunda = invocar_cli("seleccionar", "--fecha-corte", DIA, "--db", str(db))

    assert segunda.rechazo_del_dominio(DecisionYaMaterializada), (
        "la reejecucion no fue rechazada por la guarda: codigo %r, excepcion %r"
        % (segunda.codigo, segunda.excepcion))
    assert decision(db) == antes, "la reejecucion sustituyo la decision"
    assert conteos(db) == conteo_antes
    assert receipts(db) == []


def test_la_cli_expone_corregir_y_motivo():
    """La prueba de ALCANZABILIDAD, la que habria atrapado el hueco.

    `cmd_seleccionar` lee las dos con `getattr(..., por_omision)`, asi que su
    ausencia en el parser no rompia nada: dejaba la correccion inalcanzable en
    silencio. Comprobar el comportamiento no basta si nadie puede invocarlo.
    """
    parser = construir_parser()

    con = parser.parse_args(
        ["seleccionar", "--fecha-corte", DIA, "--corregir", "--motivo", MOTIVO])
    assert con.corregir is True
    assert con.motivo == MOTIVO

    sin = parser.parse_args(["seleccionar", "--fecha-corte", DIA])
    assert sin.corregir is False
    assert sin.motivo == ""


def test_corregir_sin_motivo_lo_acepta_el_parser_y_lo_rechaza_el_dominio(noche):
    """Una sola autoridad sobre el motivo vacio, y no es argparse.

    Duplicar la exigencia en el parser partiria el contrato en dos sitios que
    pueden divergir, y la rama de `CorreccionInvalida` quedaria sin ejercitar:
    la ruta pareceria probada por el guardian de la puerta y nunca por quien de
    verdad decide.

    **La prueba recorre las DOS mitades en una sola corrida, y eso es el arreglo
    de un defecto propio.** Antes solo miraba el parser, mientras el nombre
    prometia tambien el dominio. Se midio: retirada la exigencia de motivo en
    `db.py`, esta prueba NO caia. Un nombre que reclama una autoridad que no
    ejerce es la misma especie que el archivo entero persigue, cometida en la
    etiqueta en vez de en la asercion.
    """
    _args, db, _raiz = noche

    # Mitad 1: la puerta deja pasar la linea incompleta.
    cli = construir_parser().parse_args(
        ["seleccionar", "--fecha-corte", DIA, "--corregir"])
    assert cli.corregir is True
    assert cli.motivo == ""

    # Mitad 2: el dominio la rechaza, y lo hace con SU tipo.
    seleccionar(_args)
    antes = decision(db)
    with pytest.raises(CorreccionInvalida):
        seleccionar(_args, corregir=cli.corregir, motivo=cli.motivo)
    assert decision(db) == antes
    assert receipts(db) == []


# ─── Pieza 3 · la correccion autorizada, alcanzable y con receipt ───────────

def test_la_correccion_autorizada_sustituye_y_deja_receipt(noche):
    """A -> B con motivo, UNA sola decision viva, y sin piezas acumuladas.

    **RETRACTACION.** Una version anterior de este docstring declaraba las tres
    aserciones de no acumulacion INDEMOSTRABLES, porque las dos sondas que se
    intentaron saltaban por una restriccion UNIQUE antes de llegar a ellas. Era
    falso, y lo falso eran las sondas: ambas corregian eligiendo LOS MISMOS
    temas, asi que la UNIQUE de `piezas` disparaba primero. Se dio por
    indemostrable una propiedad por no haber encontrado la sonda, que es el
    mismo error que declarar demostrado lo que no se midio, con el signo
    cambiado.

    Medido despues: retirando `ON DELETE CASCADE` de `piezas.corrida_id`, esta
    prueba CAE. Lo que sostiene la propiedad es el DELETE mas la cascada, no las
    UNIQUE. La seccion "Pieza 4" separa cada frontera y la hace caer sola.
    """
    args, db, _raiz = noche
    seleccionar(args)
    a = identidad(db)
    _corridas_a, piezas_a = conteos(db)
    assert piezas_a > 0, "la noche no materializo ninguna pieza"

    cambiar_el_orden(db)
    seleccionar(args, corregir=True, motivo=MOTIVO)
    b = identidad(db)

    assert a != b, "la correccion no cambio la decision: el receipt no probaria nada"
    assert conteos(db) == (1, piezas_a), (
        "la correccion acumulo en vez de sustituir: %r" % (conteos(db),))
    assert huerfanas(db) == 0, "quedaron piezas sin corrida tras la correccion"

    recibo = receipts(db)
    assert len(recibo) == 1, "una correccion, un receipt"
    assert recibo[0]["identidad_anterior"] == a, "el receipt no conserva la sustituida"
    assert recibo[0]["identidad_nueva"] == b
    assert recibo[0]["motivo"] == MOTIVO


def test_el_receipt_sobrevive_a_la_corrida_que_documenta(noche):
    """Guarda la identidad anterior POR VALOR, no como llave foranea.

    La corrida A es justo la que la correccion borra. Una llave quedaria rota
    en el momento en que hace falta leerla.
    """
    args, db, _raiz = noche
    seleccionar(args)
    a = identidad(db)

    cambiar_el_orden(db)
    seleccionar(args, corregir=True, motivo=MOTIVO)

    conn = _abrir(db)
    vivas = [identidad_de_decision(f[0])
             for f in conn.execute("SELECT top10_json FROM corridas").fetchall()]
    conn.close()
    assert a not in vivas, "la corrida A todavia existe: la prueba no demuestra nada"
    assert receipts(db)[0]["identidad_anterior"] == a


def test_la_correccion_desde_la_cli_deja_receipt(noche, monkeypatch):
    """La ruta completa desde la linea de comandos. Es la que estaba rota."""
    args, db, _raiz = noche
    monkeypatch.setattr("content_factory.cli.angulo.proponer",
                        lambda *a, **k: ("angulo de prueba", "plantilla"))

    assert invocar_cli("seleccionar", "--fecha-corte", DIA, "--db", str(db)).codigo == 0
    a = identidad(db)

    cambiar_el_orden(db)
    corregida = invocar_cli("seleccionar", "--fecha-corte", DIA, "--db", str(db),
                            "--corregir", "--motivo", MOTIVO)

    assert corregida.excepcion is None, "la correccion por CLI fallo: %r" % corregida.excepcion
    assert corregida.codigo == 0
    assert identidad(db) != a
    assert conteos(db)[0] == 1
    assert receipts(db)[0]["identidad_anterior"] == a
    assert receipts(db)[0]["motivo"] == MOTIVO


def test_una_correccion_sin_motivo_conserva_la_decision_y_no_deja_receipt(noche):
    """El rechazo ocurre ANTES de la primera escritura.

    `registrar_correccion` levanta dentro de la transaccion y antes del
    `DELETE`, asi que el rollback devuelve A entera. Se comprueban los bytes,
    no solo el conteo: una A recreada con las mismas filas pero distinto
    contenido pasaria el conteo y seria otra decision.
    """
    args, db, _raiz = noche
    seleccionar(args)
    antes, conteo_antes = decision(db), conteos(db)

    cambiar_el_orden(db)
    with pytest.raises(CorreccionInvalida):
        seleccionar(args, corregir=True, motivo="   ")

    assert decision(db) == antes, "A no quedo intacta tras el rechazo"
    assert conteos(db) == conteo_antes
    assert receipts(db) == [], "quedo receipt de una correccion que no ocurrio"


def test_sin_motivo_desde_la_cli_tampoco_sustituye(noche, monkeypatch):
    """Mismo invariante por la ruta del operador, sin afirmar la forma del aviso.

    Las dos exclusiones —argparse y el tipo ajeno— viven en
    `rechazo_del_dominio` y estan ahi por mediciones, no por prudencia. Ver su
    docstring.
    """
    args, db, _raiz = noche
    monkeypatch.setattr("content_factory.cli.angulo.proponer",
                        lambda *a, **k: ("angulo de prueba", "plantilla"))

    assert invocar_cli("seleccionar", "--fecha-corte", DIA, "--db", str(db)).codigo == 0
    antes, conteo_antes = decision(db), conteos(db)

    cambiar_el_orden(db)
    r = invocar_cli("seleccionar", "--fecha-corte", DIA, "--db", str(db), "--corregir")

    assert r.rechazo_del_dominio(CorreccionInvalida), (
        "el rechazo no vino del dominio: codigo %r, excepcion %r"
        % (r.codigo, r.excepcion))
    assert decision(db) == antes
    assert conteos(db) == conteo_antes
    assert receipts(db) == []


# ─── Pieza 4 · que sostiene de verdad la no acumulacion ─────────────────────
#
# **Esta seccion nace de una retractacion.** Se afirmo que las aserciones de no
# acumulacion eran INDEMOSTRABLES porque toda mutacion saltaba por una
# restriccion UNIQUE antes de llegar a ellas. Era falso, y lo falso era la
# sonda: las dos correcciones que se probaron elegian los MISMOS temas, asi que
# la UNIQUE de `piezas` disparaba primero. Con una correccion que elige temas
# DISTINTOS no dispara, y entonces si se ve quien sostiene la propiedad.
#
# Lo que sostiene cada mitad, medido y no inferido:
#
#   UNIQUE (fecha_corte) en corridas
#       -> una sola decision viva por fecha. SI la sostiene.
#
#   UNIQUE (tema_id, creada_en, formato) en piezas
#       -> solo impide duplicar LA MISMA pieza logica. NO impide acumular
#          piezas de temas distintos tras una correccion.
#
#   DELETE FROM corridas + ON DELETE CASCADE sobre piezas.corrida_id
#       -> esto es lo que de verdad retira las piezas de A antes de
#          materializar B. Rota la cascada: 3 piezas esperadas, 8 obtenidas.

def restricciones_unique(db: Path, tabla: str) -> list[list[str]]:
    """Las UNIQUE declaradas de una tabla, leidas de la base y no del DDL.

    Se leen con PRAGMA a proposito: una prueba que citara el texto del DDL
    verificaria que el archivo dice lo que dice, no que la base lo cumpla.
    """
    conn = _abrir(db)
    salida = []
    for idx in conn.execute("PRAGMA index_list(%s)" % tabla).fetchall():
        if idx["unique"] and idx["origin"] == "u":
            salida.append([r["name"] for r in
                           conn.execute("PRAGMA index_info(%s)" % idx["name"]).fetchall()])
    conn.close()
    return sorted(salida)


def sin_cascada(monkeypatch):
    """Desactiva las llaves foraneas en las conexiones que abre el motor.

    No toca el esquema: la CASCADE sigue declarada. Lo que se retira es su
    cumplimiento, que es exactamente el modo de falla que la propiedad teme.
    """
    import content_factory.cli as cli_mod
    import content_factory.db as db_mod

    real = db_mod.conectar

    def sin_fk(ruta=None, produccion=False):
        conn = real(ruta, produccion=produccion)
        conn.execute("PRAGMA foreign_keys = OFF")
        return conn

    monkeypatch.setattr(db_mod, "conectar", sin_fk)
    monkeypatch.setattr(cli_mod, "conectar", sin_fk)


def dejar_solo_otros_temas(db: Path) -> int:
    """Retira de candidatura a los temas ya elegidos. Devuelve cuantos quedan.

    Es lo que convierte la correccion en una que elige temas DISTINTOS. Sin
    esto, A y B comparten temas y la UNIQUE de `piezas` dispara antes de que se
    pueda observar nada: fue justo el error que invalido las dos sondas
    anteriores.
    """
    conn = _abrir(db)
    elegidos = [f[0] for f in conn.execute("SELECT tema_id FROM piezas").fetchall()]
    marcadores = ",".join("?" for _ in elegidos)
    conn.execute("DELETE FROM scores WHERE fecha = ? AND tema_id IN (%s)" % marcadores,
                 (DIA,) + tuple(elegidos))
    quedan = conn.execute(
        "SELECT COUNT(*) FROM scores WHERE fecha = ?", (DIA,)).fetchone()[0]
    conn.commit()
    conn.close()
    return int(quedan)


def test_una_sola_corrida_viva_la_sostiene_unique_fecha_corte(noche):
    """UNIQUE (fecha_corte). Se nombra la restriccion y se la hace disparar."""
    args, db, _raiz = noche
    assert ["fecha_corte"] in restricciones_unique(db, "corridas"), (
        "el esquema ya no declara UNIQUE (fecha_corte) en corridas")

    seleccionar(args)
    conn = _abrir(db)
    fila = conn.execute("SELECT * FROM corridas").fetchone()
    with pytest.raises(sqlite3.IntegrityError, match="fecha_corte"):
        conn.execute(
            "INSERT INTO corridas (fecha_corte, version_reglas, hash_entradas, "
            " top10_json, fuentes_json, creada_en, sin_llm, estado_corrida) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (fila["fecha_corte"], "otra", "otro", "{}", "[]", DIA, 0, "completa"))
    conn.close()


def test_la_unique_de_piezas_no_impide_acumular_temas_distintos(noche):
    """UNIQUE (tema_id, creada_en, formato). Y lo que NO cubre.

    Se nombra, se comprueba que existe, y se demuestra su alcance real: bloquea
    la misma terna y deja pasar otra. Una prueba que solo comprobara que existe
    invitaria a creer que sostiene la no acumulacion entera, que es justo la
    afirmacion que hubo que retractar.
    """
    args, db, _raiz = noche
    assert ["tema_id", "creada_en", "formato"] in restricciones_unique(db, "piezas")

    seleccionar(args)
    conn = _abrir(db)
    pieza = conn.execute("SELECT * FROM piezas LIMIT 1").fetchone()

    def insertar(tema_id):
        conn.execute(
            "INSERT INTO piezas (tema_id, formato, angulo, angulo_metodo, estado, "
            " corrida_id, creada_en, pilar_decision) VALUES (?,?,?,?,?,?,?,?)",
            (tema_id, pieza["formato"], "", "plantilla", pieza["estado"],
             pieza["corrida_id"], pieza["creada_en"], pieza["pilar_decision"]))

    with pytest.raises(sqlite3.IntegrityError, match="tema_id"):
        insertar(pieza["tema_id"])                     # misma terna: bloquea

    libre = conn.execute(
        "SELECT id FROM temas WHERE id NOT IN (SELECT tema_id FROM piezas) "
        "LIMIT 1").fetchone()
    assert libre is not None, "el corpus no deja ningun tema sin pieza"
    insertar(libre[0])                                 # otro tema: NO bloquea
    conn.commit()
    conn.close()


def test_la_no_acumulacion_la_sostiene_la_cascada_no_las_unique(noche):
    """La frontera real: DELETE FROM corridas + ON DELETE CASCADE.

    Se corrige eligiendo temas DISTINTOS, que es lo que saca de en medio a la
    UNIQUE de `piezas`. Con la cascada viva quedan solo las piezas de B.
    """
    args, db, _raiz = noche
    seleccionar(args)
    piezas_a = conteos(db)[1]
    quedan = dejar_solo_otros_temas(db)
    assert 0 < quedan < piezas_a, (
        "el montaje no crea un conjunto disjunto util: A=%d, candidatos=%d"
        % (piezas_a, quedan))

    seleccionar(args, corregir=True, motivo=MOTIVO)
    corridas_vivas, piezas_b = conteos(db)

    assert corridas_vivas == 1
    assert piezas_b == quedan, (
        "quedaron piezas de A tras la correccion: esperadas %d, hay %d"
        % (quedan, piezas_b))

    conn = _abrir(db)
    temas_con_pieza = {f[0] for f in
                       conn.execute("SELECT DISTINCT tema_id FROM piezas").fetchall()}
    vivos = {f[0] for f in
             conn.execute("SELECT tema_id FROM scores WHERE fecha = ?", (DIA,)).fetchall()}
    conn.close()
    assert temas_con_pieza <= vivos, "sobrevivio una pieza de un tema ya retirado"


def test_rota_la_cascada_la_correccion_acumula_en_silencio(noche, monkeypatch):
    """La misma correccion con la CASCADE desactivada. Es el contraste.

    Sin este caso, la prueba anterior no distinguiria "la cascada funciono" de
    "nunca hubo nada que borrar". Aqui se ve el numero, y se ve que **no salta
    ninguna excepcion**: es un modo de falla silencioso.
    """
    args, db, _raiz = noche
    seleccionar(args)
    piezas_a = conteos(db)[1]
    quedan = dejar_solo_otros_temas(db)

    sin_cascada(monkeypatch)
    seleccionar(args, corregir=True, motivo=MOTIVO)

    corridas_vivas, piezas_b = conteos(db)
    assert corridas_vivas == 1, "la UNIQUE de corridas sigue sosteniendo su mitad"
    assert piezas_b == piezas_a + quedan, (
        "se esperaba acumulacion de %d + %d, hubo %d" % (piezas_a, quedan, piezas_b))


def test_las_piezas_acumuladas_no_quedan_huerfanas_y_por_eso_no_se_ven(noche, monkeypatch):
    """Por que huerfanas() NO sirve como centinela de esto.

    Al borrar la corrida A, SQLite reutiliza su rowid para la corrida B. Las
    piezas de A conservan su `corrida_id` y quedan **reapuntadas a B** sin ser
    huerfanas nunca. El centinela devuelve cero y todo parece sano.

    Lo unico que delata la acumulacion es el CONTEO. Queda dicho aqui para que
    nadie use huerfanas() creyendo que cubre este caso: no lo cubre, y su cero
    es el cero de la razon equivocada.
    """
    args, db, _raiz = noche
    seleccionar(args)
    dejar_solo_otros_temas(db)
    sin_cascada(monkeypatch)
    seleccionar(args, corregir=True, motivo=MOTIVO)

    assert huerfanas(db) == 0, "si esto cambia, la reutilizacion de rowid dejo de darse"
    assert conteos(db)[1] > 0
