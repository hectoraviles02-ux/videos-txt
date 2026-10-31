# -*- coding: utf-8 -*-
"""Seleccion del TOP del dia. Aqui vive el UNICO limite duro del motor.

Las penalizaciones de `diversity.py` son continuas y se pagan. **Esto no.** La
compuerta de RQ-26 es binaria: en la ventana de las ultimas 10 piezas ningun
pilar puede pasar de 3. No se pondera, no se relaja y no se convierte en multa.
Si fuera una multa, un pilar con puntajes altisimos se pasaria del limite
pagandola, que es exactamente lo que la compuerta existe para impedir.

**Antes que relajar la compuerta, el TOP sale incompleto** (RQ-27). Con
candidatos de solo dos pilares salen 6 piezas, no 10, y el informe declara el
faltante y su causa.

**Nada de esto mira el reloj.** El formato sale de `fecha_corte.weekday()`
(D-06): un replay de un martes corrido un jueves da formato de martes. El orden
sale de `final DESC, hash_titulo ASC`, que es TOTAL porque `hash_titulo` es
`UNIQUE` (D-04): no existen dos candidatos con la misma llave, asi que SQLite no
puede devolver dos ordenes distintos para la misma entrada.

## La ventana, leida al pie de la letra y con una decision declarada

RQ-26 la define como "las 10 filas mas recientes de `piezas` con estado
`SELECCIONADA`, ordenadas por `creada_en DESC, id DESC`, concatenadas con las
piezas ya elegidas en la corrida en curso".

Concatenar sin mas haria crecer la lista por encima de 10 y, con un historial
lleno, el dia siguiente no podria elegir casi nada: cada pilar ya vendria con su
cupo gastado desde ayer. **Se toma la lectura de ventana DESLIZANTE**: la lista
combinada se recorta a las `tope_ventana` mas recientes, con lo elegido hoy al
frente. Asi, al terminar una corrida de 10, la ventana son exactamente las 10 de
hoy y la consulta de verificacion de AC-3 da lo mismo que el selector. Es una
decision, esta declarada, y hay prueba de las dos propiedades.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from content_factory.config import Marca
from content_factory.db import registrar_correccion
from content_factory.raw import escribir_derivados
from content_factory.topics.scorer import Componentes, Medidas

#: Cuantas piezas trae una corrida completa (RQ-54: nunca mas).
TOPE_TOP = 10

#: Motivo unico de descarte en V0.
MOTIVO_CUPO = "cupo_pilar"

#: Estados de `piezas` que cuentan para la ventana. Misma lista que
#: `diversity.ESTADOS_DE_LA_VENTANA`, por la misma razon (RQ-26).
ESTADOS_DE_LA_VENTANA: tuple[str, ...] = ("SELECCIONADA",)


@dataclass(frozen=True)
class Candidato:
    tema_id: int
    hash_titulo: str
    titulo: str
    pilar: str
    intent: str
    medidas: Medidas
    componentes: Componentes


def formato_de(fecha_corte: date, marca: Marca) -> str:
    """Formato del dia segun la cadencia. **`fecha_corte`, nunca `date.today()`.**

    Es la diferencia entre un replay honesto y uno que miente: si mirara el
    reloj, reproducir un martes en jueves daria formato de jueves y el
    `top10_json` no coincidiria con el de esa noche.
    """
    return marca.cadencia.get(fecha_corte.weekday(), "corto")


def candidatos(conn: sqlite3.Connection, fecha_corte: date) -> list[Candidato]:
    """Temas puntuados ese dia, en orden TOTAL (D-04).

    `ORDER BY final DESC, hash_titulo ASC`. El desempate por `hash_titulo` es
    total porque la columna es `UNIQUE`; `temas.id` no serviria, porque depende
    del orden de insercion, que a su vez depende del orden en que respondieron
    las fuentes esa noche.
    """
    filas = conn.execute(
        "SELECT t.id, t.hash_titulo, t.titulo, t.pilar, t.intent, "
        "       s.demanda, s.novedad, s.relevancia, s.produccion, s.historico, "
        "       s.m_demanda, s.m_diversidad, s.m_estrategia, s.final "
        "FROM scores s JOIN temas t ON t.id = s.tema_id "
        "WHERE s.fecha = ? "
        "ORDER BY s.final DESC, t.hash_titulo ASC",
        (str(fecha_corte),),
    ).fetchall()
    salida = []
    for f in filas:
        salida.append(Candidato(
            tema_id=f[0], hash_titulo=f[1], titulo=f[2], pilar=f[3], intent=f[4],
            componentes=Componentes(demanda=f[5], novedad=f[6], relevancia=f[7],
                                    produccion=f[8], historico=f[9]),
            medidas=Medidas(m_demanda=f[10], m_diversidad=f[11],
                            m_estrategia=f[12], final=f[13]),
        ))
    return salida


def historial_pilares(
    conn: sqlite3.Connection, fecha_corte: date, ventana: int = TOPE_TOP
) -> list[str]:
    """Pilares de las ultimas piezas elegidas ANTES O EN `fecha_corte`.

    **El corte temporal es contractual (1a).** La ventana de la decision de `D`
    NO puede observar piezas decididas despues de `D`: reconstruir o corregir
    `D` sobre una base que ya corrio dias posteriores contaria piezas que esa
    noche no podia conocer. Medida: con tres piezas futuras de P2 en la base, la
    seleccion del dia 14 pasaba de 3 piezas a 0. `piezas.creada_en` es el
    `fecha_corte` de la corrida que la materializo (escritor unico:
    `registrar_corrida`), asi que `creada_en <= fecha_corte` es un corte total y
    sin ambiguedad.

    La lectura (pilar y orden) es la misma que usa `diversity.share_por_pilar`,
    que lleva el MISMO corte desde 1b. Los dos lectores de la ventana vuelven a
    tener una sola definicion, como exige RQ-26: si divergieran, el motor podria
    cumplir la compuerta dura y romper la penalizacion continua, o al reves.
    """
    marcadores = ",".join("?" for _ in ESTADOS_DE_LA_VENTANA)
    # **Lee `p.pilar_decision`, no `temas.pilar`.** La compuerta decide sobre
    # HECHOS: con que pilar se publico cada pieza. Con el JOIN al tema, una
    # reclasificacion posterior cambiaba que pilar estaba saturado, y se midio
    # que dejaba pasar una pieza que la compuerta debia bloquear.
    filas = conn.execute(
        "SELECT p.pilar_decision FROM piezas p "
        "WHERE p.estado IN (%s) AND p.creada_en <= ? "
        "ORDER BY p.creada_en DESC, p.id DESC LIMIT ?" % marcadores,
        tuple(ESTADOS_DE_LA_VENTANA) + (str(fecha_corte), ventana),
    ).fetchall()
    return [f[0] for f in filas]


def seleccionar(
    cands: list[Candidato],
    historial: list[str],
    marca: Marca,
) -> tuple[list[Candidato], list[tuple[Candidato, str]]]:
    """`(elegidos, descartados_con_motivo)`. La compuerta dura se aplica aqui.

    Recorre los candidatos en el orden total que ya traen y, por cada uno, mira
    como quedaria la ventana si lo aceptara. Si su pilar pasaria de
    `tope_pilar_ventana`, lo descarta con motivo `cupo_pilar` y sigue con el
    siguiente. **No reordena para acomodar**: eso convertiria la compuerta en
    una optimizacion y el resultado dejaria de ser reproducible por simple
    lectura del orden.

    La compuerta esta activa desde la primera corrida, con historial vacio
    (RQ-26). No espera a acumular 10 piezas.
    """
    tope_pilar = marca.tope_pilar_ventana
    tope_ventana = marca.tope_ventana
    elegidos: list[Candidato] = []
    descartados: list[tuple[Candidato, str]] = []

    for cand in cands:
        if len(elegidos) >= TOPE_TOP:
            break
        # Ventana deslizante: lo elegido hoy va al frente, lo viejo detras, y se
        # recorta a las mas recientes. Ver el encabezado del modulo.
        ventana = ([c.pilar for c in reversed(elegidos)] + list(historial))
        propuesta = ([cand.pilar] + ventana)[:tope_ventana]
        if propuesta.count(cand.pilar) > tope_pilar:
            descartados.append((cand, MOTIVO_CUPO))
            continue
        elegidos.append(cand)

    return elegidos, descartados


def hash_entradas(version_reglas: str, fecha_corte: date, hashes: list[str]) -> str:
    """Preimagen FIJADA por el diseno. No se improvisa el orden de las llaves.

    Incluye `version_reglas` a proposito: cambiar pesos o lexico debe producir
    otro `hash_entradas`. Sin eso, comparar dos corridas mentiria, porque dos
    resultados distintos con la misma entrada declarada pareceria un defecto
    cuando en realidad cambiaron las reglas.
    """
    obj = {
        "version_reglas": version_reglas,
        "fecha_corte": str(fecha_corte),
        "senales": sorted(hashes),
    }
    crudo = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()


def serializar(obj) -> str:
    """Serializacion canonica, la misma en todas partes (cierre 4 de RQ-37).

    `sort_keys` para que el orden de las llaves no dependa del de insercion,
    `ensure_ascii=False` para no convertir los acentos en escapes, y separadores
    sin espacio para que dos serializaciones del mismo objeto den el mismo byte.
    """
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


#: Resultados de fuente que vuelven la corrida `incompleta` (hueco H-3).
#:
#: `desde_raw` y `http_304` NO estan aqui, y es deliberado: si lo estuvieran,
#: reejecutar la etapa de senales el mismo dia cambiaria el `top10_json` SIN
#: cambiar `hash_entradas`, y RQ-15 con AC-6 quedarian rotos.
RESULTADOS_QUE_INCOMPLETAN: tuple[str, ...] = (
    "fallida", "desactivada", "omitida_por_cuota",
)


def estado_corrida(resultados: list[str], n_elegidos: int) -> str:
    """`completa` o `incompleta`, calculado de una sola forma.

    **Tiene que ser computable identico desde `raw/D/_manifiesto.json`**, porque
    el replay reconstruye `ingestas` desde ese manifiesto. Por eso recibe una
    lista de cadenas y no la base: la misma funcion sirve para las dos rutas, y
    esa es la que mantiene honesta la segunda igualdad de AC-1.
    """
    if any(r in RESULTADOS_QUE_INCOMPLETAN for r in resultados):
        return "incompleta"
    if n_elegidos < TOPE_TOP:
        return "incompleta"
    return "completa"


def _derivados_de(por_tema: dict) -> dict:
    """Mapa `hash_titulo -> datos derivados` (D-19).

    **Vuelca TODOS los temas puntuados, no solo el TOP.** Parece de mas y no lo
    es: el `pen_repeticion` de un tema depende de su parecido con los demas de la
    ventana, y el `final` de los NO elegidos decide el orden y, con el, quien
    entra al TOP. Si el replay solo tuviera los vectores de los elegidos,
    tendria que recalcular los de los demas, cargaria MiniLM y la promesa de
    reproducir sin modelo seria falsa. Lo descubrio el guardian del replay al
    reventar, no una lectura del codigo.

    Devuelve el mapa PELADO. La capa `por_tema` la pone `escribir_derivados`,
    que es quien conoce el formato del archivo.

    **Solo los tres campos que el replay no puede recalcular**, no todo lo que
    traiga el diccionario: el embedding (necesitaria MiniLM), `pen_repeticion`
    (depende del embedding y del historial) y la evidencia (depende de verificar
    las URL, que es red). El resto se recalcula desde `raw/` y meterlo aqui solo
    daria ocasion de que las dos copias se contradijeran.
    """
    salida = {}
    for hash_titulo in sorted(por_tema):
        d = por_tema[hash_titulo]
        salida[hash_titulo] = {
            "embedding_b64": d.get("embedding_b64", ""),
            "pen_repeticion": float(d.get("pen_repeticion", 0.0)),
            "evidencia": d.get("evidencia", []),
            # Fecha de PRIMERA aparicion del tema. Tampoco vive en `raw/D/`:
            # un tema visto el dia 14 y puntuado el 15 tiene `novedad` 0.966667,
            # no 1.0. El replay sobre una base vacia lo daria por nuevo y el
            # `final` no coincidiria. Medido: 1.0 contra 0.966667.
            "creado_en": d.get("creado_en", ""),
        }
    return salida


#: Campos del `top10_json` que NO pertenecen a la identidad de una decision.
#:
#: El angulo se calcula DESPUES de fijar el orden y esta probado que no lo mueve
#: (AC-2). Si entrara, cambiar solo la redaccion pareceria otra decision. La
#: evidencia depende de verificar URLs y tampoco decide nada.
NO_DECISORIOS: tuple[str, ...] = ("angulo_propuesto", "angulo_metodo", "evidencia")


def payload_de_decision(top10_json: str) -> dict:
    """Representacion canonica de LO QUE SE DECIDIO, sin nada mas.

    Contiene la fecha y, por cada tema seleccionado y en orden: posicion,
    `hash_titulo`, el pilar consumido, el puntaje y el formato. Son los campos
    que determinan quien entro, en que orden y bajo que regla.

    **Deliberadamente NO contiene** identificadores de fila, marcas de tiempo,
    rutas, el angulo ni la evidencia. Que no haya identificadores es lo que
    permite que la misma decision de la misma identidad aunque las filas se
    hayan escrito en otra base con otros numeros.
    """
    d = json.loads(top10_json)
    return {
        "fecha_corte": d["fecha_corte"],
        "temas": [
            [t["posicion"], t["hash_titulo"], t["pilar"], t["score"], t["formato"]]
            for t in d["temas"]
        ],
    }


def identidad_de_decision(top10_json: str) -> str:
    """sha256 de la representacion canonica. **Unica implementacion.**

    No es la firma de entradas: se midio que dos decisiones distintas pueden
    compartirla, porque cubre las senales y las reglas declaradas pero no el
    codigo que las interpreta. Esta identifica el RESULTADO.

    Sirve para decir despues "la decision A fue sustituida por la B" aunque la
    corrida A y sus piezas ya no existan.
    """
    crudo = serializar(payload_de_decision(top10_json))
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()


def registrar_corrida(
    conn: sqlite3.Connection,
    fecha_corte: date,
    elegidos: list[Candidato],
    *,
    marca: Marca,
    version_reglas: str,
    sin_llm: bool,
    resultados_fuente: list[str],
    hashes_senales: list[str],
    por_tema: dict,
    raiz_raw: Path,
    descartados: list[tuple[Candidato, str]] | None = None,
    avisos: list[str] | None = None,
    ventana: list[str] | None = None,
    corregir: bool = False,
    motivo: str = "",
    identidad_persistida=...,
) -> str:
    """Escribe la corrida entera y devuelve el `top10_json`.

    **El orden importa y no es negociable** (tarea 6.6). Todo ocurre dentro de
    UNA transaccion, y el volcado de `_derivados.json` va ANTES del `COMMIT`:

        abrir transaccion
          -> borrar la corrida vieja del dia e insertar la nueva
          -> insertar las piezas
          -> escribir un temporal y `os.replace` sobre `_derivados.json`
        COMMIT

    Si el volcado falla, la excepcion sale del bloque y la transaccion hace
    rollback: **no puede existir una fila de `corridas` sin su
    `_derivados.json`**. Al reves si puede pasar, y es inofensivo: un
    `_derivados.json` huerfano lo pisa la siguiente corrida del mismo dia.

    Reejecutar el mismo dia REEMPLAZA, no acumula (D-11): el `DELETE` se lleva
    las piezas viejas por `ON DELETE CASCADE`.
    """
    descartados = descartados or []
    avisos = list(avisos or [])
    formato = formato_de(fecha_corte, marca)
    fecha = str(fecha_corte)

    if len(elegidos) < TOPE_TOP:
        avisos.append(
            "%d de %d, compuerta de diversidad: faltan pilares con candidatos"
            % (len(elegidos), TOPE_TOP)
        )

    temas = []
    for posicion, cand in enumerate(elegidos, 1):
        d = por_tema.get(cand.hash_titulo, {})
        temas.append(fila_de_tema(
            cand, posicion, formato,
            angulo=d.get("angulo", ""),
            angulo_metodo=d.get("angulo_metodo", "plantilla"),
            pen_repeticion=float(d.get("pen_repeticion", 0.0)),
            pen_saturacion=float(d.get("pen_saturacion", 0.0)),
            evidencia=d.get("evidencia", []),
        ))

    cuerpo = {
        "fecha_corte": fecha,
        "version_reglas": version_reglas,
        "hash_entradas": hash_entradas(version_reglas, fecha_corte, hashes_senales),
        "sin_llm": bool(sin_llm),
        "avisos": sorted(avisos),
        "temas": temas,
    }
    top10_json = serializar(cuerpo)
    estado = estado_corrida(resultados_fuente, len(elegidos))

    # Una sola transaccion. `with conn` hace COMMIT al salir sin excepcion y
    # ROLLBACK si sale con ella. Por eso el volcado va DENTRO del bloque.
    with conn:
        # **La autoridad para sustituir NO se decide aqui** (microconciliacion
        # del 2026-09-17). Esta funcion conserva su contrato interno: si el
        # llamador pide materializar una corrida, la materializa reemplazando la
        # del dia, como exige su propio contrato y como contratan sus pruebas.
        # Quien decide si una invocacion TIENE derecho a pedirlo es la etapa de
        # seleccion. Poner la guarda aqui la ponia debajo del contrato que
        # pretendia proteger.
        previa = conn.execute(
            "SELECT top10_json FROM corridas WHERE fecha_corte = ?", (fecha,)
        ).fetchone()
        if previa is not None and corregir:
            # Correccion autorizada. El receipt va ANTES del borrado y en la
            # MISMA transaccion: la identidad anterior se captura por valor
            # mientras la decision todavia existe, y si algo falla despues no
            # queda receipt de una correccion que no ocurrio.
            registrar_correccion(
                conn, fecha,
                identidad_de_decision(previa[0]),
                identidad_de_decision(top10_json),
                motivo,
            )

        conn.execute("DELETE FROM corridas WHERE fecha_corte = ?", (fecha,))
        cur = conn.execute(
            "INSERT INTO corridas (fecha_corte, version_reglas, hash_entradas, "
            " top10_json, fuentes_json, creada_en, sin_llm, estado_corrida) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (fecha, version_reglas, cuerpo["hash_entradas"], top10_json,
             serializar(sorted(resultados_fuente)), fecha,
             1 if sin_llm else 0, estado),
        )
        corrida_id = int(cur.lastrowid)

        for cand in elegidos:
            d = por_tema.get(cand.hash_titulo, {})
            # `cand.pilar` es el valor que la compuerta CONSUMIO unas lineas
            # mas arriba, en `seleccionar`. Se congela aqui, en el mismo acto
            # logico que materializa la decision, y no se vuelve a consultar
            # desde `temas`: ese valor puede cambiar manana con evidencia
            # legitima, y la historia no debe seguirlo.
            conn.execute(
                "INSERT INTO piezas (tema_id, formato, angulo, angulo_metodo, "
                " estado, corrida_id, creada_en, pilar_decision) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (cand.tema_id, formato, d.get("angulo", ""),
                 d.get("angulo_metodo", "plantilla"), "SELECCIONADA",
                 corrida_id, fecha, cand.pilar),
            )

        for cand, motivo in descartados:
            conn.execute(
                "INSERT INTO eventos (pieza_id, de_estado, a_estado, en, detalle) "
                "VALUES (NULL, NULL, NULL, ?, ?)",
                (fecha, "descartado por %s: %s (%s)"
                 % (motivo, cand.titulo, cand.pilar)),
            )
        for aviso in sorted(avisos):
            conn.execute(
                "INSERT INTO eventos (pieza_id, de_estado, a_estado, en, detalle) "
                "VALUES (NULL, NULL, NULL, ?, ?)",
                (fecha, "aviso de corrida: " + aviso),
            )

        # ANTES del COMMIT, a proposito. Si esto revienta, no queda corrida.
        # El escritor vive en `raw.py` y es el UNICO: dos escritores acabarian
        # discrepando en la forma del archivo y el replay leeria en el nivel
        # equivocado sin que ninguna prueba lo notara.
        # `version_reglas` es el MISMO valor que se acaba de escribir en
        # `corridas`, no una recarga: si aqui se volviera a llamar a
        # `cargar_config`, la firma guardada podria no ser la que decidio.
        #
        # `identidad_persistida` existe porque NO toda invocacion tiene
        # autoridad para ESTABLECER la identidad historica de `D`. Una
        # materializacion -corrida normal o correccion autorizada- si la tiene,
        # y por omision se persiste la suya. Un replay no: solo puede conservar
        # la que ya hubiera. Pasarle `None` preserva la AUSENCIA, que es lo que
        # impide que una reconstruccion declarada no verificable fabrique la
        # evidencia con la que la siguiente se declararia verificada.
        #
        # La capa `raw` no sabe nada de esto: recibe un valor o `None`.
        escribir_derivados(
            fecha_corte, _derivados_de(por_tema), base=Path(raiz_raw),
            ventana=list(ventana or []),
            version_reglas=(version_reglas if identidad_persistida is ...
                            else identidad_persistida),
        )

    return top10_json


def fila_de_tema(
    cand: Candidato,
    posicion: int,
    formato: str,
    angulo: str,
    angulo_metodo: str,
    pen_repeticion: float,
    pen_saturacion: float,
    evidencia: list,
) -> dict:
    """Una entrada del `top10_json`, con los nueve campos del plan (AC-4).

    `dificultad` y `potencial` son DERIVADOS, no columnas: `1 - produccion` y
    `m_estrategia`. Se calculan al escribir para que el informe no tenga que
    saber la formula.
    """
    return {
        "posicion": posicion,
        "hash_titulo": cand.hash_titulo,
        "titulo": cand.titulo,
        "pilar": cand.pilar,
        "intent": cand.intent,
        "demanda": cand.componentes.demanda,
        "novedad": cand.componentes.novedad,
        "dificultad": 1 - cand.componentes.produccion,
        "potencial": cand.medidas.m_estrategia,
        "score": cand.medidas.final,
        "penalizaciones": {
            "pen_repeticion": pen_repeticion,
            "pen_saturacion": pen_saturacion,
        },
        "angulo_propuesto": angulo,
        "angulo_metodo": angulo_metodo,
        "formato": formato,
        "evidencia": evidencia,
    }
