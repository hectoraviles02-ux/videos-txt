# -*- coding: utf-8 -*-
"""Persistencia del topic engine y guarda de produccion.

SQLite de la stdlib, un solo archivo. Sin ORM a proposito (D-02): el
determinismo del TOP 10 descansa en `ORDER BY` explicitos, y un ORM los
esconde.

Tres cosas que este modulo hace cumplir y que no son detalle:

- **Guarda de produccion (D-03, RQ-05, AC-7)**: por omision se escribe en
  `content.test.db`. Tocar `content.db` exige DOS cosas a la vez, la ruta y la
  bandera `produccion=True`. Resolver solo por ruta dejaria pasar un error de
  dedo; un mutador de estado no toca produccion por omision.
- **Redondeo AL PERSISTIR (D-05, RQ-29)**: el valor que compara `ORDER BY` debe
  ser el guardado. Redondear al leer reintroduce el orden arbitrario entre
  flotantes que "se ven" iguales.
- **Evidencia obligatoria por transicion (D-13)**: `transicionar` exige
  `detalle` no vacio. La disciplina viene de `docs/CONTRATO_EJECUCIONES.md §4`;
  la clase `runs/run_manager.py` NO se reutiliza porque sus estados son los del
  pipeline de video.

Sin imports pesados: solo stdlib (ver `content_factory/__init__.py`).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
RUTA_PRODUCCION = RAIZ / "content_factory" / "content.db"
RUTA_PRUEBA = RAIZ / "content_factory" / "content.test.db"

ESQUEMA_VERSION = 6   # v2: `piezas.pilar_decision`, el pilar que consumio la decision
                      # v3: `senales.publicada_canonica`; fuera `ix_senales_ventana` (D-18 sustituida)
                      # v4: `publicaciones` +identificador_remoto/estado/detalle_error (distribucion
                      #     trazable). Migracion v3->v4 real (ALTER TABLE) preservando datos.
                      # v5: tabla `aprobaciones` (aprobacion individual de operador atada al
                      #     SHA-256 del MP4 y del texto) + `publicaciones.programada_para` y
                      #     estado PROGRAMADA. Migracion v4->v5 aditiva (no inventa datos).
                      # v6: la aprobacion solo nace de un CODIGO de un solo uso enviado a operador
                      #     por Telegram (tabla `codigos_aprobacion`) + `aprobaciones.origen` y
                      #     `comprobante_json`. Etapa 1: se acaba la aprobacion por frase guardada.

#: Estados de una PUBLICACION (columna `publicaciones.estado`). SON DISTINTOS del
#: estado de la pieza: una pieza transita su ciclo de vida; una publicacion
#: describe el resultado en UN canal.
PUB_PENDIENTE = "PENDIENTE"                       # intento registrado, sin confirmar
PUB_CONFIRMADA = "CONFIRMADA"                      # canal confirmo y BD consistente
PUB_ERROR = "ERROR"                                # el canal rechazo
PUB_RECONCILIACION_PENDIENTE = "RECONCILIACION_PENDIENTE"  # canal confirmo, BD incompleta
PUB_PROGRAMADA = "PROGRAMADA"                      # v5: el canal acepto con fecha futura; NO es publicada
ESTADOS_PUBLICACION = (PUB_PENDIENTE, PUB_CONFIRMADA, PUB_ERROR, PUB_RECONCILIACION_PENDIENTE,
                       PUB_PROGRAMADA)

#: Maquina de estados de una pieza. El enum va COMPLETO desde V0 (D-12) para
#: que las fases 2 a 6 no obliguen a migrar la tabla. V0 solo ejercita
#: `None -> SELECCIONADA`.
ESTADOS = (
    "DESCUBIERTA",
    "CANDIDATA",
    "SELECCIONADA",
    "INVESTIGANDO",
    "INVESTIGADA",
    "GUIONADA",
    "VERIFICADA",
    "PRODUCIDA",
    "LISTA",
    "PUBLICADA",
    "MEDIDA",
    "APRENDIDA",
)
ESTADOS_FALLO = ("FALLIDA", "REINTENTO", "FALLIDA_PERMANENTE")

#: Transiciones permitidas. Se avanza de a un paso; saltar etapas hacia
#: adelante esta prohibido. `APRENDIDA` y `FALLIDA_PERMANENTE` son terminales.
def _tabla_transiciones() -> dict[str | None, frozenset[str]]:
    permitidas: dict[str | None, set[str]] = {None: {ESTADOS[0], "SELECCIONADA"}}
    for i, estado in enumerate(ESTADOS):
        siguientes: set[str] = {"FALLIDA"}
        if i + 1 < len(ESTADOS):
            siguientes.add(ESTADOS[i + 1])
        permitidas[estado] = siguientes
    permitidas["APRENDIDA"] = {"FALLIDA"}          # terminal salvo por fallo
    permitidas["FALLIDA"] = {"REINTENTO", "FALLIDA_PERMANENTE"}
    # Desde REINTENTO el diseno dice "estado previo | FALLIDA_PERMANENTE". El
    # conjunto se deja abierto AQUI y el destino real se acota en tiempo de
    # ejecucion contra la bitacora (`_estado_previo_al_fallo`): declarar aqui
    # `set(ESTADOS)` sin esa comprobacion permitiria reintentar hacia APRENDIDA
    # y saltarse todo el camino.
    permitidas["REINTENTO"] = set(ESTADOS) | {"FALLIDA_PERMANENTE"}
    permitidas["FALLIDA_PERMANENTE"] = set()       # no se sale de aqui
    return {k: frozenset(v) for k, v in permitidas.items()}


TRANSICIONES = _tabla_transiciones()

DDL = """
CREATE TABLE IF NOT EXISTS senales (
  id            INTEGER PRIMARY KEY,
  hash          TEXT    NOT NULL UNIQUE,
  fuente        TEXT    NOT NULL,
  texto         TEXT    NOT NULL,
  url           TEXT,
  publicada_en  TEXT,
  -- Instante UTC canonico de `publicada_en`, o NULL si no es inequivoco. Es lo
  -- que usan las decisiones temporales V2; `publicada_en` se conserva CRUDA
  -- porque, en las senales sin URL, forma parte de su identidad (contrato V2-B).
  publicada_canonica TEXT,
  detectada_en  TEXT    NOT NULL,
  extra_json    TEXT    NOT NULL DEFAULT '{}',
  raw_path      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_senales_fuente_fecha ON senales(fuente, detectada_en);
CREATE INDEX IF NOT EXISTS ix_senales_publicada    ON senales(publicada_en);

CREATE TABLE IF NOT EXISTS temas (
  id                   INTEGER PRIMARY KEY,
  hash_titulo          TEXT    NOT NULL UNIQUE,
  titulo               TEXT    NOT NULL,
  pilar                TEXT    NOT NULL,
  intent               TEXT    NOT NULL,
  creado_en            TEXT    NOT NULL,
  estado               TEXT    NOT NULL,
  embedding            BLOB,
  metodo_clasificacion TEXT    NOT NULL,
  evidencia_json       TEXT    NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS ix_temas_pilar  ON temas(pilar);
CREATE INDEX IF NOT EXISTS ix_temas_creado ON temas(creado_en);

CREATE TABLE IF NOT EXISTS tema_senal (
  tema_id  INTEGER NOT NULL REFERENCES temas(id)   ON DELETE CASCADE,
  senal_id INTEGER NOT NULL REFERENCES senales(id) ON DELETE CASCADE,
  PRIMARY KEY (tema_id, senal_id)
);
CREATE INDEX IF NOT EXISTS ix_tema_senal_senal ON tema_senal(senal_id);

CREATE TABLE IF NOT EXISTS scores (
  tema_id        INTEGER NOT NULL REFERENCES temas(id) ON DELETE CASCADE,
  fecha          TEXT    NOT NULL,
  demanda        REAL    NOT NULL,
  novedad        REAL    NOT NULL,
  relevancia     REAL    NOT NULL,
  produccion     REAL    NOT NULL,
  historico      REAL    NOT NULL DEFAULT 0.0,
  pen_repeticion REAL    NOT NULL DEFAULT 0.0,
  pen_saturacion REAL    NOT NULL DEFAULT 0.0,
  m_demanda      REAL    NOT NULL,
  m_diversidad   REAL    NOT NULL,
  m_estrategia   REAL    NOT NULL,
  final          REAL    NOT NULL,
  version_reglas TEXT    NOT NULL,
  PRIMARY KEY (tema_id, fecha)
);
CREATE INDEX IF NOT EXISTS ix_scores_orden ON scores(fecha, final DESC);

CREATE TABLE IF NOT EXISTS corridas (
  id             INTEGER PRIMARY KEY,
  fecha_corte    TEXT    NOT NULL,
  version_reglas TEXT    NOT NULL,
  hash_entradas  TEXT    NOT NULL,
  top10_json     TEXT    NOT NULL,
  fuentes_json   TEXT    NOT NULL DEFAULT '[]',
  creada_en      TEXT    NOT NULL,
  sin_llm        INTEGER NOT NULL CHECK (sin_llm IN (0,1)),
  estado_corrida TEXT    NOT NULL CHECK (estado_corrida IN ('completa','incompleta')),
  UNIQUE (fecha_corte)
);

CREATE TABLE IF NOT EXISTS piezas (
  id                 INTEGER PRIMARY KEY,
  tema_id            INTEGER NOT NULL REFERENCES temas(id) ON DELETE CASCADE,
  formato            TEXT    NOT NULL,
  angulo             TEXT    NOT NULL,
  angulo_metodo      TEXT    NOT NULL,
  objetivo_audiencia TEXT,
  objetivo_negocio   TEXT,
  estado             TEXT    NOT NULL,
  parent_id          INTEGER REFERENCES piezas(id) ON DELETE SET NULL,
  run_id             TEXT,
  corrida_id         INTEGER REFERENCES corridas(id) ON DELETE CASCADE,
  creada_en          TEXT    NOT NULL,
  -- Pilar que la compuerta consumio AL materializar esta pieza. HECHO
  -- HISTORICO, no estado presente: `temas.pilar` puede cambiar despues con
  -- evidencia legitima y esta columna NO le sigue. Sin ella, la ventana
  -- reconstruia el pasado leyendo la clasificacion de hoy, y se midio que dos
  -- piezas publicadas como P4 pasaban a contarse como P2, cambiando que pilar
  -- estaba saturado y dejando pasar una pieza que la compuerta debia bloquear.
  pilar_decision     TEXT    NOT NULL,
  UNIQUE (tema_id, creada_en, formato)
);
CREATE INDEX IF NOT EXISTS ix_piezas_ventana ON piezas(creada_en DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_piezas_estado  ON piezas(estado);

CREATE TABLE IF NOT EXISTS eventos (
  id        INTEGER PRIMARY KEY,
  pieza_id  INTEGER REFERENCES piezas(id) ON DELETE CASCADE,
  de_estado TEXT,
  a_estado  TEXT,
  en        TEXT NOT NULL,
  detalle   TEXT NOT NULL,
  -- Receipt de una CORRECCION AUTORIZADA. Guarda POR VALOR la identidad de la
  -- decision sustituida, no una llave foranea: la corrida anterior se borra y
  -- una llave quedaria rota justo cuando hace falta. Estas columnas sobreviven
  -- porque un evento de corrida lleva `pieza_id` NULL y la cascada no lo
  -- alcanza. `detalle` explica; NO es la autoridad.
  identidad_anterior TEXT,
  identidad_nueva    TEXT,
  motivo             TEXT
);
CREATE INDEX IF NOT EXISTS ix_eventos_pieza ON eventos(pieza_id, id);

CREATE TABLE IF NOT EXISTS ingestas (
  fecha_corte TEXT NOT NULL,
  fuente      TEXT NOT NULL,
  resultado   TEXT NOT NULL,
  n_senales   INTEGER NOT NULL DEFAULT 0,
  detalle     TEXT    NOT NULL DEFAULT '',
  raw_path    TEXT,
  PRIMARY KEY (fecha_corte, fuente)
);

CREATE TABLE IF NOT EXISTS cache_llm (
  hash_entrada   TEXT PRIMARY KEY,
  modelo         TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  salida_json    TEXT NOT NULL,
  creada_en      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cache_http (
  url        TEXT PRIMARY KEY,
  etag       TEXT NOT NULL,
  visto_en   TEXT NOT NULL,
  raw_path   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
  id            INTEGER PRIMARY KEY,
  pieza_id      INTEGER NOT NULL REFERENCES piezas(id) ON DELETE CASCADE,
  afirmacion    TEXT    NOT NULL,
  fuente_url    TEXT    NOT NULL,
  tipo_fuente   TEXT    NOT NULL,
  verificada_en TEXT,
  confianza     REAL
);
CREATE INDEX IF NOT EXISTS ix_claims_pieza ON claims(pieza_id);

CREATE TABLE IF NOT EXISTS publicaciones (
  pieza_id             INTEGER NOT NULL REFERENCES piezas(id) ON DELETE CASCADE,
  canal                TEXT    NOT NULL,
  url                  TEXT,
  publicada_en         TEXT,
  -- v4: id remoto del canal (p.ej. video_id de FB), estado de la PUBLICACION
  -- (distinto del estado de la pieza) y detalle de error si fallo.
  identificador_remoto TEXT,
  estado               TEXT    NOT NULL DEFAULT 'CONFIRMADA',
  detalle_error        TEXT,
  -- v5: fecha/hora en que el canal la hara publica (ISO con zona). NULL si fue inmediata.
  programada_para      TEXT,
  PRIMARY KEY (pieza_id, canal)
);

-- v5: aprobacion INDIVIDUAL de operador. Atada a la huella del MP4 y del texto: si
-- cualquiera cambia, la aprobacion se invalida (no se "actualiza"). Una sola
-- VIGENTE por pieza. `palabras` son las de operador, textuales; sin ellas no hay fila.
CREATE TABLE IF NOT EXISTS aprobaciones (
  id                 INTEGER PRIMARY KEY,
  pieza_id           INTEGER NOT NULL REFERENCES piezas(id) ON DELETE CASCADE,
  sha256_mp4         TEXT    NOT NULL,
  ruta_mp4           TEXT    NOT NULL,
  sha256_texto       TEXT    NOT NULL,
  canales_json       TEXT    NOT NULL,
  programada_para    TEXT,
  palabras           TEXT    NOT NULL,
  otorgada_en        TEXT    NOT NULL,
  estado             TEXT    NOT NULL CHECK (estado IN ('VIGENTE','INVALIDADA','REVOCADA')),
  invalidada_en      TEXT,
  motivo_invalidacion TEXT,
  -- v6: de donde vino la aprobacion ('telegram') y su comprobante verificable
  -- (usuario, chat, mensaje, huella del codigo). NULL solo en filas anteriores a v6.
  origen             TEXT,
  comprobante_json   TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_aprobacion_vigente ON aprobaciones(pieza_id) WHERE estado = 'VIGENTE';

-- v6: codigos de un solo uso. Se envian a operador junto al video; se guarda solo la
-- huella del codigo. Atado a pieza + SHA-256 del MP4 + usuario destinatario; caduca.
CREATE TABLE IF NOT EXISTS codigos_aprobacion (
  id                    INTEGER PRIMARY KEY,
  pieza_id              INTEGER NOT NULL REFERENCES piezas(id) ON DELETE CASCADE,
  sha256_mp4            TEXT    NOT NULL,
  codigo_sha256         TEXT    NOT NULL UNIQUE,
  destinatario_user_id  TEXT    NOT NULL,
  emitido_en            TEXT    NOT NULL,
  caduca_en             TEXT    NOT NULL,
  usado_en              TEXT,
  message_id            TEXT
);

CREATE TABLE IF NOT EXISTS metricas (
  pieza_id    INTEGER NOT NULL REFERENCES piezas(id) ON DELETE CASCADE,
  fecha       TEXT    NOT NULL,
  vistas      INTEGER,
  ctr         REAL,
  retencion   REAL,
  horas       REAL,
  subs        INTEGER,
  comentarios INTEGER,
  compartidos INTEGER,
  PRIMARY KEY (pieza_id, fecha)
);

CREATE TABLE IF NOT EXISTS esquema_version (
  version     INTEGER PRIMARY KEY,
  aplicada_en TEXT NOT NULL
);
"""


class GuardaDeProduccion(RuntimeError):
    """Se intento escribir en la base real sin autorizarlo explicitamente."""


class TransicionInvalida(ValueError):
    """La transicion de estado pedida no esta permitida, o le falta evidencia."""


def conectar(ruta: Path | str | None = None, *, produccion: bool = False) -> sqlite3.Connection:
    """Abre la base. Sin argumentos, la de prueba.

    Escribir en `content.db` exige la ruta de produccion Y `produccion=True`.
    Con una sola de las dos, levanta `GuardaDeProduccion`.
    """
    ruta = Path(ruta) if ruta is not None else RUTA_PRUEBA
    es_produccion = ruta.resolve() == RUTA_PRODUCCION.resolve()

    if es_produccion and not produccion:
        raise GuardaDeProduccion(
            "%s es la base de produccion: para escribir en ella hace falta la bandera "
            "--produccion ademas de --db. Sin banderas se usa %s" % (ruta, RUTA_PRUEBA.name)
        )
    if produccion and not es_produccion:
        raise GuardaDeProduccion(
            "se paso --produccion pero la base apuntada es %s, no %s. La bandera y la ruta "
            "deben coincidir" % (ruta, RUTA_PRODUCCION)
        )

    ruta.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(ruta))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


class EsquemaDesactualizado(RuntimeError):
    """La base es de un esquema anterior y no se puede migrar sin inventar datos."""


def _exigir_esquema_con_publicada_canonica(conn: sqlite3.Connection) -> None:
    """Rechaza una base v2 SIN TOCARLA, en vez de migrarla en silencio.

    A diferencia de `pilar_decision`, aqui un relleno NO seria inventar
    historia: `publicada_canonica` se deriva de `publicada_en`, que si esta
    guardada. **No se construye** porque hoy no existe ninguna base que migrar,
    y porque rechazar mantiene una sola politica para las dos columnas. Si
    alguna vez aparece una base v2, rellenar es la salida correcta y no la
    inventada.

    Ocurre ANTES de cualquier sentencia de esquema: una base que se rechaza no
    puede haber perdido ya un indice ni ganado una tabla.
    """
    existe = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'senales'"
    ).fetchone()
    if existe is None:
        return
    columnas = {fila[1] for fila in conn.execute("PRAGMA table_info(senales)")}
    if "publicada_canonica" not in columnas:
        raise EsquemaDesactualizado(
            "esta base tiene `senales` sin `publicada_canonica`: es anterior al "
            "esquema v3. No se migra automaticamente y no se ha modificado. La "
            "columna es derivable de `publicada_en`, asi que rellenarla seria "
            "legitimo, pero es una decision que se toma a mano"
        )


def _exigir_esquema_con_pilar_decision(conn: sqlite3.Connection) -> None:
    """Rechaza una base vieja en vez de rellenar `pilar_decision` a ciegas.

    `CREATE TABLE IF NOT EXISTS` no altera una tabla que ya existe, asi que una
    base creada antes de esta version se quedaria sin la columna y fallaria al
    insertar, con un mensaje que no dice por que.

    **No se migra automaticamente y es deliberado.** `pilar_decision` es un
    HECHO HISTORICO: el pilar que la compuerta consumio aquella noche. Para una
    pieza ya escrita ese dato no esta en ningun sitio, y `ALTER TABLE ... NOT
    NULL` exigiria un valor por omision. Ese valor seria inventado, y inventar
    historia es exactamente lo que esta columna existe para impedir.

    Hoy no hay ninguna base de produccion, asi que el caso no tiene coste. Si
    alguna vez lo tiene, esta excepcion obliga a decidir a mano en vez de
    rellenar en silencio.
    """
    existe = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'piezas'"
    ).fetchone()
    if existe is None:
        return
    columnas = {fila[1] for fila in conn.execute("PRAGMA table_info(piezas)")}
    if "pilar_decision" not in columnas:
        raise EsquemaDesactualizado(
            "esta base tiene una tabla `piezas` sin `pilar_decision`. No se migra "
            "automaticamente: esa columna guarda el pilar que la compuerta consumio "
            "en cada decision pasada, y para las piezas ya escritas ese dato no "
            "existe en ningun sitio. Rellenarlo seria inventar historia. Decide a "
            "mano que hacer con esas piezas antes de continuar"
        )


def _migrar_publicaciones_v3_a_v4(conn: sqlite3.Connection) -> bool:
    """Migracion REAL v3->v4: agrega columnas a `publicaciones` PRESERVANDO datos.

    A diferencia de las guardas v1/v2 (que RECHAZAN porque rellenar inventaria
    historia), aqui agregar columnas NO inventa nada: para las filas v3 el id
    remoto queda NULL (desconocido, honesto) y el estado por omision 'CONFIRMADA'
    (ya estaban publicadas). `ALTER TABLE ADD COLUMN` conserva las filas
    existentes. Devuelve True si tuvo que alterar algo.

    Corre DESPUES de `executescript`: en una base nueva el DDL ya crea
    `publicaciones` con las columnas (no-op); en una base v3 el `CREATE TABLE IF
    NOT EXISTS` no la altera, y este ALTER completa la migracion.
    """
    existe = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='publicaciones'").fetchone()
    if existe is None:
        return False
    cols = {fila[1] for fila in conn.execute("PRAGMA table_info(publicaciones)")}
    cambios = False
    if "identificador_remoto" not in cols:
        conn.execute("ALTER TABLE publicaciones ADD COLUMN identificador_remoto TEXT")
        cambios = True
    if "estado" not in cols:
        conn.execute("ALTER TABLE publicaciones ADD COLUMN estado TEXT NOT NULL DEFAULT 'CONFIRMADA'")
        cambios = True
    if "detalle_error" not in cols:
        conn.execute("ALTER TABLE publicaciones ADD COLUMN detalle_error TEXT")
        cambios = True
    return cambios


def _migrar_publicaciones_v4_a_v5(conn: sqlite3.Connection) -> bool:
    """Migracion v4->v5: agrega `programada_para` PRESERVANDO datos.

    Las filas v4 quedan con NULL: no se sabe si fueron programadas y no se
    inventa. La tabla `aprobaciones` la crea el DDL (`IF NOT EXISTS`) y nace
    vacia: ninguna pieza anterior queda aprobada por migracion.
    """
    cols = {fila[1] for fila in conn.execute("PRAGMA table_info(publicaciones)")}
    if cols and "programada_para" not in cols:
        conn.execute("ALTER TABLE publicaciones ADD COLUMN programada_para TEXT")
        return True
    return False


def _migrar_aprobaciones_v5_a_v6(conn: sqlite3.Connection) -> bool:
    """v5->v6: `aprobaciones` +origen +comprobante_json. Filas viejas quedan NULL (no se inventa)."""
    cols = {fila[1] for fila in conn.execute("PRAGMA table_info(aprobaciones)")}
    cambios = False
    for col in ("origen", "comprobante_json"):
        if cols and col not in cols:
            conn.execute("ALTER TABLE aprobaciones ADD COLUMN %s TEXT" % col)
            cambios = True
    return cambios


def crear_esquema(conn: sqlite3.Connection) -> None:
    """Crea las 14 tablas, migra v3->v4 si hace falta, y siembra la version. Idempotente.

    El DDL va en un solo `executescript` porque `piezas.corrida_id` referencia
    `corridas(id)`; ejecutarlo por partes obligaria a ordenar las sentencias a
    mano y a mantener ese orden para siempre.
    """
    _exigir_esquema_con_pilar_decision(conn)
    _exigir_esquema_con_publicada_canonica(conn)
    conn.executescript(DDL)
    _migrar_publicaciones_v3_a_v4(conn)   # ALTER si venia de v3; no-op en base nueva v4
    _migrar_publicaciones_v4_a_v5(conn)   # ALTER si venia de v4; no-op en base nueva v5
    _migrar_aprobaciones_v5_a_v6(conn)    # ALTER si venia de v5; no-op en base nueva v6
    fila = conn.execute("SELECT COUNT(*) AS n FROM esquema_version").fetchone()
    if fila["n"] == 0:
        conn.execute(
            "INSERT INTO esquema_version (version, aplicada_en) VALUES (?, datetime('now'))",
            (ESQUEMA_VERSION,),
        )
    else:
        maxv = conn.execute("SELECT MAX(version) AS v FROM esquema_version").fetchone()["v"]
        if maxv is not None and maxv < ESQUEMA_VERSION:
            conn.execute(
                "INSERT INTO esquema_version (version, aplicada_en) VALUES (?, datetime('now'))",
                (ESQUEMA_VERSION,),
            )
    conn.commit()


def insertar_senales(conn: sqlite3.Connection, senales: list[dict]) -> int:
    """`INSERT OR IGNORE` por `hash`. Devuelve cuantas eran nuevas.

    Reejecutar la ingestion del mismo dia no duplica: el hash es la llave
    natural (sha256 completo, no recortado).
    """
    antes = conn.execute("SELECT COUNT(*) AS n FROM senales").fetchone()["n"]
    conn.executemany(
        """
        INSERT OR IGNORE INTO senales
          (hash, fuente, texto, url, publicada_en, detectada_en, extra_json, raw_path)
        VALUES (:hash, :fuente, :texto, :url, :publicada_en, :detectada_en, :extra_json, :raw_path)
        """,
        [
            {
                "hash": s["hash"],
                "fuente": s["fuente"],
                "texto": s["texto"],
                "url": s.get("url"),
                "publicada_en": s.get("publicada_en"),
                "detectada_en": s["detectada_en"],
                "extra_json": json.dumps(s.get("extra", {}), sort_keys=True, ensure_ascii=False),
                "raw_path": s["raw_path"],
            }
            for s in senales
        ],
    )
    conn.commit()
    despues = conn.execute("SELECT COUNT(*) AS n FROM senales").fetchone()["n"]
    return despues - antes


def upsert_tema(conn: sqlite3.Connection, tema: dict) -> int:
    """Alta o actualizacion por `hash_titulo`. Devuelve el id."""
    conn.execute(
        """
        INSERT INTO temas
          (hash_titulo, titulo, pilar, intent, creado_en, estado, embedding,
           metodo_clasificacion, evidencia_json)
        VALUES (:hash_titulo, :titulo, :pilar, :intent, :creado_en, :estado, :embedding,
                :metodo_clasificacion, :evidencia_json)
        ON CONFLICT(hash_titulo) DO UPDATE SET
          titulo               = excluded.titulo,
          pilar                = excluded.pilar,
          intent               = excluded.intent,
          estado               = excluded.estado,
          embedding            = COALESCE(excluded.embedding, temas.embedding),
          metodo_clasificacion = excluded.metodo_clasificacion,
          evidencia_json       = excluded.evidencia_json
        """,
        {
            "hash_titulo": tema["hash_titulo"],
            "titulo": tema["titulo"],
            "pilar": tema["pilar"],
            "intent": tema.get("intent", "desconocido"),
            "creado_en": tema["creado_en"],
            "estado": tema.get("estado", "CANDIDATA"),
            "embedding": tema.get("embedding"),
            "metodo_clasificacion": tema.get("metodo_clasificacion", "lexico"),
            "evidencia_json": json.dumps(
                tema.get("evidencia", []), sort_keys=True, ensure_ascii=False
            ),
        },
    )
    conn.commit()
    fila = conn.execute(
        "SELECT id FROM temas WHERE hash_titulo = ?", (tema["hash_titulo"],)
    ).fetchone()
    return int(fila["id"])


_COLUMNAS_SCORE = (
    "demanda",
    "novedad",
    "relevancia",
    "produccion",
    "historico",
    "pen_repeticion",
    "pen_saturacion",
    "m_demanda",
    "m_diversidad",
    "m_estrategia",
    "final",
)


def upsert_score(
    conn: sqlite3.Connection,
    tema_id: int,
    fecha: str,
    valores: dict,
    version_reglas: str,
    decimales: int,
) -> None:
    """Guarda las doce medidas REDONDEADAS (D-05).

    El redondeo va aqui y no al leer: el valor que compara `ORDER BY` tiene que
    ser exactamente el que quedo en disco.
    """
    datos = {c: round(float(valores.get(c, 0.0)), decimales) for c in _COLUMNAS_SCORE}
    datos.update({"tema_id": tema_id, "fecha": fecha, "version_reglas": version_reglas})
    conn.execute(
        """
        INSERT INTO scores
          (tema_id, fecha, demanda, novedad, relevancia, produccion, historico,
           pen_repeticion, pen_saturacion, m_demanda, m_diversidad, m_estrategia,
           final, version_reglas)
        VALUES (:tema_id, :fecha, :demanda, :novedad, :relevancia, :produccion, :historico,
                :pen_repeticion, :pen_saturacion, :m_demanda, :m_diversidad, :m_estrategia,
                :final, :version_reglas)
        ON CONFLICT(tema_id, fecha) DO UPDATE SET
          demanda = excluded.demanda, novedad = excluded.novedad,
          relevancia = excluded.relevancia, produccion = excluded.produccion,
          historico = excluded.historico, pen_repeticion = excluded.pen_repeticion,
          pen_saturacion = excluded.pen_saturacion, m_demanda = excluded.m_demanda,
          m_diversidad = excluded.m_diversidad, m_estrategia = excluded.m_estrategia,
          final = excluded.final, version_reglas = excluded.version_reglas
        """,
        datos,
    )
    conn.commit()


def upsert_ingesta(conn: sqlite3.Connection, fecha: str, resultado: dict) -> None:
    """Estado de una fuente en un dia. Es lo que hace auditable un replay (D-10)."""
    conn.execute(
        """
        INSERT INTO ingestas (fecha_corte, fuente, resultado, n_senales, detalle, raw_path)
        VALUES (:fecha_corte, :fuente, :resultado, :n_senales, :detalle, :raw_path)
        ON CONFLICT(fecha_corte, fuente) DO UPDATE SET
          resultado = excluded.resultado, n_senales = excluded.n_senales,
          detalle = excluded.detalle, raw_path = excluded.raw_path
        """,
        {
            "fecha_corte": fecha,
            "fuente": resultado["fuente"],
            "resultado": resultado["resultado"],
            "n_senales": int(resultado.get("n_senales", 0)),
            "detalle": resultado.get("detalle", ""),
            "raw_path": resultado.get("raw_path"),
        },
    )
    conn.commit()


def reemplazar_corrida(conn: sqlite3.Connection, fecha: str, sin_llm: bool, fila: dict) -> int:
    """Reejecutar REEMPLAZA la corrida del dia, no acumula (D-11, AC-6).

    `DELETE` + `INSERT` en una sola transaccion; las `piezas` de la corrida
    vieja caen por `ON DELETE CASCADE`.
    """
    with conn:  # transaccion: o queda la nueva entera, o queda la vieja
        conn.execute("DELETE FROM corridas WHERE fecha_corte = ?", (fecha,))
        cur = conn.execute(
            """
            INSERT INTO corridas
              (fecha_corte, version_reglas, hash_entradas, top10_json, fuentes_json,
               creada_en, sin_llm, estado_corrida)
            VALUES (:fecha_corte, :version_reglas, :hash_entradas, :top10_json, :fuentes_json,
                    :creada_en, :sin_llm, :estado_corrida)
            """,
            {
                "fecha_corte": fecha,
                "version_reglas": fila["version_reglas"],
                "hash_entradas": fila["hash_entradas"],
                "top10_json": fila["top10_json"],
                "fuentes_json": fila.get("fuentes_json", "[]"),
                "creada_en": fila.get("creada_en", fecha),
                "sin_llm": 1 if sin_llm else 0,
                "estado_corrida": fila["estado_corrida"],
            },
        )
    return int(cur.lastrowid)


class HistorialInsuficiente(TransicionInvalida):
    """No hay evidencia para demostrar a que estado debe volver un REINTENTO.

    Subclase a proposito: quien ya capturaba `TransicionInvalida` sigue
    capturandola, y quien necesite distinguir este caso puede hacerlo. El
    contrato lo exige distinguible (H-1).
    """


def _estado_previo_al_fallo(conn: sqlite3.Connection, pieza_id: int) -> str:
    """Estado del que vino el fallo que origino el REINTENTO actual (H-1).

    **Lee el ORIGEN del fallo, no el destino del ultimo evento normal.** La
    version anterior buscaba el ultimo evento cuyo `a_estado` fuera un estado
    normal, y eso solo funciona si antes hubo un ciclo completo que dejara esa
    huella. Con un solo ciclo devolvia `None` **aunque el dato existiera**, y la
    guarda que depende de ella quedaba desactivada. Medido: esperado
    `SELECCIONADA`, obtenido `None`.

    El dato correcto se escribe de forma contemporanea al hecho: cuando una
    pieza cae, la transicion registra `de_estado` con el estado del que venia.
    Desde H-2 ese valor esta contrastado contra el estado persistido al
    escribirlo, asi que es evidencia y no una declaracion del llamador.

    **La identificacion del fallo no es ambigua**: a `REINTENTO` solo se llega
    desde `FALLIDA`, por una ruta unica, asi que el fallo que corresponde es el
    ultimo por orden de bitacora.

    **Sin evidencia, levanta.** No infiere, no elige un valor por omision y no
    fabrica historia: la ausencia de evidencia no autoriza una transicion (H-1).
    """
    fila = conn.execute(
        "SELECT de_estado FROM eventos "
        "WHERE pieza_id = ? AND a_estado = 'FALLIDA' AND de_estado IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        (pieza_id,),
    ).fetchone()
    if fila is None:
        raise HistorialInsuficiente(
            "la pieza %d esta en REINTENTO y su bitacora no demuestra de que estado "
            "cayo: no hay ninguna transicion a FALLIDA con origen registrado. No se "
            "infiere un estado ni se elige uno por omision" % pieza_id
        )
    return str(fila["de_estado"])


def _estado_persistido(conn: sqlite3.Connection, pieza_id: int) -> str | None:
    """Estado real de la pieza, tal como esta en la tabla.

    No introduce fuente de verdad nueva: `piezas.estado` **ya es** la autoridad
    del estado actual. Se lee para poder contrastar contra el origen que declara
    el llamador (H-2).
    """
    fila = conn.execute("SELECT estado FROM piezas WHERE id = ?", (pieza_id,)).fetchone()
    return str(fila["estado"]) if fila is not None else None


def registrar_evento(conn: sqlite3.Connection, en: str, detalle: str) -> None:
    """Hecho de la CORRIDA, no de una pieza: fuente caida, cupo agotado, etc.

    Funcion aparte de `transicionar` a proposito. Un hecho de corrida no tiene
    estado de origen ni de destino, y forzarlo a parecer una transicion obliga
    a inventar un estado que despues nadie sabe interpretar.
    """
    if not detalle or not detalle.strip():
        raise TransicionInvalida("un evento de corrida sin 'detalle' no deja evidencia de nada")
    conn.execute(
        "INSERT INTO eventos (pieza_id, de_estado, a_estado, en, detalle) "
        "VALUES (NULL, NULL, NULL, ?, ?)",
        (en, detalle),
    )
    conn.commit()


class CorreccionInvalida(RuntimeError):
    """Una correccion autorizada sin la evidencia que el contrato exige."""


def registrar_correccion(
    conn: sqlite3.Connection,
    en: str,
    identidad_anterior: str,
    identidad_nueva: str,
    motivo: str,
) -> None:
    """Receipt de una sustitucion deliberada. **No confirma**: es del llamador.

    A diferencia de `registrar_evento`, esta no llama a `commit`. La correccion
    tiene que ser una sola unidad logica con la sustitucion que documenta, y una
    confirmacion propia la partiria en dos: podria quedar receipt sin correccion.

    El motivo es obligatorio y no puede estar vacio, por el mismo criterio que
    `transicionar` aplica a `detalle`: conservar que hubo una sustitucion sin
    conservar por que la vuelve inservible como evidencia.
    """
    if not motivo or not motivo.strip():
        raise CorreccionInvalida(
            "una correccion sin motivo no deja evidencia de nada: se conserva que "
            "hubo sustitucion pero no por que, que es justo lo que hace falta despues"
        )
    if not identidad_anterior or not identidad_nueva:
        raise CorreccionInvalida(
            "el receipt exige la identidad de las DOS decisiones, la sustituida y la "
            "nueva. Sin la anterior no se puede demostrar que fue una correccion"
        )
    conn.execute(
        "INSERT INTO eventos (pieza_id, de_estado, a_estado, en, detalle, "
        " identidad_anterior, identidad_nueva, motivo) "
        "VALUES (NULL, NULL, NULL, ?, ?, ?, ?, ?)",
        (en, "correccion autorizada de la decision del %s" % en,
         identidad_anterior, identidad_nueva, motivo.strip()),
    )


def transicionar(
    conn: sqlite3.Connection,
    pieza_id: int,
    de: str | None,
    a: str,
    en: str,
    detalle: str,
) -> None:
    """Valida la transicion de una pieza y la escribe en `eventos` con su evidencia.

    `detalle` vacio se rechaza: una transicion sin evidencia es justo lo que el
    contrato de ejecuciones prohibe. Para hechos de corrida existe
    `registrar_evento`, que no finge ser una transicion.
    """
    if not detalle or not detalle.strip():
        raise TransicionInvalida(
            "la transicion %s -> %s no trae 'detalle': toda transicion exige evidencia"
            % (de, a)
        )
    if a not in ESTADOS and a not in ESTADOS_FALLO:
        raise TransicionInvalida("estado destino desconocido: %r" % a)

    permitidas = TRANSICIONES.get(de)
    if permitidas is None:
        raise TransicionInvalida("estado de origen desconocido: %r" % de)
    if a not in permitidas:
        raise TransicionInvalida(
            "transicion prohibida %s -> %s; permitidas desde %s: %s"
            % (de, a, de, sorted(permitidas))
        )

    # H-2 · el llamador NO tiene autoridad para declarar un pasado distinto del
    # que la base ya conoce. Sin esto se midio que una pieza en SELECCIONADA
    # podia declarar origen GUIONADA, pasar todas las validaciones, y terminar
    # en GUIONADA saltandose investigacion y verificacion.
    #
    # `de is None` queda FUERA: es el nacimiento, y no hay estado anterior
    # contra el que contrastar. Como debe registrarse un nacimiento pertenece al
    # frente de D-12 y no se resuelve aqui. Limitacion declarada, no omitida.
    if de is not None:
        real = _estado_persistido(conn, pieza_id)
        if real is not None and real != de:
            raise TransicionInvalida(
                "la pieza %d esta en %s, no en %s: el origen declarado no coincide con "
                "el estado persistido. Una transicion no puede afirmar un pasado que la "
                "base no respalda" % (pieza_id, real, de)
            )

    # H-1 · desde REINTENTO solo se vuelve al estado donde se fallo, o se
    # abandona. Sin esta comprobacion, un reintento podria aterrizar en
    # APRENDIDA y saltarse todas las etapas intermedias.
    #
    # `_estado_previo_al_fallo` levanta si la bitacora no lo demuestra: la
    # ausencia de evidencia NO autoriza la transicion.
    if de == "REINTENTO" and a != "FALLIDA_PERMANENTE":
        previo = _estado_previo_al_fallo(conn, pieza_id)
        if a != previo:
            raise TransicionInvalida(
                "desde REINTENTO solo se vuelve a %s (donde fallo) o a FALLIDA_PERMANENTE; "
                "se pidio %s" % (previo, a)
            )

    conn.execute(
        "INSERT INTO eventos (pieza_id, de_estado, a_estado, en, detalle) VALUES (?, ?, ?, ?, ?)",
        (pieza_id, de, a, en, detalle),
    )
    conn.execute("UPDATE piezas SET estado = ? WHERE id = ?", (a, pieza_id))
    conn.commit()


# ─── Helpers de PRODUCCION y DISTRIBUCION (cambio SDD content-factory-produccion-y-distribucion) ──
#
# Se AGREGAN; nada de lo de arriba se modifica. `registrar_pieza` resuelve, SOLO
# para este caso, el frente D-12 ("como se registra un nacimiento") que
# `transicionar` deja fuera: una pieza que NO nace del selector (stock legado ya
# producido) se registra directamente en su estado derivado del ARTEFACTO, con un
# evento de nacimiento (de_estado NULL) que deja la evidencia. Las transiciones
# HACIA ADELANTE siguen pasando por `transicionar` con su validacion intacta.


class NacimientoInvalido(TransicionInvalida):
    """Un nacimiento de pieza sin estado valido o sin evidencia."""


def registrar_pieza(
    conn: sqlite3.Connection,
    *,
    tema_id: int,
    formato: str,
    angulo: str,
    angulo_metodo: str,
    pilar_decision: str,
    estado: str,
    creada_en: str,
    detalle_nacimiento: str,
    objetivo_audiencia: str | None = None,
    objetivo_negocio: str | None = None,
    parent_id: int | None = None,
    run_id: str | None = None,
    corrida_id: int | None = None,
) -> tuple[int, bool]:
    """Alta de una pieza con evento de nacimiento. Devuelve (pieza_id, creada).

    IDEMPOTENTE por `(tema_id, formato)`: si ya hay una pieza con ese tema y
    formato, devuelve su id y `False` sin crear otra. `estado` tiene que ser un
    estado valido de la maquina; el nacimiento se escribe como un evento con
    `de_estado = NULL` y `a_estado = estado` (contemporaneo al hecho, D-13).
    """
    if estado not in ESTADOS:
        raise NacimientoInvalido("estado de nacimiento desconocido: %r" % estado)
    if not detalle_nacimiento or not detalle_nacimiento.strip():
        raise NacimientoInvalido(
            "el nacimiento de una pieza exige 'detalle' con su evidencia (D-13)"
        )
    existente = conn.execute(
        "SELECT id FROM piezas WHERE tema_id = ? AND formato = ?",
        (tema_id, formato),
    ).fetchone()
    if existente is not None:
        return int(existente["id"]), False

    cur = conn.execute(
        "INSERT INTO piezas (tema_id, formato, angulo, angulo_metodo, objetivo_audiencia,"
        " objetivo_negocio, estado, parent_id, run_id, corrida_id, creada_en, pilar_decision)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (tema_id, formato, angulo, angulo_metodo, objetivo_audiencia, objetivo_negocio,
         estado, parent_id, run_id, corrida_id, creada_en, pilar_decision),
    )
    pieza_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO eventos (pieza_id, de_estado, a_estado, en, detalle) VALUES (?, NULL, ?, ?, ?)",
        (pieza_id, estado, creada_en, detalle_nacimiento.strip()),
    )
    conn.commit()
    return pieza_id, True


def publicacion_existente(conn: sqlite3.Connection, pieza_id: int, canal: str) -> bool:
    """¿Ya hay una publicacion registrada para esta pieza en este canal?

    Es la compuerta anti-doble-publicacion: se consulta ANTES de llamar a la API
    del canal.
    """
    fila = conn.execute(
        "SELECT 1 FROM publicaciones WHERE pieza_id = ? AND canal = ?",
        (pieza_id, canal),
    ).fetchone()
    return fila is not None


def obtener_publicacion(conn: sqlite3.Connection, pieza_id: int, canal: str):
    """Devuelve la fila de `publicaciones` (v4) para esta pieza/canal, o None."""
    return conn.execute(
        "SELECT pieza_id, canal, url, publicada_en, identificador_remoto, estado, detalle_error, "
        "programada_para FROM publicaciones WHERE pieza_id = ? AND canal = ?",
        (pieza_id, canal),
    ).fetchone()


def registrar_publicacion(
    conn: sqlite3.Connection,
    *,
    pieza_id: int,
    canal: str,
    publicada_en: str,
    identificador_remoto: str | None = None,
    url: str | None = None,
    estado_pub: str = PUB_CONFIRMADA,
    detalle_error: str | None = None,
    programada_para: str | None = None,
) -> bool:
    """Registra una publicacion (v4) de forma TRANSACCIONAL. Devuelve `creada`.

    En UNA transaccion escribe la fila en `publicaciones` con TODOS sus campos
    (incluido `identificador_remoto` y `estado` de la publicacion) y un receipt
    durable en `eventos` (respaldo legible y reconciliacion). `estado_pub` es un
    estado de PUBLICACION (ver ESTADOS_PUBLICACION), NO el estado de la pieza.

    IDEMPOTENTE por `(pieza_id, canal)`: si ya existe, no re-inserta y devuelve
    `False`. La transicion de la pieza a PUBLICADA la hace el llamador DESPUES:
    asi nunca queda una pieza PUBLICADA sin su fila de publicacion.
    """
    if estado_pub not in ESTADOS_PUBLICACION:
        raise ValueError("estado de publicacion desconocido: %r" % estado_pub)
    if publicacion_existente(conn, pieza_id, canal):
        return False
    receipt = {
        "canal": canal, "identificador_remoto": identificador_remoto, "url": url,
        "estado": estado_pub, "detalle_error": detalle_error, "publicada_en": publicada_en,
        "programada_para": programada_para,
    }
    with conn:  # transaccion: o quedan las dos filas, o ninguna
        conn.execute(
            "INSERT INTO publicaciones "
            "(pieza_id, canal, url, publicada_en, identificador_remoto, estado, detalle_error,"
            " programada_para) VALUES (?,?,?,?,?,?,?,?)",
            (pieza_id, canal, url, publicada_en, identificador_remoto, estado_pub, detalle_error,
             programada_para),
        )
        conn.execute(
            "INSERT INTO eventos (pieza_id, de_estado, a_estado, en, detalle) "
            "VALUES (?, NULL, NULL, ?, ?)",
            (pieza_id, publicada_en,
             "publicacion %s :: %s" % (canal, json.dumps(receipt, ensure_ascii=False, sort_keys=True))),
        )
    return True


def actualizar_estado_publicacion(
    conn: sqlite3.Connection, pieza_id: int, canal: str, estado_pub: str,
    detalle_error: str | None = None, *, url: str | None = None,
    publicada_en: str | None = None,
) -> None:
    """Cambia el `estado` (de publicacion) de una fila ya existente.

    `url` y `publicada_en` solo se sobrescriben si se pasan (v5: al confirmar una
    PROGRAMADA se anota la url y el momento real en que quedo publica).
    """
    if estado_pub not in ESTADOS_PUBLICACION:
        raise ValueError("estado de publicacion desconocido: %r" % estado_pub)
    with conn:
        conn.execute(
            "UPDATE publicaciones SET estado = ?, detalle_error = ?, url = COALESCE(?, url),"
            " publicada_en = COALESCE(?, publicada_en) WHERE pieza_id = ? AND canal = ?",
            (estado_pub, detalle_error, url, publicada_en, pieza_id, canal),
        )


# ─── v5 · Aprobacion individual de operador (Fase A de la fabrica editorial) ──────────
#
# APROBADO no es un estado de `piezas` (el enum D-12 no se toca): es un HECHO en
# `aprobaciones`, atado a la huella del MP4 y del texto. PROGRAMADO y PUBLICADO son
# estados de cada `publicaciones` (por canal). `estado_editorial` los combina.

ESTADOS_APROBABLES = ("PRODUCIDA", "LISTA", "PUBLICADA")   # PUBLICADA: un canal mas


class AprobacionInvalida(ValueError):
    """Una aprobacion sin palabras de operador, sin canales, sin texto o sin archivo."""


def sha256_archivo(ruta: Path | str) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def sha256_texto(texto: str) -> str:
    """Huella del texto de publicacion. Solo se ignoran espacios al inicio y al final."""
    return hashlib.sha256((texto or "").strip().encode("utf-8")).hexdigest()


def _exigir_fecha_con_zona(valor: str) -> str:
    try:
        dt = datetime.fromisoformat(valor)
    except ValueError as e:
        raise AprobacionInvalida("programada_para no es ISO 8601: %r" % valor) from e
    if dt.tzinfo is None:
        raise AprobacionInvalida(
            "programada_para %r no trae zona horaria; sin ella la hora es ambigua" % valor)
    return dt.isoformat()


def aprobacion_vigente(conn: sqlite3.Connection, pieza_id: int):
    return conn.execute(
        "SELECT * FROM aprobaciones WHERE pieza_id = ? AND estado = 'VIGENTE'", (pieza_id,)
    ).fetchone()


def emitir_codigo(
    conn: sqlite3.Connection,
    pieza_id: int,
    *,
    ruta_mp4: Path | str,
    destinatario_user_id: str | int,
    emitido_en: str,
    vigencia_horas: int = 72,
) -> str:
    """Crea el codigo de un solo uso que viaja en el boton APROBAR de Telegram.

    Devuelve el codigo en claro (va en el boton); en la base solo queda su huella. Queda
    atado a la pieza, a la huella ACTUAL del MP4 y al usuario de Telegram destinatario.
    """
    estado = _estado_persistido(conn, pieza_id)
    if estado not in ESTADOS_APROBABLES:
        raise AprobacionInvalida(
            "la pieza %s esta en %s; solo se aprueba desde %s" % (pieza_id, estado, ESTADOS_APROBABLES))
    ruta = Path(ruta_mp4).resolve()
    if not ruta.is_file():
        raise AprobacionInvalida("no existe el MP4 a aprobar: %s" % ruta)
    if not str(destinatario_user_id).strip():
        raise AprobacionInvalida("el codigo necesita un usuario destinatario")
    emitido = datetime.fromisoformat(_exigir_fecha_con_zona(emitido_en))
    codigo = os.urandom(16).hex()   # 128 bits; `secrets` no esta en las dependencias permitidas
    with conn:
        conn.execute(
            "INSERT INTO codigos_aprobacion (pieza_id, sha256_mp4, codigo_sha256, destinatario_user_id,"
            " emitido_en, caduca_en) VALUES (?,?,?,?,?,?)",
            (pieza_id, sha256_archivo(ruta), hashlib.sha256(codigo.encode()).hexdigest(),
             str(destinatario_user_id).strip(), emitido.isoformat(),
             (emitido + timedelta(hours=vigencia_horas)).isoformat()))
    return codigo


def aprobar_pieza(
    conn: sqlite3.Connection,
    pieza_id: int,
    *,
    ruta_mp4: Path | str,
    texto: str,
    canales: list[str],
    otorgada_en: str,
    comprobante: dict,
    programada_para: str | None = None,
) -> int:
    """Registra la aprobacion de operador. SOLO con el comprobante de un boton de Telegram.

    `comprobante` = {'origen': 'telegram', 'codigo', 'user_id', 'chat_id', 'message_id'}.
    El codigo tiene que existir, ser de esta pieza, no estar usado ni caducado, haber
    sido enviado a ESE usuario y corresponder a la huella ACTUAL del MP4. Se gasta en la
    misma transaccion: no sirve dos veces ni para otro video. Una frase, por clara que
    sea, ya no aprueba nada (Etapa 1, 21-oct-2026).
    """
    comp = dict(comprobante or {})
    if comp.get("origen") != "telegram":
        raise AprobacionInvalida("una aprobacion solo nace de un boton de Telegram con su comprobante")
    for campo in ("codigo", "user_id", "chat_id", "message_id"):
        if not str(comp.get(campo) or "").strip():
            raise AprobacionInvalida("al comprobante le falta %r" % campo)
    canales = sorted({c.strip() for c in (canales or []) if c and c.strip()})
    if not canales:
        raise AprobacionInvalida("la aprobacion debe nombrar al menos un canal")
    if not texto or not texto.strip():
        raise AprobacionInvalida("la aprobacion debe incluir el texto que se publicara")
    ruta = Path(ruta_mp4).resolve()
    if not ruta.is_file():
        raise AprobacionInvalida("no existe el MP4 a aprobar: %s" % ruta)
    estado = _estado_persistido(conn, pieza_id)
    if estado not in ESTADOS_APROBABLES:
        raise AprobacionInvalida(
            "la pieza %s esta en %s; solo se aprueba desde %s" % (pieza_id, estado, ESTADOS_APROBABLES))
    prog = _exigir_fecha_con_zona(programada_para) if programada_para else None
    cuando = datetime.fromisoformat(_exigir_fecha_con_zona(otorgada_en))
    huella_codigo = hashlib.sha256(str(comp["codigo"]).encode()).hexdigest()
    cod = conn.execute("SELECT * FROM codigos_aprobacion WHERE codigo_sha256 = ?", (huella_codigo,)).fetchone()
    if cod is None:
        raise AprobacionInvalida("el codigo no existe: no fue emitido por la fabrica")
    if int(cod["pieza_id"]) != int(pieza_id):
        raise AprobacionInvalida("el codigo es de otra pieza (%s)" % cod["pieza_id"])
    if cod["usado_en"]:
        raise AprobacionInvalida("el codigo ya se uso el %s" % cod["usado_en"])
    if cuando > datetime.fromisoformat(cod["caduca_en"]):
        raise AprobacionInvalida("el codigo caduco el %s" % cod["caduca_en"])
    if str(comp["user_id"]).strip() != cod["destinatario_user_id"]:
        raise AprobacionInvalida("el usuario %s no es el destinatario del codigo" % comp["user_id"])
    sha = sha256_archivo(ruta)
    if sha != cod["sha256_mp4"]:
        raise AprobacionInvalida("el MP4 cambio desde que se envio a revision: enviado %s, ahora %s"
                                 % (cod["sha256_mp4"], sha))
    recibo = {"origen": "telegram", "user_id": str(comp["user_id"]), "chat_id": str(comp["chat_id"]),
              "message_id": str(comp["message_id"]), "codigo_sha256": huella_codigo,
              "codigo_id": int(cod["id"])}
    palabras = ("APROBAR por Telegram: user_id=%s chat_id=%s message_id=%s"
                % (recibo["user_id"], recibo["chat_id"], recibo["message_id"]))
    with conn:
        gastado = conn.execute(
            "UPDATE codigos_aprobacion SET usado_en = ?, message_id = ? WHERE id = ? AND usado_en IS NULL",
            (cuando.isoformat(), recibo["message_id"], cod["id"]))
        if gastado.rowcount != 1:
            raise AprobacionInvalida("el codigo se uso al mismo tiempo en otra aprobacion")
        previa = aprobacion_vigente(conn, pieza_id)
        if previa is not None:
            conn.execute(
                "UPDATE aprobaciones SET estado = 'REVOCADA', invalidada_en = ?, "
                "motivo_invalidacion = ? WHERE id = ?",
                (otorgada_en, "reemplazada por una aprobacion nueva", previa["id"]))
        cur = conn.execute(
            "INSERT INTO aprobaciones (pieza_id, sha256_mp4, ruta_mp4, sha256_texto, canales_json,"
            " programada_para, palabras, otorgada_en, estado, origen, comprobante_json)"
            " VALUES (?,?,?,?,?,?,?,?, 'VIGENTE', 'telegram', ?)",
            (pieza_id, sha, str(ruta), sha256_texto(texto), json.dumps(canales),
             prog, palabras, otorgada_en, json.dumps(recibo, sort_keys=True)))
        conn.execute(
            "INSERT INTO eventos (pieza_id, de_estado, a_estado, en, detalle) VALUES (?, NULL, NULL, ?, ?)",
            (pieza_id, otorgada_en, "aprobacion de operador :: sha256_mp4=%s canales=%s "
             "programada_para=%s %s" % (sha, canales, prog, palabras)))
    return int(cur.lastrowid)


def invalidar_aprobacion(conn: sqlite3.Connection, aprobacion_id: int, en: str, motivo: str) -> None:
    with conn:
        fila = conn.execute("SELECT pieza_id FROM aprobaciones WHERE id = ?", (aprobacion_id,)).fetchone()
        conn.execute(
            "UPDATE aprobaciones SET estado = 'INVALIDADA', invalidada_en = ?, motivo_invalidacion = ?"
            " WHERE id = ? AND estado = 'VIGENTE'", (en, motivo, aprobacion_id))
        conn.execute(
            "INSERT INTO eventos (pieza_id, de_estado, a_estado, en, detalle) VALUES (?, NULL, NULL, ?, ?)",
            (fila["pieza_id"] if fila else None, en,
             "aprobacion %d INVALIDADA :: %s" % (aprobacion_id, motivo)))


def verificar_aprobacion(
    conn: sqlite3.Connection,
    pieza_id: int,
    *,
    ruta_mp4: Path | str,
    texto: str,
    canal: str,
    en: str,
    programada_para: str | None = None,
) -> dict:
    """¿Hay aprobacion VIGENTE que cubra ESTE archivo, texto, canal y horario?

    Devuelve {'ok': bool, 'motivo': str, 'aprobacion': fila|None}. Si el MP4
    APROBADO cambio (o desaparecio), la aprobacion se INVALIDA aqui mismo: no hay
    forma de "seguir aprobado" con otro contenido.
    """
    ap = aprobacion_vigente(conn, pieza_id)
    if ap is None:
        return {"ok": False, "aprobacion": None,
                "motivo": "la pieza %d no tiene aprobacion vigente de operador" % pieza_id}
    aprobado = Path(ap["ruta_mp4"])
    actual = sha256_archivo(aprobado) if aprobado.is_file() else None
    if actual != ap["sha256_mp4"]:
        motivo = ("el MP4 aprobado cambio o ya no existe: aprobado sha256=%s, ahora %s"
                  % (ap["sha256_mp4"], actual or "(no existe)"))
        invalidar_aprobacion(conn, int(ap["id"]), en, motivo)
        return {"ok": False, "motivo": motivo, "aprobacion": None}
    a_subir = Path(ruta_mp4).resolve()
    if a_subir != aprobado.resolve():
        sha_subir = sha256_archivo(a_subir) if a_subir.is_file() else None
        if sha_subir != ap["sha256_mp4"]:
            return {"ok": False, "aprobacion": ap,
                    "motivo": "el archivo a subir (%s) no es el aprobado" % a_subir}
    if sha256_texto(texto) != ap["sha256_texto"]:
        return {"ok": False, "aprobacion": ap, "motivo": "el texto a publicar no es el aprobado"}
    if canal not in json.loads(ap["canales_json"]):
        return {"ok": False, "aprobacion": ap,
                "motivo": "el canal %s no esta en la aprobacion (%s)" % (canal, ap["canales_json"])}
    if ap["programada_para"]:
        pedida = _exigir_fecha_con_zona(programada_para) if programada_para else None
        if pedida is None or (datetime.fromisoformat(pedida)
                              != datetime.fromisoformat(ap["programada_para"])):
            return {"ok": False, "aprobacion": ap,
                    "motivo": "el horario pedido (%s) no es el aprobado (%s)"
                    % (programada_para, ap["programada_para"])}
    return {"ok": True, "motivo": "aprobacion vigente %d" % ap["id"], "aprobacion": ap}


def estado_editorial(conn: sqlite3.Connection, pieza_id: int) -> dict:
    """Vista de la cola: estado de la pieza + APROBADO + PROGRAMADO/PUBLICADO por canal.

    No escribe ni re-hashea: el MP4 cambiado lo detecta `verificar_aprobacion`, que
    el distribuidor corre antes de cada envio.
    """
    ap = aprobacion_vigente(conn, pieza_id)
    canales = {}
    for f in conn.execute(
            "SELECT canal, estado, programada_para, identificador_remoto, url FROM publicaciones"
            " WHERE pieza_id = ? ORDER BY canal", (pieza_id,)):
        canales[f["canal"]] = {
            "estado": {PUB_CONFIRMADA: "PUBLICADO", PUB_PROGRAMADA: "PROGRAMADO"}.get(
                f["estado"], f["estado"]),
            "programada_para": f["programada_para"],
            "identificador_remoto": f["identificador_remoto"], "url": f["url"]}
    return {"pieza_id": pieza_id, "estado_pieza": _estado_persistido(conn, pieza_id),
            "aprobado": ap is not None,
            "aprobacion": ({"id": ap["id"], "sha256_mp4": ap["sha256_mp4"],
                            "canales": json.loads(ap["canales_json"]),
                            "programada_para": ap["programada_para"]} if ap is not None else None),
            "canales": canales}
