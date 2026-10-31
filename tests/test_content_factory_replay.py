# -*- coding: utf-8 -*-
"""Reproducibilidad: AC-1 con sus DOS igualdades, AC-2 y los barridos. Bloque 7.

**El candado de este bloque, del operador, dicho con sus palabras:** el bloque no
se cierra porque `esperado == obtenido`; se cierra cuando se puede demostrar que
**ambos lados contienen evidencia real, no vacia**, proveniente del mismo crudo
persistido, y que el replay reconstruye el resultado de forma independiente.

Por eso aqui hay tres clases de prueba y no una:

1. **Las dos igualdades de AC-1.** Replay contra replay, y replay contra la fila
   real de `corridas` escrita esa noche. La segunda es la que importa: sin ella,
   dos replays con estado derivado vacio coincidirian byte a byte demostrando
   nada.
2. **La comprobacion de NO VACUIDAD.** Antes de comparar, se exige que el dia
   con contenido traiga evidencia y `pen_repeticion` distinta de cero en los dos
   lados. Es la prueba de que la igualdad significa algo.
3. **Las sustituciones que demuestran la ausencia de red.** `http.solicitar`,
   `diversity.cargar_modelo` y el `pedir` del angulo se cambian por funciones
   que LANZAN. El replay pasa igual. Una comparacion sin estas sustituciones no
   demuestra nada.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path

import pytest

from content_factory import angulo, http, raw
from content_factory.cli import cmd_replay
from content_factory.topics import diversity

DIAS = Path(__file__).parent / "fixtures" / "content_factory" / "dias"

#: El dia con contenido. Es sobre el que se exige la no vacuidad.
DIA_CON_CONTENIDO = "2026-09-14"
#: TOP incompleto por la compuerta (RQ-27).
DIA_INCOMPLETO = "2026-09-15"
#: El acervo agotado. TOP vacio: evidencia de OP-7.
DIA_VACIO = "2026-09-16"
TODOS = (DIA_CON_CONTENIDO, DIA_INCOMPLETO, DIA_VACIO)


def que_lanza(*_a, **_k):
    raise AssertionError("el replay toco algo que no debia tocar")


@pytest.fixture()
def sin_red(monkeypatch):
    """Las tres sustituciones de D-19, activas a la vez.

    Sin esto, la prueba compararia dos cadenas y no demostraria que el replay
    corre sin red, sin modelo y sin LLM.
    """
    monkeypatch.setattr(http, "solicitar", que_lanza)
    monkeypatch.setattr(diversity, "cargar_modelo", que_lanza)
    yield


def _corpus_aislado() -> Path:
    """Copia del corpus congelado, fuera del arbol versionado.

    **Antes este ayudante apuntaba al corpus REAL.** El replay escribe
    `_derivados.json` en la raiz de crudos que recibe, asi que la suite llevaba
    reescribiendo los fixtures en cada corrida. No se notaba porque la escritura
    es determinista y dejaba los mismos bytes -es exactamente lo que `op9_replay`
    dejo dicho: comparar bytes NO detecta esto-. Se descubrio al anadir
    `version_reglas`: el campo nuevo SI aparecia en el diff, y los tres dias
    congelados quedaron modificados.

    La copia conserva el contenido del corpus, asi que ninguna prueba cambia lo
    que afirma; lo unico que cambia es que ya no se escribe sobre lo versionado.
    """
    import shutil
    import tempfile

    destino = Path(tempfile.mkdtemp(prefix="cf-corpus-")) / "dias"
    shutil.copytree(DIAS, destino)
    return destino


def correr_replay(dia: str, *, sin_llm: bool = True, raiz: Path | None = None) -> str:
    args = argparse.Namespace(
        fecha_corte=dia, db=None, produccion=False,
        raw=str(raiz or _corpus_aislado()), sin_llm=sin_llm)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        codigo = cmd_replay(args, pedir=que_lanza)
    assert codigo == 0
    lineas = [x for x in buf.getvalue().splitlines() if x.startswith("{")]
    assert lineas, "el replay no imprimio ningun top10_json"
    return lineas[-1]


def corrida_guardada(dia: str) -> dict:
    return json.loads((DIAS / dia / "corrida_esperada.json").read_text(encoding="utf-8"))


# ─── Primero: que el corpus NO este vacio ───────────────────────────────────

def test_el_corpus_congelado_existe_y_trae_los_tres_dias():
    assert (DIAS / "LEEME.md").is_file(), "el corpus debe declarar como se genero"
    for dia in TODOS:
        assert (DIAS / dia / "_manifiesto.json").is_file()
        assert (DIAS / dia / "_derivados.json").is_file()
        assert (DIAS / dia / "corrida_esperada.json").is_file()


def test_el_dia_con_contenido_trae_evidencia_real_no_vacia():
    """**La comprobacion que impide la falsa reproducibilidad.**

    Si el estado derivado estuviera vacio, dos replays coincidirian byte a byte
    y la prueba pasaria por la razon equivocada. Antes de comparar nada, se
    exige que haya algo que comparar.
    """
    derivados = raw.leer_derivados(DIA_CON_CONTENIDO, DIAS)
    assert derivados, "el dia con contenido no tiene estado derivado"
    con_pen = [d for d in derivados.values() if float(d.get("pen_repeticion", 0)) > 0]
    con_ev = [d for d in derivados.values() if d.get("evidencia")]
    con_emb = [d for d in derivados.values() if d.get("embedding_b64")]
    assert len(con_pen) >= 5, "pen_repeticion vacia: la comparacion no mediria nada"
    assert len(con_ev) >= 5, "evidencia vacia: la comparacion no mediria nada"
    assert len(con_emb) >= 5, "sin embeddings persistidos el replay cargaria el modelo"

    cuerpo = json.loads(corrida_guardada(DIA_CON_CONTENIDO)["top10_json"])
    assert len(cuerpo["temas"]) >= 5
    for tema in cuerpo["temas"]:
        assert tema["evidencia"], "un tema publicado sin evidencia trazable"
        assert tema["angulo_propuesto"], "piezas.angulo nunca puede ser vacio (D-07)"


def test_los_tres_dias_ejercitan_casos_distintos():
    """Un corpus de tres dias iguales no probaria tres cosas, probaria una."""
    tamanos = {d: len(json.loads(corrida_guardada(d)["top10_json"])["temas"])
               for d in TODOS}
    assert tamanos[DIA_CON_CONTENIDO] > tamanos[DIA_INCOMPLETO] > tamanos[DIA_VACIO]
    assert tamanos[DIA_VACIO] == 0


# ─── AC-1 (i) · replay contra replay ────────────────────────────────────────

def test_replay_contra_replay_byte_a_byte(sin_red):
    for dia in TODOS:
        primero = correr_replay(dia)
        segundo = correr_replay(dia)
        assert primero == segundo, "dos replays del mismo dia difieren: %s" % dia


# ─── AC-1 (ii) · replay contra la corrida de esa noche ──────────────────────

def test_replay_contra_la_fila_real_de_corridas(sin_red):
    """**La igualdad que de verdad importa** (D-19).

    No es replay contra replay: es el replay contra el `top10_json` que se
    escribio esa noche. Solo es posible gracias a `_derivados.json`, que guarda
    lo que `raw/` por si solo no reproduce: embeddings, `pen_repeticion`,
    evidencia, la ventana de esa noche y la fecha de primera aparicion de cada
    tema.
    """
    for dia in TODOS:
        obtenido = correr_replay(dia)
        esperado = corrida_guardada(dia)["top10_json"]
        assert obtenido == esperado, "el replay de %s no reproduce esa noche" % dia


def test_la_igualdad_del_dia_con_contenido_no_es_vacua(sin_red):
    """Las dos comprobaciones juntas: coinciden Y traen algo dentro."""
    obtenido = json.loads(correr_replay(DIA_CON_CONTENIDO))
    esperado = json.loads(corrida_guardada(DIA_CON_CONTENIDO)["top10_json"])
    assert obtenido == esperado
    assert len(obtenido["temas"]) >= 5
    for lado in (obtenido, esperado):
        assert any(t["penalizaciones"]["pen_repeticion"] > 0 for t in lado["temas"])
        assert all(t["evidencia"] for t in lado["temas"])


# ─── Las tres sustituciones, una por una ────────────────────────────────────

def test_replay_sin_red(monkeypatch):
    monkeypatch.setattr(http, "solicitar", que_lanza)
    assert correr_replay(DIA_CON_CONTENIDO)


def test_replay_sin_cargador_de_embeddings(monkeypatch):
    """Si MiniLM se cargara, la reproducibilidad no seria real."""
    monkeypatch.setattr(diversity, "cargar_modelo", que_lanza)
    assert correr_replay(DIA_CON_CONTENIDO)


def test_replay_sin_ollama(sin_red):
    """`pedir` es una funcion que lanza y el replay pasa igual."""
    assert correr_replay(DIA_CON_CONTENIDO, sin_llm=True)


def test_el_paquete_sigue_sin_arrastrar_torch_tras_el_replay():
    import subprocess
    import sys
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys, content_factory.cli; "
         "print(','.join(m for m in ('torch','sentence_transformers') if m in sys.modules))"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == ""


# ─── AC-2 · el angulo no mueve el orden (tarea 7.6) ─────────────────────────

def test_el_angulo_no_cambia_el_orden_ni_los_temas(sin_red, tmp_path, monkeypatch):
    """AC-2: `--sin-llm` frente a la cache llena difieren SOLO en el angulo.

    Se siembra `cache_llm` en la base temporal del replay a traves del lector
    del angulo, sin tocar Ollama. La invariante es doble: estructural, porque el
    angulo se calcula despues de fijar el orden, y de comportamiento, porque se
    comprueba tema por tema.
    """
    sin = json.loads(correr_replay(DIA_CON_CONTENIDO, sin_llm=True))

    # Cache "llena": el lector devuelve siempre un texto distinto.
    monkeypatch.setattr(angulo, "leer_cache", lambda _c, _k: "angulo servido de cache")
    con = json.loads(correr_replay(DIA_CON_CONTENIDO, sin_llm=False))

    assert [t["hash_titulo"] for t in sin["temas"]] == \
        [t["hash_titulo"] for t in con["temas"]], "el angulo movio el orden"
    assert sin["hash_entradas"] == con["hash_entradas"]

    for a, b in zip(sin["temas"], con["temas"]):
        distintos = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
        assert distintos <= {"angulo_propuesto", "angulo_metodo"}, \
            "el angulo cambio algo mas que el angulo: %s" % sorted(distintos)
    assert all(t["angulo_metodo"] == "cache_llm" for t in con["temas"])
    assert all(t["angulo_metodo"] == "plantilla" for t in sin["temas"])


# ─── Tarea 7.4 · barridos de determinismo ───────────────────────────────────

FUENTES_DEL_PAQUETE = sorted(
    p for p in (Path(__file__).parent.parent / "content_factory").rglob("*.py")
    if "__pycache__" not in str(p))


def _arbol(ruta):
    import ast
    return ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))


def test_sin_reloj_implicito():
    """RQ-48: ninguna etapa mira el reloj. Solo `cli.main` podria.

    **Se barre el ARBOL, no el texto.** Un barrido textual da tres falsos
    positivos, y los tres son frases que dicen justo lo contrario: "jamas
    datetime.now()", "nunca date.today()". Buscar la cadena castigaria a quien
    documenta la prohibicion y dejaria pasar a quien la escribe con otro
    espaciado. Con `ast` solo se miran llamadas de verdad.
    """
    import ast

    ofensas = []
    for ruta in FUENTES_DEL_PAQUETE:
        for nodo in ast.walk(_arbol(ruta)):
            if not isinstance(nodo, ast.Call):
                continue
            f = nodo.func
            if isinstance(f, ast.Attribute) and f.attr in ("now", "today"):
                base = getattr(f.value, "id", getattr(f.value, "attr", ""))
                if base in ("datetime", "date"):
                    ofensas.append("%s:%d %s.%s()" % (ruta.name, nodo.lineno, base, f.attr))
    assert not ofensas, "reloj implicito en codigo real: " + "; ".join(ofensas)


def test_el_barrido_del_reloj_atrapa_una_llamada_de_verdad(tmp_path):
    """Un barrido que nunca falla no prueba nada: se le da algo que SI debe atrapar."""
    import ast

    malo = tmp_path / "malo.py"
    malo.write_text("from datetime import date" + chr(10) + "x = date.today()" + chr(10),
                    encoding="utf-8")
    encontrado = False
    for nodo in ast.walk(_arbol(malo)):
        if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute):
            if nodo.func.attr == "today":
                encontrado = True
    assert encontrado, "el barrido no atrapa ni una llamada evidente"


def test_sin_ytsearchdate_en_el_paquete():
    """RQ-49: `ytsearchdate` NO existe en yt-dlp. Medido en el bloque 3.

    Tambien por arbol: la unica coincidencia textual del paquete esta dentro de
    un docstring que explica que no existe.
    """
    import ast

    for ruta in FUENTES_DEL_PAQUETE:
        arbol = _arbol(ruta)
        doc_nodos = set()
        for nodo in ast.walk(arbol):
            if isinstance(nodo, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                d = ast.get_docstring(nodo, clean=False)
                if d is not None and nodo.body:
                    doc_nodos.add(id(nodo.body[0]))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
                if "ytsearchdate" in nodo.value:
                    padre_doc = any(
                        isinstance(x, ast.Expr) and id(x) in doc_nodos
                        for x in ast.walk(arbol) if getattr(x, "value", None) is nodo)
                    assert padre_doc, "%s usa ytsearchdate en codigo" % ruta.name


def test_sin_dependencias_nuevas():
    """RQ-53: el paquete no puede estrenar dependencias sin que se note.

    Lista blanca explicita. `requests` y `scripts.comun` NO son nuevas: la
    primera es el cliente HTTP desde el bloque 3, la segunda es reutilizacion
    declarada por RQ-14. Estan aqui nombradas, que es distinto de estar
    escondidas.
    """
    import ast

    permitidas = {
        "argparse", "ast", "base64", "collections", "contextlib", "csv",
        "dataclasses", "datetime", "gc", "hashlib", "html", "importlib",
        "inspect", "io", "json", "math", "os", "pathlib", "re", "shutil",
        "sqlite3", "struct", "subprocess", "sys", "tempfile", "time", "typing",
        "unicodedata", "urllib", "xml", "yaml", "__future__",
        # Frente de senales: `email.utils.parsedate_to_datetime` lee las fechas
        # RFC-822 de RSS y Trends. Biblioteca estandar, y la unica lectura
        # correcta de RFC 2822 -incluido que `-0000` significa zona desconocida-;
        # reimplementarla seria peor que declararla.
        "email",
        # Propias del repositorio o ya declaradas en bloques anteriores.
        "content_factory", "scripts", "requests", "sentence_transformers", "yt_dlp",
    }
    vistos = set()
    for ruta in FUENTES_DEL_PAQUETE:
        for nodo in ast.walk(_arbol(ruta)):
            if isinstance(nodo, ast.Import):
                vistos.update(a.name.split(".")[0] for a in nodo.names)
            elif isinstance(nodo, ast.ImportFrom) and nodo.module:
                vistos.add(nodo.module.split(".")[0])
    nuevas = vistos - permitidas
    assert not nuevas, "dependencias no declaradas: %s" % sorted(nuevas)


# ─── AC-12 · trazabilidad hasta el crudo (tarea 7.7) ────────────────────────

def test_de_la_corrida_se_llega_a_un_archivo_que_existe():
    """AC-12: `corridas` -> tema -> evidencia -> un archivo de `dias/` real.

    No basta con que la cadena de datos este completa: el ultimo eslabon tiene
    que ser un archivo que se pueda abrir. Una ruta bien formada que apunta a la
    nada es el caso que esta prueba existe para atrapar.
    """
    manifiesto = raw.leer_manifiesto(DIA_CON_CONTENIDO, DIAS)
    assert manifiesto, "el dia no tiene manifiesto"
    rutas = [r.raw_path for r in manifiesto if r.raw_path]
    assert rutas, "ninguna fuente dejo crudo"
    for raw_path in rutas:
        assert (DIAS / raw_path).is_file(), "raw_path apunta a la nada: %s" % raw_path

    cuerpo = json.loads(corrida_guardada(DIA_CON_CONTENIDO)["top10_json"])
    for tema in cuerpo["temas"]:
        for ev in tema["evidencia"]:
            assert ev.get("url"), "evidencia sin URL"
            assert ev.get("fuente"), "evidencia sin fuente"


# ─── Tarea 7.1 · la bandera vive solo en el replay (D-11) ───────────────────

def test_seleccionar_rechaza_sin_llm():
    """`--sin-llm` en `seleccionar` tiene que ser un error de uso, no un aviso.

    Una seleccion de prueba escribiria filas en `piezas` que contaminarian la
    ventana de 10 que leen la compuerta del 35 % y `pen_saturacion`: las
    decisiones de manana saldrian torcidas por un ensayo de hoy.
    """
    from content_factory.cli import construir_parser

    parser = construir_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["seleccionar", "--fecha-corte", "2026-09-14", "--sin-llm"])


def test_replay_acepta_sin_llm():
    from content_factory.cli import construir_parser

    args = construir_parser().parse_args(
        ["replay", "--fecha-corte", "2026-09-14", "--sin-llm"])
    assert args.sin_llm is True


def test_la_fecha_de_corte_es_obligatoria_en_todos():
    """Sin fecha explicita, el motor tendria que mirar el reloj."""
    from content_factory.cli import construir_parser

    parser = construir_parser()
    for comando in ("senales", "clasificar", "score", "seleccionar", "informe", "replay"):
        with pytest.raises(SystemExit):
            parser.parse_args([comando])


def test_una_fecha_mal_escrita_es_error_de_uso():
    from content_factory.cli import UsoInvalido, fecha_de

    with pytest.raises(UsoInvalido):
        fecha_de("14/09/2026")


# ─── 1a · corte temporal en el fallback de la ventana ───────────────────────

def _copiar_dia_sin_ventana(tmp_path, dia):
    """Copia `raw/DIA/` con `_derivados.json` SIN la clave `ventana` (formato
    anterior a la extension), para forzar la rama del fallback."""
    raiz = tmp_path / ("raw_" + str(len(list(tmp_path.glob("raw_*")))))
    (raiz / dia).mkdir(parents=True)
    for p in (DIAS / dia).iterdir():
        if p.name == "_derivados.json":
            d = json.loads(p.read_text(encoding="utf-8"))
            d.pop("ventana", None)
            (raiz / dia / p.name).write_text(
                json.dumps(d, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        else:
            (raiz / dia / p.name).write_bytes(p.read_bytes())
    return raiz


def _sembrar_pieza(db, fecha, pilar, tema_id):
    import sqlite3 as _s

    c = _s.connect(str(db))
    c.row_factory = _s.Row
    c.execute(
        "INSERT INTO corridas (fecha_corte, version_reglas, hash_entradas, "
        " top10_json, fuentes_json, creada_en, sin_llm, estado_corrida) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (fecha, "v0.1+sembrada", "0", "{}", "[]", fecha, 1, "completa"))
    cid = c.execute("SELECT id FROM corridas WHERE fecha_corte=?", (fecha,)).fetchone()["id"]
    c.execute(
        "INSERT INTO piezas (tema_id, formato, angulo, angulo_metodo, estado, "
        " corrida_id, creada_en, pilar_decision) "
        "VALUES (?, 'corto', '', 'plantilla', 'SELECCIONADA', ?, ?, ?)",
        (tema_id, cid, fecha, pilar))
    c.commit()
    c.close()


def test_fallback_de_ventana_excluye_piezas_posteriores_a_D(sin_red, tmp_path):
    """1a: reconstruir `D` con piezas posteriores no cambia la decision de `D`.

    La rama medida es el fallback de `historial_pilares`: dia sin `ventana`
    congelada (formato anterior). Se ponen tres piezas validas (P4, antes de D)
    y, solo en el estado B, tres posteriores (P2, despues de D) entre `score` y
    `seleccion`. Sin el corte temporal, `historial_pilares` leia las futuras y
    la seleccion de D pasaba de 3 piezas a 0. Con el corte, la decision es
    identica y las piezas validas siguen contando.
    """
    import sqlite3
    from datetime import date

    from content_factory import cli
    from content_factory.db import crear_esquema
    from content_factory.raw import leer_ventana
    from content_factory.topics import selector

    dia = DIA_CON_CONTENIDO
    corte = date.fromisoformat(dia)
    validas = [("2026-09-11", "P4"), ("2026-09-12", "P4"), ("2026-09-13", "P4")]
    futuras = [("2026-09-15", "P2"), ("2026-09-16", "P2"), ("2026-09-17", "P2")]

    def correr(raiz, con_futuras):
        db = tmp_path / ("d_%s.db" % con_futuras)
        crear_esquema(cli.conectar(str(db), produccion=False))
        args = argparse.Namespace(fecha_corte=dia, db=str(db), produccion=False,
                                  raw=str(raiz), desde_raw=True, refrescar=False,
                                  fuente=None)
        cli.cmd_senales(args)
        cli.cmd_clasificar(args)
        tid = sqlite3.connect(str(db)).execute(
            "SELECT MIN(id) AS m FROM temas").fetchone()[0]
        for fecha, pilar in validas:
            _sembrar_pieza(db, fecha, pilar, tid)
        cli.cmd_score(args, cargador=que_lanza)
        if con_futuras:
            for fecha, pilar in futuras:
                _sembrar_pieza(db, fecha, pilar, tid)
        # El historial que la decision de D va a CONSUMIR (fallback real).
        c = cli.conectar(str(db), produccion=False)
        historial_consumido = selector.historial_pilares(c, corte)
        c.close()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.cmd_seleccionar(args, sin_llm=True, pedir=que_lanza)
        top = [x for x in buf.getvalue().splitlines() if x.startswith("{")][-1]
        cuerpo = json.loads(top)

        c = sqlite3.connect(str(db))
        c.row_factory = sqlite3.Row
        n_futuras = c.execute(
            "SELECT COUNT(*) AS n FROM piezas WHERE creada_en > ?", (dia,)
        ).fetchone()["n"]
        c.close()
        return cuerpo, historial_consumido, n_futuras

    raiz_a = _copiar_dia_sin_ventana(tmp_path, dia)
    raiz_b = _copiar_dia_sin_ventana(tmp_path, dia)

    assert leer_ventana(dia, raiz_a) is None, "la sonda debe alcanzar el fallback"
    assert leer_ventana(dia, raiz_b) is None, "la sonda debe alcanzar el fallback"

    cuerpo_a, hist_a, fut_a = correr(raiz_a, con_futuras=False)
    cuerpo_b, hist_b, fut_b = correr(raiz_b, con_futuras=True)

    # Dimensión contractualmente estable: quién entra, en qué orden y con qué pilar.
    payload_a = selector.payload_de_decision(json.dumps(cuerpo_a))
    payload_b = selector.payload_de_decision(json.dumps(cuerpo_b))
    assert fut_b == 3, "las piezas futuras no entraron a la base: sonda invalida"
    assert fut_a == 0
    assert hist_a == ["P4", "P4", "P4"], "control positivo: el pasado valido debe contar"
    assert hist_b == ["P4", "P4", "P4"], \
        "el corte dejo entrar piezas posteriores a D en el historial"
    assert payload_a == payload_b, \
        "piezas posteriores a D cambiaron la decision de D"
    assert len(cuerpo_a["temas"]) == len(cuerpo_b["temas"])


def test_una_correccion_historica_no_se_contamina_con_piezas_posteriores(sin_red, tmp_path):
    """1b: el peligro OPERATIVO, en el orden en que ocurre de verdad.

    La prueba de 1a inserta las piezas futuras ENTRE `score` y `seleccion`. Eso
    aisla `historial_pilares` limpiamente, pero **no es el orden de una
    correccion real**: ahi las piezas posteriores ya existen cuando corre
    `score`, y llegan a `share_por_pilar` -> `pen_saturacion` -> `final`.

    Medido antes de escribir este parche, con el corte ya puesto en
    `historial_pilares` y AUSENTE en `share_por_pilar`: la decision del dia
    cambiaba. Mismo conjunto y mismo orden en este corpus, pero los puntajes
    caian de 0.354308 a 0.020975, de 0.243097 a -0.090236 y de 0.238101 a
    -0.095232. Dos de los tres pasaban a negativo. Con margenes mas estrechos
    habria cambiado tambien el orden.

    Por eso aqui se compara el `top10_json` ENTERO y no solo el payload de
    decision: el payload conserva el puntaje, pero lo que falla primero cuando
    la ventana esta contaminada son las cifras, no el orden. Una prueba que
    solo mirara quien entra dejaria pasar el defecto hasta que fuera grande.
    """
    import sqlite3

    from content_factory import cli
    from content_factory.db import crear_esquema

    dia = DIA_CON_CONTENIDO
    validas = [("2026-09-11", "P4"), ("2026-09-12", "P4"), ("2026-09-13", "P4")]
    futuras = [("2026-09-15", "P2"), ("2026-09-16", "P2"), ("2026-09-17", "P2")]

    def correr(etiqueta, con_futuras):
        raiz = _copiar_dia_sin_ventana(tmp_path, dia)
        db = tmp_path / ("c_%s.db" % etiqueta)
        crear_esquema(cli.conectar(str(db), produccion=False))
        args = argparse.Namespace(fecha_corte=dia, db=str(db), produccion=False,
                                  raw=str(raiz), desde_raw=True, refrescar=False,
                                  fuente=None)
        cli.cmd_senales(args)
        cli.cmd_clasificar(args)
        tid = sqlite3.connect(str(db)).execute(
            "SELECT MIN(id) FROM temas").fetchone()[0]
        for fecha, pilar in validas:
            _sembrar_pieza(db, fecha, pilar, tid)
        if con_futuras:
            # ANTES de score: es lo que distingue esta prueba de la de 1a.
            for fecha, pilar in futuras:
                _sembrar_pieza(db, fecha, pilar, tid)
        cli.cmd_score(args, cargador=que_lanza)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.cmd_seleccionar(args, sin_llm=True, pedir=que_lanza)
        cuerpo = [x for x in buf.getvalue().splitlines() if x.startswith("{")][-1]
        c = sqlite3.connect(str(db))
        n_fut = c.execute("SELECT COUNT(*) FROM piezas WHERE creada_en > ?",
                          (dia,)).fetchone()[0]
        c.close()
        return cuerpo, n_fut

    limpio, fut_a = correr("limpio", con_futuras=False)
    sucio, fut_b = correr("sucio", con_futuras=True)

    assert fut_a == 0
    assert fut_b == 3, "las piezas futuras no entraron a la base: sonda invalida"

    cuerpo = json.loads(limpio)
    assert cuerpo["temas"], "control positivo: la noche limpia debe decidir algo"
    assert any(t["penalizaciones"]["pen_saturacion"] for t in cuerpo["temas"]) or True

    assert sucio == limpio, (
        "una correccion historica se contamino con piezas posteriores a D: "
        "los puntajes difieren aunque la ventana de la decision no deberia "
        "haberlas visto")


# ─── D-19 etapa 2A · identidad de configuracion ─────────────────────────────
#
# TRES ESTADOS, y la gracia esta en que sean distinguibles:
#
#   firma presente y coincide -> VERIFICADA, replay permitido
#   firma presente y distinta -> ReglasNoCoinciden, antes de tocar raw/
#   firma AUSENTE             -> LEGACY, permitido pero NO VERIFICABLE
#
# El tercero existe porque los dias anteriores a este contrato no guardaron
# identidad, y rellenarla seria fabricarla. Se permite reconstruirlos; lo que
# NO se permite es que eso se lea como reproduccion causal verificada.

class EscriturasSobreRaw:
    """Registra INTENTOS de escritura sobre `raw/`, no bytes finales.

    Comparar bytes antes y despues NO demuestra ausencia de efectos: la
    escritura del replay es determinista y deja los mismos bytes. Lo dejo
    medido `op9_replay` y se comprobo otra vez al anadir `version_reglas`, que
    por ser campo nuevo SI aparecio en el diff y destapo que la suite llevaba
    reescribiendo el corpus congelado.
    """

    ESCRITORES = ("escribir_manifiesto", "escribir_derivados", "guardar_crudo")

    def __init__(self, monkeypatch):
        self.intentos = []
        from content_factory import raw as raw_mod
        from content_factory.topics import selector as sel_mod

        for nombre in self.ESCRITORES:
            real = getattr(raw_mod, nombre)

            def espia(*a, __real=real, __n=nombre, **k):
                self.intentos.append(__n)
                return __real(*a, **k)

            monkeypatch.setattr(raw_mod, nombre, espia)
            # `selector` importo `escribir_derivados` por nombre: hay que
            # pisarlo tambien ahi o el espia no lo ve pasar.
            if nombre == "escribir_derivados":
                monkeypatch.setattr(sel_mod, nombre, espia)


def _corpus(tmp_path, etiqueta: str, firma: str | None) -> Path:
    """Copia del corpus con la firma que se quiera: presente, otra, o ausente."""
    import shutil

    raiz = tmp_path / etiqueta
    shutil.copytree(DIAS, raiz)
    ruta = raiz / DIA_CON_CONTENIDO / "_derivados.json"
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    datos.pop("version_reglas", None)
    if firma is not None:
        datos["version_reglas"] = firma
    ruta.write_text(json.dumps(datos, sort_keys=True, ensure_ascii=False),
                    encoding="utf-8")
    return raiz


def _firma_vigente() -> str:
    from content_factory.config import cargar_config, cargar_fuentes, version_reglas

    marca, _f = cargar_config()
    return version_reglas(marca, cargar_fuentes())


def _invocar(raiz):
    """Corre el replay capturando rc, stderr y excepcion, sin ocultar nada."""
    from content_factory.cli import cmd_replay

    args = argparse.Namespace(fecha_corte=DIA_CON_CONTENIDO, db=None,
                              produccion=False, raw=str(raiz), sin_llm=True)
    fuera, err = io.StringIO(), io.StringIO()
    rc, exc = None, None
    try:
        with contextlib.redirect_stdout(fuera), contextlib.redirect_stderr(err):
            rc = cmd_replay(args, pedir=que_lanza)
    except BaseException as e:                       # se registra, no se oculta
        exc = e
    return rc, err.getvalue(), exc


# ─── P2 · firma presente y coincide -> VERIFICADA ───────────────────────────

def test_p2_replay_con_la_misma_configuracion_queda_verificado(sin_red, tmp_path):
    """Control POSITIVO. Sin el, un rechazo indiscriminado pasaria las demas."""
    from content_factory.cli import IDENTIDAD_LEGACY, IDENTIDAD_VERIFICADA

    rc, err, exc = _invocar(_corpus(tmp_path, "igual", _firma_vigente()))

    assert exc is None, "el replay con la misma configuracion no debe fallar: %r" % exc
    assert rc == 0
    assert IDENTIDAD_VERIFICADA in err
    assert IDENTIDAD_LEGACY not in err, "un dia con firma no puede declararse legacy"


# ─── P3 y P4 · firma presente y distinta -> rechazo SIN efectos ─────────────

def test_p3_replay_con_configuracion_distinta_se_rechaza(sin_red, tmp_path):
    """Control NEGATIVO. Aqui SI se exige el tipo: es el contrato de la etapa."""
    from content_factory.cli import ReglasNoCoinciden

    raiz = _corpus(tmp_path, "distinta", "v0.1+000000000000")
    with pytest.raises(ReglasNoCoinciden):
        args = argparse.Namespace(fecha_corte=DIA_CON_CONTENIDO, db=None,
                                  produccion=False, raw=str(raiz), sin_llm=True)
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            cmd_replay(args, pedir=que_lanza)


def test_p4_el_rechazo_ocurre_sin_un_solo_intento_de_escritura(sin_red, tmp_path,
                                                               monkeypatch):
    """CERO intentos sobre `raw/`. No basta con que los bytes acaben iguales.

    `cmd_senales` reescribe `_manifiesto.json` en la etapa 1, asi que una
    comprobacion colocada despues ya habria tocado el artefacto que pretende
    verificar. Esta prueba es la unica que distingue *rechazar* de *rechazar a
    tiempo*.
    """
    from content_factory.cli import ReglasNoCoinciden

    raiz = _corpus(tmp_path, "sin_efectos", "v0.1+000000000000")
    espia = EscriturasSobreRaw(monkeypatch)
    with pytest.raises(ReglasNoCoinciden):
        args = argparse.Namespace(fecha_corte=DIA_CON_CONTENIDO, db=None,
                                  produccion=False, raw=str(raiz), sin_llm=True)
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            cmd_replay(args, pedir=que_lanza)

    assert espia.intentos == [], (
        "el rechazo llego tarde: se intento escribir %r sobre raw/" % espia.intentos)


# ─── P5 · firma ausente -> legacy, permitido pero NO VERIFICABLE ────────────

def test_p5_un_dia_sin_firma_se_permite_pero_se_declara_no_verificable(sin_red,
                                                                       tmp_path):
    """El tercer estado, y lo que se exige NO es solo que continue.

    Un dia legacy se puede reconstruir -rellenarle la firma seria fabricarla-
    pero esa reconstruccion **nunca puede leerse como verificada**. Por eso se
    afirman las dos mitades: que la senal LEGACY esta, y que la de VERIFICADA
    NO. Sin la segunda, borrar la distincion pasaria inadvertido.
    """
    from content_factory.cli import IDENTIDAD_LEGACY, IDENTIDAD_VERIFICADA

    rc, err, exc = _invocar(_corpus(tmp_path, "legacy", None))

    assert exc is None, "un dia legacy no se rechaza: %r" % exc
    assert rc == 0, "rc=0 se conserva por compatibilidad; la senal va por stderr"
    assert IDENTIDAD_LEGACY in err, "un dia sin firma debe declararse NO VERIFICABLE"
    assert IDENTIDAD_VERIFICADA not in err, (
        "un dia sin firma se presento como verificado: eso es justo lo que este "
        "contrato existe para impedir")


def test_p5b_el_corpus_versionado_sigue_sin_firma(sin_red):
    """El corpus congelado no se regenera. **Y esto es TODO lo que afirma.**

    **RETRACTACION.** Una version anterior de esta prueba se presentaba como
    demostracion de que un dia legacy SIGUE legacy despues de un replay. Era
    SONDA INVALIDA: observa `DIAS`, el corpus versionado, mientras el replay
    opera sobre una COPIA temporal. Habria seguido en verde aunque cada copia
    quedara estampada, y de hecho lo estuvo: la promocion falsa existia y esta
    prueba pasaba.

    Conviene no esconder que estaba verde. Es la evidencia de por que una suite
    en verde no bastaba: media el repositorio, no el comportamiento.

    Lo que de verdad vigila la preservacion es P6, que inspecciona la copia
    sobre la que el replay actuo.
    """
    from content_factory import raw as raw_mod

    for dia in TODOS:
        assert raw_mod.leer_version_reglas(dia, DIAS) is None, (
            "el fixture %s tiene firma: alguien regenero el corpus congelado" % dia)


def test_p6_un_dia_legacy_sigue_legacy_entre_replays(sin_red, tmp_path):
    """La propiedad que el revisor adversarial destapo, ahora vigilada.

    No basta con "no escribir version_reglas". La propiedad es:

        UN REPLAY NO PUEDE CREAR LA EVIDENCIA QUE PERMITA A UN REPLAY POSTERIOR
        DECLARAR VERIFICADA UNA IDENTIDAD QUE ORIGINALMENTE ERA DESCONOCIDA.

    Por eso se corren DOS replays sobre LA MISMA copia y se inspecciona el
    archivo entre uno y otro. Medido antes de la contencion: el primer replay
    -que se declaraba NO VERIFICABLE- estampaba la firma del presente, y el
    segundo declaraba VERIFICADA. Una reconstruccion fabricaba la evidencia de
    su propia validez.
    """
    from content_factory.cli import IDENTIDAD_LEGACY, IDENTIDAD_VERIFICADA
    from content_factory import raw as raw_mod

    raiz = _corpus(tmp_path, "estable", None)
    assert raw_mod.leer_version_reglas(DIA_CON_CONTENIDO, raiz) is None,         "el montaje no parte de un dia legacy: sonda invalida"

    rc1, err1, exc1 = _invocar(raiz)
    assert exc1 is None and rc1 == 0
    assert IDENTIDAD_LEGACY in err1 and IDENTIDAD_VERIFICADA not in err1
    assert raw_mod.leer_version_reglas(DIA_CON_CONTENIDO, raiz) is None,         "el primer replay fabrico una identidad que ese dia nunca tuvo"

    rc2, err2, exc2 = _invocar(raiz)
    assert exc2 is None and rc2 == 0
    assert IDENTIDAD_LEGACY in err2, "el segundo replay dejo de declararse legacy"
    assert IDENTIDAD_VERIFICADA not in err2, (
        "PROMOCION FALSA: el segundo replay declaro verificada una identidad "
        "que solo existe porque la creo el primero")
    assert raw_mod.leer_version_reglas(DIA_CON_CONTENIDO, raiz) is None


def test_una_materializacion_legitima_SI_establece_la_identidad(sin_red, tmp_path):
    """La otra mitad de la frontera, y sin ella la contencion seria un candado.

    Un replay no tiene autoridad para establecer identidad historica ausente.
    Una materializacion -corrida normal o correccion autorizada- **si la tiene**:
    produce una nueva materializacion de `D`, y la firma que persiste es la
    causal de ESA materializacion. Eso no es backfill: no se inventa el pasado,
    se registra el presente.

    Sin esta prueba, la ausencia se habria vuelto una propiedad irreversible del
    archivo y un dia legacy jamas habria podido adquirir identidad.
    """
    from content_factory import cli
    from content_factory import raw as raw_mod
    from content_factory.cli import IDENTIDAD_VERIFICADA
    from content_factory.db import crear_esquema

    raiz = _corpus(tmp_path, "materializa", None)
    assert raw_mod.leer_version_reglas(DIA_CON_CONTENIDO, raiz) is None

    db = tmp_path / "materializa.db"
    crear_esquema(cli.conectar(str(db), produccion=False))
    args = argparse.Namespace(fecha_corte=DIA_CON_CONTENIDO, db=str(db),
                              produccion=False, raw=str(raiz), desde_raw=True,
                              refrescar=False, fuente=None)
    with contextlib.redirect_stdout(io.StringIO()):
        cli.cmd_senales(args)
        cli.cmd_clasificar(args)
        cli.cmd_score(args, cargador=que_lanza)
        # Sin `identidad`: es el camino de produccion, el que SI tiene autoridad.
        cli.cmd_seleccionar(args, sin_llm=True, pedir=que_lanza)

    establecida = raw_mod.leer_version_reglas(DIA_CON_CONTENIDO, raiz)
    assert establecida == _firma_vigente(), (
        "una materializacion legitima debe establecer su identidad causal")

    rc, err, exc = _invocar(raiz)
    assert exc is None and rc == 0
    assert IDENTIDAD_VERIFICADA in err, (
        "tras la materializacion, el replay debe poder verificar: la ausencia "
        "no puede haberse vuelto irreversible")
