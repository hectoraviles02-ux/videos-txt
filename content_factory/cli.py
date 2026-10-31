# -*- coding: utf-8 -*-
"""Linea de comandos del motor de temas. El unico sitio que puede mirar el reloj.

Seis subcomandos, uno por etapa, mas el replay:

    senales -> clasificar -> score -> seleccionar -> informe
    replay  (reconstruye una noche pasada, sin red y sin modelo)

**`--fecha-corte` es obligatoria en todos.** No hay valor por omision y no se
toma del reloj: si lo hiciera, reejecutar la cadena al dia siguiente daria otro
resultado con el mismo insumo, y el replay seria imposible de comprobar.
`datetime.now()` **solo** puede aparecer en `main`, para la marca de tiempo del
registro, y ninguna etapa lo usa (RQ-48, barrido de la tarea 7.4).

**`--sin-llm` existe solo en `replay`** (D-11). `seleccionar` no la acepta, y no
es un descuido: una seleccion de prueba escribiria filas en `piezas` que
contaminarian la ventana de 10 que leen la compuerta del 35 % y
`pen_saturacion`. Las decisiones de manana saldrian torcidas por un ensayo de
hoy.

**La guarda de produccion pide las dos cosas** (D-03): sin banderas se usa la
base de prueba; escribir en la de produccion exige `--produccion` Y la ruta
correcta. Resolver solo por ruta dejaria pasar un error de tecleo.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

from content_factory import angulo, raw
from content_factory.config import cargar_config
from content_factory.db import (
    conectar,
    crear_esquema,
    insertar_senales,
    registrar_evento,
    upsert_ingesta,
    upsert_score,
    upsert_tema,
)
from content_factory.ingestion import (
    github_releases,
    hackernews,
    reddit,
    rss,
    trends_mx,
    youtube,
)
from content_factory.ingestion.base import EtagStore
from content_factory.topics import diversity, selector
from content_factory.topics.classifier import clasificar
from content_factory.topics.discovery import consolidar, deduplicar
from content_factory.topics.scorer import combinar, componentes_de, valores_para_persistir

#: Que modulo atiende cada tipo de fuente. Tabla explicita: un despacho por
#: nombre de modulo seria mas corto y volveria invisible que aqui se decide algo.
POR_TIPO = {
    "trends_mx": trends_mx,
    "reddit": reddit,
    "hackernews": hackernews,
    "github_releases": github_releases,
    "rss": rss,
    "youtube": youtube,
}


class UsoInvalido(SystemExit):
    """Error de invocacion. Sale con codigo distinto de cero."""


def fecha_de(texto: str) -> date:
    """`AAAA-MM-DD` -> `date`. Cualquier otra cosa es error de uso."""
    try:
        return date.fromisoformat(texto)
    except ValueError:
        raise UsoInvalido("--fecha-corte debe ser AAAA-MM-DD, llego: %r" % texto)


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="content-factory", description=__doc__)
    sub = p.add_subparsers(dest="comando", required=True)

    def comun(sp):
        sp.add_argument("--fecha-corte", required=True,
                        help="AAAA-MM-DD. Obligatoria: nunca se toma del reloj")
        sp.add_argument("--db", default=None, help="ruta de la base")
        sp.add_argument("--produccion", action="store_true",
                        help="junto con --db de produccion, permite escribir en ella")
        return sp

    s = comun(sub.add_parser("senales", help="recolecta las fuentes del dia"))
    s.add_argument("--desde-raw", action="store_true",
                   help="lee raw/D/ en vez de la red")
    s.add_argument("--refrescar", action="store_true",
                   help="vuelve a pedir aunque ya haya crudo del dia")
    s.add_argument("--fuente", default=None, help="limita a una fuente por nombre")

    comun(sub.add_parser("clasificar", help="agrupa senales en temas y les pone pilar"))
    comun(sub.add_parser("score", help="puntua los temas del dia"))
    sel = comun(sub.add_parser("seleccionar", help="elige el TOP y registra la corrida"))
    # **Aqui NO se valida que `--corregir` traiga motivo, y es deliberado.** La
    # unica autoridad sobre un motivo vacio es `CorreccionInvalida`, en
    # `db.py:registrar_correccion`. Repetir la exigencia en argparse partiria el
    # contrato en dos sitios que pueden divergir, y dejaria sin ejercitar la
    # rama del dominio: la ruta quedaria "probada" por el guardian de la puerta
    # y nunca por quien de verdad decide. Si esto se "arregla" algun dia, se
    # pierde esa prueba.
    sel.add_argument("--corregir", action="store_true",
                     help="autoriza sustituir la decision ya materializada de esa fecha")
    sel.add_argument("--motivo", default="",
                     help="por que se sustituye. Sin el, la correccion se rechaza "
                          "en el dominio antes de tocar nada")
    comun(sub.add_parser("informe", help="imprime el top10_json del dia"))

    r = comun(sub.add_parser("replay", help="reconstruye una noche sin red ni modelo"))
    # `--sin-llm` SOLO aqui (D-11). Ver el encabezado del modulo.
    r.add_argument("--sin-llm", action="store_true",
                   help="usa la plantilla de angulo y no consulta la cache")
    r.add_argument("--raw", default=None, help="raiz de crudos alternativa")
    return p


def resolver_db(args: argparse.Namespace) -> sqlite3.Connection:
    """Aplica la guarda de produccion. Sin banderas, base de prueba."""
    return conectar(getattr(args, "db", None),
                    produccion=bool(getattr(args, "produccion", False)))


# ─── Etapa 1 · senales ──────────────────────────────────────────────────────

def cmd_senales(args) -> int:
    fecha_corte = fecha_de(args.fecha_corte)
    marca, fuentes = cargar_config()
    conn = resolver_db(args)
    crear_esquema(conn)
    etags = EtagStore(conn)
    base_raw = Path(args.raw) if getattr(args, "raw", None) else None

    resultados = []
    total = 0
    for cfg in fuentes:
        if args.fuente and cfg.nombre != args.fuente:
            continue
        modulo = POR_TIPO.get(cfg.tipo)
        if modulo is None:
            continue
        # El orden es `(senales, resultado)`, no al reves. Se escribe asi de
        # explicito porque invertirlo no revienta al importar: revienta mas
        # tarde, al iterar, y el mensaje no dice cual fue el error.
        senales, res = modulo.recolectar(
            cfg, fecha_corte, desde_raw=bool(args.desde_raw),
            base_raw=base_raw, etags=etags)
        resultados.append(res)
        if senales:
            total += insertar_senales(conn, [_como_fila(s, res) for s in senales])
        upsert_ingesta(conn, str(fecha_corte), res.como_dict())

    raw.escribir_manifiesto(fecha_corte, resultados, base_raw)
    print("senales %s: %d nuevas de %d fuentes" % (fecha_corte, total, len(resultados)))
    return 0


# ─── Etapa 2 · clasificar ───────────────────────────────────────────────────

def cmd_clasificar(args) -> int:
    fecha_corte = fecha_de(args.fecha_corte)
    marca, fuentes = cargar_config()
    por_nombre = {f.nombre: f for f in fuentes}
    conn = resolver_db(args)
    crear_esquema(conn)
    base_raw = Path(args.raw) if getattr(args, "raw", None) else None
    derivados = raw.leer_derivados(fecha_corte, base_raw)

    senales = _senales_del_dia(conn, fecha_corte)
    temas = consolidar(deduplicar(senales), fecha_corte)

    n = 0
    for tema in temas:
        cls = clasificar(tema, tema.senales, marca, por_nombre)
        if cls is None:
            # Filtro institucional: la senal NUNCA llega a `temas` (AC-9).
            continue
        d = derivados.get(tema.hash_titulo, {})
        tema_id = upsert_tema(conn, {
            "hash_titulo": tema.hash_titulo,
            "titulo": tema.titulo,
            "pilar": cls.pilar,
            "intent": cls.intent,
            # En el replay la fecha de primera aparicion viene de
            # `_derivados.json`: si se tomara la del dia, un tema visto tres
            # dias antes pareceria nuevo y su `novedad` saldria 1.0 en vez de
            # la que tuvo esa noche.
            "creado_en": d.get("creado_en") or tema.creado_en,
            "estado": "DESCUBIERTA",
            "metodo_clasificacion": cls.metodo,
            # En el replay la evidencia viene de `_derivados.json`, porque
            # verificarla de nuevo seria red (D-19). En una corrida normal el
            # diccionario esta vacio y se arma desde las senales.
            "evidencia": d.get("evidencia", []),
        })
        _enlazar(conn, tema_id, [s.hash for s in tema.senales])
        n += 1
    print("clasificar %s: %d temas" % (fecha_corte, n))
    return 0


def _como_fila(senal, resultado) -> dict:
    """`Senal` -> fila de `senales`, respetando el contrato de `insertar_senales`.

    **`extra` va como diccionario, no como texto.** La senal lo trae ya
    serializado en `extra_json`, pero la funcion de insercion vuelve a
    serializar lo que encuentre bajo `extra`. Pasarle el objeto tal cual haria
    que `extra_json` quedara en `{}` en la base y los puntos de Hacker News y
    las vistas de YouTube se perderian en silencio, dejando la demanda sin
    metrica en todos los temas.
    """
    import json as _json

    try:
        extra = _json.loads(senal.extra_json or "{}")
    except ValueError:
        extra = {}
    return {
        "hash": senal.hash,
        "fuente": senal.fuente,
        "texto": senal.texto,
        "url": senal.url,
        "publicada_en": senal.publicada_en,
        "detectada_en": senal.detectada_en,
        "extra": extra,
        "raw_path": resultado.raw_path or "",
    }


def _enlazar(conn, tema_id: int, hashes: list[str]) -> None:
    """Une el tema con sus senales en `tema_senal`.

    Sin esto, `demanda_de` y `produccion_de` no encuentran ninguna senal del
    tema y todo el puntaje sale plano. `upsert_tema` no lo hace: solo escribe la
    fila de `temas`.
    """
    for h in sorted(hashes):
        fila = conn.execute("SELECT id FROM senales WHERE hash = ?", (h,)).fetchone()
        if fila is not None:
            conn.execute(
                "INSERT OR IGNORE INTO tema_senal (tema_id, senal_id) VALUES (?,?)",
                (tema_id, int(fila[0])))
    conn.commit()


def _senales_del_dia(conn, fecha_corte):
    from content_factory.ingestion.base import Senal
    filas = conn.execute(
        "SELECT fuente, texto, url, publicada_en, detectada_en, extra_json, hash "
        "FROM senales WHERE substr(COALESCE(publicada_en, detectada_en),1,10) <= ? "
        "ORDER BY hash ASC", (str(fecha_corte),)).fetchall()
    return [Senal(fuente=f[0], texto=f[1], url=f[2], publicada_en=f[3],
                  detectada_en=f[4], extra_json=f[5], hash=f[6]) for f in filas]


# ─── Etapa 3 · score ────────────────────────────────────────────────────────

def cmd_score(args, cargador=None) -> int:
    fecha_corte = fecha_de(args.fecha_corte)
    marca, _f = cargar_config()
    conn = resolver_db(args)
    crear_esquema(conn)
    base_raw = Path(args.raw) if getattr(args, "raw", None) else None
    version = _version(marca)

    _sembrar_embeddings(conn, fecha_corte, base_raw, cargador)
    # El reparto se calcula sobre la ventana de ESA noche si esta persistida.
    # En el replay la base es temporal y no tiene piezas: consultarla daria
    # reparto vacio, `pen_saturacion` cero, y un `final` distinto al publicado.
    ventana = raw.leer_ventana(fecha_corte, base_raw)
    shares = (diversity.share_de_ventana(ventana) if ventana is not None
              else diversity.share_por_pilar(conn, fecha_corte, marca.tope_ventana))
    derivados = raw.leer_derivados(fecha_corte, base_raw)

    # Fase A (21-oct): un tema ANTECEDENTE es una pieza ya publicada antes de que la
    # cola existiera. Se registra como historia, no como candidato: puntuarlo lo
    # dejaria entrar al TOP y producirse otra vez.
    filas = conn.execute(
        "SELECT id, hash_titulo, pilar FROM temas WHERE estado != 'ANTECEDENTE' "
        "ORDER BY hash_titulo ASC").fetchall()
    for tema_id, hash_titulo, pilar in filas:
        c = componentes_de(tema_id, conn, fecha_corte, marca)
        guardado = derivados.get(hash_titulo, {})
        if "pen_repeticion" in guardado:
            # Replay: el valor de esa noche, no uno recalculado (D-19).
            rep = float(guardado["pen_repeticion"])
        else:
            rep = diversity.pen_repeticion(
                tema_id, conn, fecha_corte,
                cargador=cargador or diversity.cargar_modelo)
        # **Se redondea ANTES de componer, no solo al persistir.** `rep` se
        # guarda en `_derivados.json` ya redondeado y el replay lo lee de ahi;
        # si la corrida original compusiera con el valor sin redondear, los dos
        # `final` diferirian en la ultima cifra. Se midio: 0.243097 contra
        # 0.243096. Una diferencia de una unidad en el sexto decimal basta para
        # que AC-1 falle, y por una razon que no es el scoring.
        rep = round(float(rep), marca.pesos.decimales)
        sat = round(diversity.pen_saturacion(pilar, shares, marca),
                    marca.pesos.decimales)
        m = combinar(c, rep, sat, marca.pesos)
        upsert_score(conn, tema_id, str(fecha_corte),
                     valores_para_persistir(c, rep, sat, m),
                     version, marca.pesos.decimales)
    print("score %s: %d temas" % (fecha_corte, len(filas)))
    return 0


def _sembrar_embeddings(conn, fecha_corte, base_raw, cargador):
    """Mete los embeddings de `_derivados.json` antes de tocar el modelo.

    Es lo que permite que el replay no cargue MiniLM: si los vectores de esa
    noche ya estan en la base, `asegurar_embeddings` no encuentra pendientes y
    nunca llama al cargador.
    """
    import base64

    derivados = raw.leer_derivados(fecha_corte, base_raw)
    for hash_titulo, d in sorted(derivados.items()):
        b64 = d.get("embedding_b64")
        if not b64:
            continue
        conn.execute(
            "UPDATE temas SET embedding = ? WHERE hash_titulo = ? AND embedding IS NULL",
            (base64.b64decode(b64), hash_titulo))
    conn.commit()
    diversity.asegurar_embeddings(
        conn, fecha_corte, cargador=cargador or diversity.cargar_modelo)


def _version(marca):
    from content_factory.config import cargar_fuentes, version_reglas
    return version_reglas(marca, cargar_fuentes())


# ─── Etapa 4 · seleccionar ──────────────────────────────────────────────────

class DecisionYaMaterializada(RuntimeError):
    """La fecha ya tiene decision y esta invocacion no tiene autoridad para sustituirla."""


def cmd_seleccionar(args, *, sin_llm=False, pedir=None, identidad=...) -> int:
    fecha_corte = fecha_de(args.fecha_corte)
    marca, _f = cargar_config()
    conn = resolver_db(args)
    crear_esquema(conn)
    base_raw = Path(args.raw) if getattr(args, "raw", None) else raw._base(None)
    version = _version(marca)

    # ── D-11 · la frontera de autoridad vive AQUI, no en la persistencia ────
    #
    # Se consulta LA BASE QUE SE ESTA ESCRIBIENDO. Por eso el replay pasa sin
    # excepcion especial: su base es temporal y recien creada, asi que nunca
    # encuentra decision previa. Un `if replay` seria una excepcion que tarde o
    # temprano alguien olvida mantener.
    #
    # RQ-15 exige que repetir la cadena no acumule ni duplique. Rechazar cumple
    # eso de la forma mas fuerte: no cambia nada. Lo que RQ-15 NO exige es que
    # toda reejecucion destruya la decision anterior; esa frase enumera el
    # mecanismo de la etapa, no un deber independiente.
    corregir = bool(getattr(args, "corregir", False))
    motivo = str(getattr(args, "motivo", "") or "")
    previa = conn.execute(
        "SELECT 1 FROM corridas WHERE fecha_corte = ?", (str(fecha_corte),)
    ).fetchone()
    if previa is not None and not corregir:
        raise DecisionYaMaterializada(
            "el %s ya tiene una decision materializada. Una reejecucion ordinaria "
            "no la sustituye: sus piezas guardan el pilar que la compuerta consumio "
            "esa noche, y recrearlas lo tomaria del presente. Para sustituirla hace "
            "falta autorizacion explicita con motivo" % fecha_corte
        )

    cands = selector.candidatos(conn, fecha_corte)
    # La ventana de esa noche, si el dia la tiene guardada. Es lo que permite
    # que un replay sobre una base temporal vacia reproduzca la seleccion de un
    # dia que dependia de las piezas de dias anteriores. Sin esto, el replay de
    # tres dias devolvia 5, 5 y 5 piezas cuando lo publicado fue 5, 1 y 0.
    guardada = raw.leer_ventana(fecha_corte, base_raw)
    historial = (guardada if guardada is not None
                 else selector.historial_pilares(conn, fecha_corte, marca.tope_ventana))
    elegidos, descartados = selector.seleccionar(cands, historial, marca)
    formato = selector.formato_de(fecha_corte, marca)
    derivados = raw.leer_derivados(fecha_corte, base_raw)

    # **Se recorre TODOS los candidatos, no solo los elegidos.** El volcado de
    # derivados tiene que cubrir a los perdedores: su `final` decide el orden y,
    # con el, quien entra al TOP. Sin sus vectores, el replay tendria que
    # recalcularlos y cargaria MiniLM. El angulo, en cambio, solo se pide para
    # los elegidos: nadie publica a un descartado.
    elegidos_por_hash = {c.hash_titulo for c in elegidos}
    por_tema = {}
    for cand in cands:
        fila = conn.execute(
            "SELECT embedding, evidencia_json, creado_en FROM temas WHERE id = ?",
            (cand.tema_id,)).fetchone()
        guardado = derivados.get(cand.hash_titulo, {})
        datos = {
            "creado_en": fila[2] if fila is not None else str(fecha_corte),
            "pen_repeticion": _pen_guardada(conn, cand, fecha_corte),
            "pen_saturacion": _sat_guardada(conn, cand, fecha_corte),
            "evidencia": guardado.get("evidencia") or _evidencia(fila, cand, conn),
            "embedding_b64": guardado.get("embedding_b64") or _b64(fila),
        }
        if cand.hash_titulo in elegidos_por_hash:
            texto, metodo = angulo.proponer(
                cand, conn, marca, sin_llm=sin_llm, formato=formato, pedir=pedir)
            datos["angulo"] = texto
            datos["angulo_metodo"] = metodo
        por_tema[cand.hash_titulo] = datos

    resultados = [r.resultado for r in raw.leer_manifiesto(fecha_corte, base_raw)]
    hashes = [f[0] for f in conn.execute("SELECT hash FROM senales").fetchall()]
    top10_json = selector.registrar_corrida(
        conn, fecha_corte, elegidos, marca=marca, version_reglas=version,
        sin_llm=sin_llm, resultados_fuente=resultados, hashes_senales=hashes,
        por_tema=por_tema, raiz_raw=base_raw, descartados=descartados,
        ventana=historial, corregir=corregir, motivo=motivo,
        identidad_persistida=identidad)
    registrar_evento(conn, str(fecha_corte),
                     "seleccion: %d elegidas, %d descartadas por cupo de pilar"
                     % (len(elegidos), len(descartados)))
    print(top10_json)
    return 0


def _b64(fila):
    import base64
    if fila is None or not fila[0]:
        return ""
    return base64.b64encode(fila[0]).decode("ascii")


def _evidencia(fila, cand, conn):
    """Evidencia del tema: las URL de sus senales, tal como quedaron en crudo."""
    if fila is not None and fila[1]:
        import json
        try:
            guardada = json.loads(fila[1])
            if guardada:
                return guardada
        except ValueError:
            pass
    filas = conn.execute(
        "SELECT s.url, s.publicada_en, s.fuente FROM senales s "
        "JOIN tema_senal ts ON ts.senal_id = s.id WHERE ts.tema_id = ? "
        "AND s.url IS NOT NULL ORDER BY s.hash ASC", (cand.tema_id,)).fetchall()
    return [{"url": f[0], "publicada_en": f[1], "fuente": f[2]} for f in filas]


def _pen_guardada(conn, cand, fecha_corte):
    f = conn.execute("SELECT pen_repeticion FROM scores WHERE tema_id = ? AND fecha = ?",
                     (cand.tema_id, str(fecha_corte))).fetchone()
    return float(f[0]) if f else 0.0


def _sat_guardada(conn, cand, fecha_corte):
    f = conn.execute("SELECT pen_saturacion FROM scores WHERE tema_id = ? AND fecha = ?",
                     (cand.tema_id, str(fecha_corte))).fetchone()
    return float(f[0]) if f else 0.0


# ─── informe ────────────────────────────────────────────────────────────────

def cmd_informe(args) -> int:
    fecha_corte = fecha_de(args.fecha_corte)
    conn = resolver_db(args)
    fila = conn.execute("SELECT top10_json FROM corridas WHERE fecha_corte = ?",
                        (str(fecha_corte),)).fetchone()
    if fila is None:
        print("no hay corrida para %s" % fecha_corte, file=sys.stderr)
        return 1
    print(fila[0])
    return 0


# ─── replay ─────────────────────────────────────────────────────────────────

class ReglasNoCoinciden(RuntimeError):
    """La configuracion de hoy no es la que materializo ese dia (D-19 2A)."""


#: Los DOS estados que el replay declara por `stderr`. El tercero -no coincide-
#: no imprime: levanta. Se usa `stderr` y no `avisos` del `top10_json` porque
#: ese cuerpo se compara byte a byte en AC-1: una marca ahi haria fallar la
#: comparacion por una razon que no es la decision.
IDENTIDAD_VERIFICADA = "identidad de configuracion VERIFICADA"
IDENTIDAD_LEGACY = "identidad de configuracion NO VERIFICABLE (legacy)"


def _verificar_reglas(fecha_corte, base_raw) -> tuple[str, str | None]:
    """Compara la identidad esperada de `D` contra la del presente.

    **Ocurre ANTES de cualquier efecto sobre `raw/`**, y esa es la mitad del
    contrato que importa: `cmd_senales` reescribe `_manifiesto.json` en la
    etapa 1, asi que una comprobacion posterior ya habria tocado el artefacto
    que pretende verificar. Aqui solo se lee.

    Tres estados, deliberadamente distinguibles:

    1. firma presente y coincide -> devuelve `IDENTIDAD_VERIFICADA`
    2. firma presente y NO coincide -> levanta `ReglasNoCoinciden`
    3. firma AUSENTE -> devuelve `IDENTIDAD_LEGACY`

    El tercero existe porque los dias anteriores a este contrato no guardaron
    identidad y rellenarla seria fabricarla. Se permite reconstruirlos, pero
    **nunca puede leerse como reproduccion causal verificada**: por eso el
    estado se declara en voz alta en vez de callarse.
    """
    esperada = raw.leer_version_reglas(fecha_corte, base_raw)
    if esperada is None:
        return IDENTIDAD_LEGACY, None
    marca, _f = cargar_config()
    actual = _version(marca)
    if esperada != actual:
        raise ReglasNoCoinciden(
            "el %s se materializo con las reglas %s y la configuracion de ahora "
            "es %s. Reconstruirlo con reglas distintas no reproduce esa decision: "
            "produce otra. Se rechaza antes de tocar nada de raw/"
            % (fecha_corte, esperada, actual)
        )
    return IDENTIDAD_VERIFICADA, esperada


def cmd_replay(args, *, cargador=None, pedir=None) -> int:
    """Reconstruye una noche pasada en una base TEMPORAL. Sin red, sin modelo.

    La base es temporal y se borra al terminar por una razon que no es de
    limpieza: si escribiera en la base real, el replay meteria filas en `piezas`
    y contaminaria la ventana de 10 que decide la compuerta del 35 % de manana.
    Reproducir el pasado no puede cambiar el futuro.
    """
    fecha_corte = fecha_de(args.fecha_corte)
    base_raw = Path(args.raw) if getattr(args, "raw", None) else raw._base(None)

    # D-19 2A. Antes del temporal y MUCHO antes de `cmd_senales`: si esto
    # levanta, no se ha escrito ni un byte en `raw/`.
    estado, esperada = _verificar_reglas(fecha_corte, base_raw)
    print(estado, file=sys.stderr)

    tmp = Path(tempfile.mkdtemp(prefix="cf-replay-")) / "replay.db"
    try:
        conn = sqlite3.connect(str(tmp))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        crear_esquema(conn)
        conn.close()

        base = argparse.Namespace(
            fecha_corte=args.fecha_corte, db=str(tmp), produccion=False,
            raw=str(base_raw), desde_raw=True, refrescar=False, fuente=None)

        cmd_senales(base)
        _reconstruir_ingestas(tmp, fecha_corte, base_raw)
        cmd_clasificar(base)
        cmd_score(base, cargador=cargador or _cargador_que_lanza)
        # El replay NO tiene autoridad para establecer identidad historica:
        # solo conserva la que ya hubiera. En LEGACY `esperada` es `None` y la
        # ausencia se preserva; en VERIFICADA es la misma firma que ya estaba.
        return cmd_seleccionar(base, sin_llm=bool(getattr(args, "sin_llm", False)),
                               pedir=pedir or _pedir_que_lanza,
                               identidad=esperada)
    finally:
        try:
            tmp.unlink()
            tmp.parent.rmdir()
        except OSError:
            pass


def _cargador_que_lanza():
    raise AssertionError(
        "el replay intento cargar MiniLM: los embeddings debian venir de "
        "_derivados.json. Si esto salta, la reproducibilidad no es real")


def _pedir_que_lanza(_prompt):
    raise AssertionError("el replay intento llamar al modelo de lenguaje")


def _reconstruir_ingestas(ruta_db, fecha_corte, base_raw):
    """`ingestas` sale del manifiesto, no de lo que acaba de pasar.

    El replay tiene que reportar las fuentes que cayeron ESA noche, no las que
    caen hoy. Sin esto, el informe del replay inventaria un estado de fuentes.
    """
    conn = sqlite3.connect(str(ruta_db))
    conn.row_factory = sqlite3.Row
    for res in raw.leer_manifiesto(fecha_corte, base_raw):
        upsert_ingesta(conn, str(fecha_corte), res.como_dict())
    conn.close()


# ─── main ───────────────────────────────────────────────────────────────────

COMANDOS = {
    "senales": cmd_senales,
    "clasificar": cmd_clasificar,
    "score": cmd_score,
    "seleccionar": cmd_seleccionar,
    "informe": cmd_informe,
    "replay": cmd_replay,
}


def main(argv: list[str] | None = None) -> int:
    """Unico sitio del paquete donde puede aparecer el reloj (RQ-48)."""
    args = construir_parser().parse_args(argv)
    try:
        return COMANDOS[args.comando](args)
    except UsoInvalido as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
