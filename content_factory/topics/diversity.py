# -*- coding: utf-8 -*-
"""Diversidad: las dos penalizaciones que frenan la monotonia.

Son dos cosas distintas y conviene no confundirlas:

- **`pen_repeticion`** mira el CONTENIDO. Castiga al tema que se parece a algo
  que ya tratamos en los ultimos 30 dias, aunque sea de otro pilar.
- **`pen_saturacion`** mira el REPARTO. Castiga al pilar que ya ocupa mas
  espacio del que declara su cuota, aunque cada pieza sea distinta.

Ninguna de las dos bloquea nada. Son penalizaciones continuas que restan en
`m_diversidad`. **El unico limite duro vive en el selector** (RQ-26: maximo 3
piezas por pilar en la ventana de 10) y es binario: no se pondera ni se relaja.
Mezclar las dos cosas seria el error clasico de convertir una compuerta en una
multa, y entonces un pilar con puntajes altisimos acabaria pasandose el limite
pagando la multa.

**La cuota base es tope declarado, no mandato** (RQ-25). Un pilar que rinde
puede superar su cuota: paga `pen_saturacion` y sigue compitiendo. Lo que no
puede es pasarse del limite duro, y eso no se decide aqui.

**Importacion perezosa, a proposito** (D-15). `sentence_transformers` y `torch`
se importan DENTRO de `cargar_modelo`, nunca al cargar el modulo. La carga del
modelo son 52.9 segundos medidos, hasta unos 170 con la maquina ocupada. A nivel
de modulo, las pruebas del repositorio pagarian ese costo cada vez y el paquete
arrastraria torch entero. `device='cpu'` forzado: la GPU de 8 GB la ocupa el
trabajo de voz, y ademas el vector en CPU es reproducible bit a bit, que es lo
que el replay necesita.

**El modelo se carga solo si hace falta.** Si `temas.embedding` ya esta poblado,
`cargador` no se invoca nunca. De ahi que el replay pueda reconstruir
`pen_repeticion` desde `_derivados.json` sin MiniLM y sin red.
"""

from __future__ import annotations

import math
import sqlite3
import struct
from datetime import date, timedelta

from content_factory.config import Marca

#: Ventana de contenido, en dias. La misma que usa el scorer.
VENTANA_DIAS = 30

#: Ventana de reparto, en piezas (RQ-26: `tope_ventana` de la capa 0).
VENTANA_PIEZAS = 10

#: Estados de `piezas` que cuentan para la ventana de reparto.
#:
#: **Lista explicita, nunca un criterio del tipo "o posterior"** (RQ-26): el
#: orden de los estados no esta definido en ninguna parte, asi que "posterior"
#: no se puede escribir como consulta. En V0 el motor solo escribe
#: `SELECCIONADA`. Todo estado que despues deba contar hay que agregarlo aqui a
#: mano, y esa molestia es intencional.
ESTADOS_DE_LA_VENTANA: tuple[str, ...] = ("SELECCIONADA",)

#: Modelo de embeddings. **Eleccion declarada, con una deuda abierta.**
#:
#: El diseno dice "MiniLM" sin fijar cual. Se toma la variante inglesa porque es
#: la que ESTA descargada en la maquina y la unica cuyo costo de carga esta
#: medido (los 52.9 s del diseno). La corrida nocturna es desatendida: elegir un
#: modelo que no esta en disco convertiria la primera noche en una descarga de
#: cientos de megas a las tres de la manana, con la corrida colgada esperando.
#:
#: **Medido el 2026-09-16, no supuesto** (con `HF_HUB_OFFLINE=1`, asi que es
#: carga de disco y no descarga): 27.1 s de carga, vector de 384 dimensiones.
#: El diseno anotaba 52.9 s; la diferencia es el chequeo de red que aqui no
#: ocurre. Y sobre la duda de fondo, dos titulos en ESPANOL:
#:
#:     "Como correr un modelo de lenguaje en tu laptop sin internet"
#:       contra "Correr un LLM en tu laptop sin conexion"      -> 0.605
#:       contra "Spacegulls y Bobl: lo mejor del homebrew NES" -> 0.254
#:
#: La separacion existe y va en el sentido correcto, que es lo unico que
#: `pen_repeticion` necesita: ordenar, no puntuar en absoluto.
#:
#: **La deuda que queda:** la variante esta entrenada en ingles y los titulos son
#: en espanol, asi que 0.605 para un par casi identico es tibio. Un multilingue
#: probablemente separe mejor. `MODELO_CANDIDATO` es el reemplazo razonable, y la
#: decision NO se toma aqui: exige descargar el modelo, volver a medir y
#: recalcular todos los embeddings persistidos. Queda como OP-6.
MODELO = "sentence-transformers/all-MiniLM-L6-v2"

#: Reemplazo propuesto, multilingue. Ver la deuda descrita en `MODELO`. No se
#: usa todavia: cambiarlo obliga a recalcular `temas.embedding` y a subir
#: `version_reglas`, porque dos embeddings de modelos distintos no son
#: comparables entre si.
MODELO_CANDIDATO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class ModeloNoDisponible(RuntimeError):
    """El modelo hace falta y no se pudo cargar. No se inventa un vector."""


def cargar_modelo() -> object:
    """Carga MiniLM en CPU. **Los imports viven aqui dentro** (D-15).

    Si se moviera arriba, importar `content_factory` arrastraria torch y las
    pruebas del repositorio pagarian casi un minuto cada vez.
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:                      # pragma: no cover
        raise ModeloNoDisponible(
            "sentence_transformers no esta instalado: sin el no hay embeddings y "
            "pen_repeticion no se puede calcular. No se sustituye por un vector falso"
        ) from exc
    return SentenceTransformer(MODELO, device="cpu")


def empaquetar(vector) -> bytes:
    """Vector -> BLOB `float32` little-endian, como declara el esquema.

    Se fija el orden de bytes en vez de dejarlo a la maquina: un embedding
    guardado hoy tiene que leerse igual en cualquier lado, y `_derivados.json`
    lo lleva en base64 al replay.
    """
    valores = [float(x) for x in vector]
    return struct.pack("<%df" % len(valores), *valores)


def desempaquetar(blob: bytes) -> list[float]:
    """BLOB -> lista de flotantes. Inversa exacta de `empaquetar`."""
    if not blob:
        return []
    n = len(blob) // 4
    return list(struct.unpack("<%df" % n, blob[: n * 4]))


def embedding_de(titulo: str, cargador=cargar_modelo) -> bytes:
    """Embedding del titulo, ya empaquetado. `cargador` es inyectable.

    Que sea inyectable no es comodidad de prueba: es lo que permite pasarle al
    replay un cargador que LANZA, y asi demostrar que MiniLM no se cargo en vez
    de afirmarlo.
    """
    modelo = cargador()
    vector = modelo.encode(str(titulo))
    return empaquetar(vector)


def coseno(a: list[float], b: list[float]) -> float:
    """Coseno entre dos vectores. `0.0` si alguno es nulo o de otro tamano.

    Devolver `0.0` ante tamanos distintos y no reventar es deliberado: un
    embedding viejo calculado con otro modelo no debe tumbar la corrida
    completa, tiene que dejar de aportar parecido. El cambio de modelo se nota
    igual, porque `version_reglas` cambia.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def _embedding_persistido(
    conn: sqlite3.Connection, tema_id: int, cargador
) -> list[float]:
    """Lee el embedding del tema; lo calcula y lo PERSISTE solo si falta (RQ-24).

    Una sola vez por tema. Si ya esta, `cargador` no se invoca: es la condicion
    que hace posible el replay sin MiniLM.
    """
    fila = conn.execute(
        "SELECT titulo, embedding FROM temas WHERE id = ?", (tema_id,)
    ).fetchone()
    if fila is None:
        return []
    titulo, blob = fila[0], fila[1]
    if blob:
        return desempaquetar(blob)
    blob = embedding_de(titulo, cargador)
    conn.execute("UPDATE temas SET embedding = ? WHERE id = ?", (blob, tema_id))
    conn.commit()
    return desempaquetar(blob)


def asegurar_embeddings(
    conn: sqlite3.Connection,
    fecha_corte: date,
    ventana_dias: int = VENTANA_DIAS,
    cargador=cargar_modelo,
) -> int:
    """Materializa los embeddings que falten en la ventana. Devuelve cuantos calculo.

    **Hay que llamarla ANTES de puntuar, y no es opcional.** `pen_repeticion`
    solo compara contra temas que ya tienen embedding persistido. Si los
    embeddings se fueran calculando al vuelo, tema por tema, el primero en
    puntuarse no tendria contra quien compararse y el ultimo se compararia
    contra todos: la penalizacion dependeria del ORDEN en que se recorren los
    temas, que es justo lo que AC-1 prohibe. Dos corridas del mismo dia con el
    mismo insumo darian numeros distintos.

    Recorre en orden de `hash_titulo`, que es total y estable, para que incluso
    una interrupcion a la mitad deje el mismo estado que la vez anterior.

    Carga el modelo **una sola vez** y solo si de verdad falta algun embedding:
    con todo persistido, `cargador` no se invoca nunca.
    """
    desde = str(fecha_corte - timedelta(days=ventana_dias - 1))
    hasta = str(fecha_corte)
    pendientes = conn.execute(
        "SELECT id, titulo FROM temas "
        "WHERE embedding IS NULL AND substr(creado_en, 1, 10) BETWEEN ? AND ? "
        "ORDER BY hash_titulo ASC",
        (desde, hasta),
    ).fetchall()
    if not pendientes:
        return 0
    modelo = cargador()
    for tema_id, titulo in pendientes:
        conn.execute(
            "UPDATE temas SET embedding = ? WHERE id = ?",
            (empaquetar(modelo.encode(str(titulo))), tema_id),
        )
    conn.commit()
    return len(pendientes)


def pen_repeticion(
    tema_id: int,
    conn: sqlite3.Connection,
    fecha_corte: date,
    ventana_dias: int = VENTANA_DIAS,
    cargador=cargar_modelo,
) -> float:
    """Parecido maximo con los temas de la ventana, acotado a `[0, 1]` (RQ-24).

    Se toma el **maximo** y no el promedio: repetir un tema una sola vez ya es
    repetirlo. Promediar lo diluiria entre decenas de temas ajenos y la
    penalizacion nunca llegaria a notarse.

    El coseno negativo se recorta a `0.0`: "menos que nada parecido" no existe
    como penalizacion, y dejarlo negativo convertiria `m_diversidad` en un
    premio encubierto.

    Solo compara contra temas que YA tienen embedding persistido. No calcula
    embeddings ajenos al vuelo: hacerlo cargaria MiniLM en mitad de la corrida
    por un tema que quiza ni compite, y romperia la promesa del replay.

    **Por eso el llamador debe correr `asegurar_embeddings` primero.** Sin ese
    paso previo, la penalizacion de cada tema dependeria de cuantos se hubieran
    puntuado antes que el, y dos corridas identicas darian numeros distintos.
    """
    mio = _embedding_persistido(conn, tema_id, cargador)
    if not mio:
        return 0.0
    desde = str(fecha_corte - timedelta(days=ventana_dias - 1))
    hasta = str(fecha_corte)
    filas = conn.execute(
        "SELECT id, embedding FROM temas "
        "WHERE id != ? AND embedding IS NOT NULL "
        "AND substr(creado_en, 1, 10) BETWEEN ? AND ? "
        "ORDER BY hash_titulo ASC",
        (tema_id, desde, hasta),
    ).fetchall()
    mayor = 0.0
    for _id, blob in filas:
        s = coseno(mio, desempaquetar(blob))
        if s > mayor:
            mayor = s
    return 0.0 if mayor < 0.0 else (1.0 if mayor > 1.0 else mayor)


def share_de_ventana(pilares: list[str]) -> dict[str, float]:
    """Reparto a partir de una lista de pilares ya resuelta.

    Existe para que el replay pueda calcular el MISMO reparto que esa noche
    partiendo de la ventana persistida, en vez de consultar una base temporal
    vacia. Sin esto, `pen_saturacion` salia 0.0 en el replay y distinta de la
    que se publico: el `final` no coincidia y AC-1 fallaba por una razon que no
    tenia nada que ver con el scoring.

    `share_por_pilar` no es mas que esta funcion alimentada por la base.
    """
    if not pilares:
        return {}
    total = len(pilares)
    conteo: dict[str, int] = {}
    for pilar in pilares:
        conteo[pilar] = conteo.get(pilar, 0) + 1
    return {p: conteo[p] / total for p in sorted(conteo)}


def share_por_pilar(
    conn: sqlite3.Connection, fecha_corte: date, ventana: int = VENTANA_PIEZAS
) -> dict[str, float]:
    """Reparto por pilar en la ventana de piezas ANTES O EN `fecha_corte`.

    **El corte temporal es contractual (1b), y cierra la divergencia que abrio
    1a.** El reparto de la decision de `D` no puede observar piezas decididas
    despues de `D`. Sin el corte, reconstruir o corregir un dia antiguo sobre
    una base que ya corrio dias posteriores contaminaba `pen_saturacion` y con
    ella el `final` de cada tema. Medido sobre el dia 14 con tres piezas
    futuras de P2 en la base: los puntajes caian de 0.354308 a 0.020975, de
    0.243097 a -0.090236 y de 0.238101 a -0.095232, y dos de los tres pasaban a
    negativo. El conjunto aguantaba en ese corpus; con margenes mas estrechos
    habria cambiado tambien el orden.

    `piezas.creada_en` es el `fecha_corte` de la corrida que materializo la
    pieza (escritor unico: `registrar_corrida`), asi que `creada_en <=
    fecha_corte` es un corte total y sin ambiguedad.

    **Definicion unica de la ventana** (RQ-26), la misma que usa
    `selector.historial_pilares` y la misma que usara la consulta de
    verificacion de AC-3: las `ventana` filas mas recientes de `piezas` con
    estado en `ESTADOS_DE_LA_VENTANA` y `creada_en <= fecha_corte`, ordenadas
    por `creada_en DESC, id DESC`. Los dos lectores vuelven a coincidir.

    El denominador son las piezas que hay, no `ventana`: con 4 piezas
    acumuladas, un pilar con 2 tiene 50 %, no 20 %. Dividir entre 10 desde el
    primer dia haria que ningun pilar pareciera saturado durante las primeras
    nueve noches, justo cuando la base es mas facil de desbalancear.
    """
    marcadores = ",".join("?" for _ in ESTADOS_DE_LA_VENTANA)
    # **Lee `p.pilar_decision`, no `temas.pilar`.** El reparto describe lo que
    # se publico, no como estaria clasificado hoy. Con el JOIN al tema, una
    # reclasificacion posterior reescribia el pasado: se midio que dos piezas
    # publicadas como P4 pasaban a contarse como P2.
    filas = conn.execute(
        "SELECT p.pilar_decision FROM piezas p "
        "WHERE p.estado IN (%s) AND p.creada_en <= ? "
        "ORDER BY p.creada_en DESC, p.id DESC LIMIT ?" % marcadores,
        tuple(ESTADOS_DE_LA_VENTANA) + (str(fecha_corte), ventana),
    ).fetchall()
    return share_de_ventana([f[0] for f in filas])


def pen_saturacion(pilar: str, shares: dict[str, float], marca: Marca) -> float:
    """`max(0, share - cuota_base) * k_saturacion`, acotado a `[0, 1]` (RQ-25).

    Es continua y solo castiga el EXCESO sobre la cuota declarada. Un pilar por
    debajo de su cuota paga exactamente cero, no un poquito.

    **P7 no recibe compuerta propia por esta via.** Su cuota base es 10 % en vez
    de 1/6, asi que empieza a pagar antes, pero el limite duro que lo puede
    dejar fuera es el mismo 3 de todos los pilares y vive en el selector
    (cierre de hueco de RQ-25).

    Un pilar que no existe en la capa 0 no paga penalizacion: no hay cuota
    contra la cual medir el exceso, e inventarle una seria fabricar politica
    editorial.
    """
    cfg = marca.pilares.get(pilar)
    if cfg is None:
        return 0.0
    exceso = shares.get(pilar, 0.0) - cfg.cuota_base
    if exceso <= 0.0:
        return 0.0
    valor = exceso * marca.pesos.k_saturacion
    return 1.0 if valor > 1.0 else valor
