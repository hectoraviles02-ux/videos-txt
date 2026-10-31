# -*- coding: utf-8 -*-
"""Contrato comun de las seis fuentes: `Senal`, `ResultadoFuente` y el hash.

Las tres cosas que este modulo hace cumplir, y por que:

- **RQ-14 - llave natural sha256 COMPLETO.** `scripts/comun.py::calcular_hash`
  recorta a `sha1[:12]`; con miles de senales acumuladas el riesgo de colision
  no es aceptable para una llave natural. `normalizar_url` si se reutiliza tal
  cual, sin tocarla.
- **RQ-07 - `detectada_en = fecha_corte`, jamas `datetime.now()`.** Si la fecha
  viniera del reloj, el replay dejaria de reproducir (cierre 1 de RQ-37). No hay
  un solo `now()` en este paquete de ingestion.
- **RQ-41 - una senal sin `fuente` o sin `texto` no existe.** Se rechaza al
  construirla, no se persiste vacia para "revisarla luego".

**Cierre de hueco aplicado (spec RQ-07 sobre design section 1)**: `raw_path` NO es
campo de `Senal`. El diseno lo listaba, pero el mismo diseno exige que `parsear`
sea pura (`str -> Senal[]`), y un parser puro no puede saber donde se volco el
crudo. El `raw_path` viaja en `ResultadoFuente` y lo estampa la capa de
persistencia al insertar.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from dataclasses import dataclass

from scripts.comun import normalizar_url  # RQ-14: se reutiliza tal cual

#: Valores validos de `ResultadoFuente.resultado`. La taxonomia se amplio frente
#: al diseno original (huecos H-1 y H-2): `omitida` fundia tres causas distintas
#: -fuente desactivada, cuota externa agotada, crudo del dia ya presente- y un
#: crudo malformado caia en `error_red`, que es el diagnostico opuesto.
RESULTADOS = (
    "ok",
    "vacia",
    "http_429",
    "http_403",
    "http_304",
    "error_red",
    "parse_error",
    "desactivada",
    "omitida_por_cuota",
    "desde_raw",
    "omitida",  # historico legible; YA NO SE EMITE
)

#: Las cinco categorias auditables. Las cuatro primeras son por fuente; la
#: quinta (corrida incompleta) es de la corrida y la decide `estado_corrida`.
CATEGORIA_CON_RESULTADOS = "consultada_con_resultados"
CATEGORIA_SIN_RESULTADOS = "consultada_sin_resultados"
CATEGORIA_FALLIDA = "fallida"
CATEGORIA_NO_CONSULTADA = "no_consultada"

_CATEGORIAS: dict[str, str] = {
    # La fuente aporto senales. `http_304` y `desde_raw` cuentan aqui: las
    # senales estan presentes, servidas de la cache local. NUNCA son una falla.
    "ok": CATEGORIA_CON_RESULTADOS,
    "http_304": CATEGORIA_CON_RESULTADOS,
    "desde_raw": CATEGORIA_CON_RESULTADOS,
    # Respondio, sin items.
    "vacia": CATEGORIA_SIN_RESULTADOS,
    # Se intento y salio mal. Esto y solo esto vuelve incompleta la corrida.
    "http_429": CATEGORIA_FALLIDA,
    "http_403": CATEGORIA_FALLIDA,
    "error_red": CATEGORIA_FALLIDA,
    "parse_error": CATEGORIA_FALLIDA,
    # Nunca se pregunto. No es falla: es una decision declarada.
    "desactivada": CATEGORIA_NO_CONSULTADA,
    "omitida_por_cuota": CATEGORIA_NO_CONSULTADA,
    "omitida": CATEGORIA_NO_CONSULTADA,
}


class SenalInvalida(ValueError):
    """Una senal sin fuente o sin texto. Se rechaza, no se rellena (RQ-41)."""


@dataclass(frozen=True)
class Senal:
    """Lo que toda fuente produce, sea Reddit, un RSS o yt-dlp (RQ-07)."""

    fuente: str
    texto: str
    url: str | None
    publicada_en: str | None
    detectada_en: str
    extra_json: str
    hash: str

    def __post_init__(self) -> None:
        if not str(self.fuente).strip():
            raise SenalInvalida("senal sin 'fuente'")
        if not str(self.texto).strip():
            raise SenalInvalida("senal sin 'texto' (fuente=%s)" % self.fuente)


@dataclass(frozen=True)
class ResultadoFuente:
    """Que le paso a una fuente ese dia. Es lo que hace auditable el replay."""

    fuente: str
    resultado: str
    n_senales: int = 0
    detalle: str = ""
    raw_path: str | None = None

    def __post_init__(self) -> None:
        if self.resultado not in RESULTADOS:
            raise ValueError(
                "resultado '%s' fuera de la taxonomia: %s" % (self.resultado, list(RESULTADOS))
            )

    @property
    def categoria(self) -> str:
        return categoria_auditable(self.resultado)

    def como_dict(self) -> dict:
        return {
            "fuente": self.fuente,
            "resultado": self.resultado,
            "n_senales": int(self.n_senales),
            "detalle": self.detalle,
            "raw_path": self.raw_path,
        }


def categoria_auditable(resultado: str) -> str:
    """Los once valores de la taxonomia colapsados en las cuatro por fuente."""
    if resultado not in _CATEGORIAS:
        raise ValueError("resultado desconocido: %s" % resultado)
    return _CATEGORIAS[resultado]


def estado_corrida(resultados: list[ResultadoFuente]) -> str:
    """Quinto caso auditable, a nivel de corrida: `completa` o `incompleta`.

    Una corrida es incompleta si **alguna** fuente fallo. Una fuente vacia o no
    consultada no la vuelve incompleta: se pregunto y no habia, o se decidio no
    preguntar. Ambas son estados sanos y declarados.
    """
    if any(r.categoria == CATEGORIA_FALLIDA for r in resultados):
        return "incompleta"
    return "completa"


_PUNTUACION = re.compile(r"[^\w\s]", re.UNICODE)
_ESPACIOS = re.compile(r"\s+", re.UNICODE)


def fecha_canonica_utc(valor: str | None) -> str | None:
    """Instante UTC canonico `AAAA-MM-DDTHH:MM:SSZ`, o `None` si no se sabe.

    **Frente de senales, contrato V2-B.** La fecha de una senal tiene dos
    responsabilidades y aqui solo se atiende una: el INSTANTE que se usa para
    decidir. La otra -la fecha cruda como parte de la identidad de una senal sin
    URL- no pasa por esta funcion y no cambia.

    Por que hace falta: `publicada_en` guarda ISO-8601 en unas fuentes y
    RFC-822 en otras (RSS, Trends), y la comparacion textual de las consultas
    solo funcionaba con ISO. Medido sobre el corpus del dia 14: una noticia del
    MISMO dia quedaba fuera del dia porque `"Sun, 14 Se"` no es menor que
    `"2026-09-14"`.

    **Devuelve `None`, y no inventa, cuando el instante no es inequivoco:**

    - ISO sin zona, o fecha sin hora: no se sabe en que huso se escribio;
    - RFC-822 con `-0000`: RFC 2822 reserva ese valor para decir *zona
      desconocida*, que no es lo mismo que UTC;
    - cualquier cosa que no se pueda leer.

    En esos casos la decision temporal cae a `detectada_en`, que es un dato
    propio de la corrida y no una suposicion. Las fracciones de segundo se
    descartan: la decision es por dia.
    """
    from datetime import datetime, timezone
    from email.utils import parsedate_to_datetime

    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto:
        return None

    instante = None
    try:
        instante = datetime.fromisoformat(texto)
    except ValueError:
        try:
            instante = parsedate_to_datetime(texto)
        except (TypeError, ValueError, IndexError):
            return None

    # Sin zona -incluido el `-0000` de RFC 2822, que `parsedate_to_datetime`
    # devuelve sin zona a proposito- no hay instante que convertir.
    if instante is None or instante.tzinfo is None:
        return None
    return instante.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalizar_titulo(texto: str) -> str:
    """Minusculas, sin acentos, sin puntuacion, espacios colapsados.

    Sirve para dos cosas distintas y por eso vive aqui: la preimagen del hash de
    Trends (que no tiene URL) y, mas adelante, el `hash_titulo` de los temas.
    """
    plano = unicodedata.normalize("NFKD", str(texto or ""))
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    plano = _PUNTUACION.sub(" ", plano.lower())
    return _ESPACIOS.sub(" ", plano).strip()


def hash_senal(fuente: str, url: str | None, texto: str, publicada_en: str | None) -> str:
    """sha256 hexadecimal completo (64 caracteres). Nunca recortado (RQ-14).

    Con `url`, la preimagen es `normalizar_url(url)`. Sin `url` (Trends MX), es
    `fuente + "|" + titulo_normalizado + "|" + publicada_en`.

    Que la preimagen con URL NO incluya la fuente es deliberado: el mismo enlace
    visto por Reddit y por un RSS es la misma senal, y debe deduplicarse.
    """
    if url and str(url).strip():
        preimagen = normalizar_url(str(url))
    else:
        preimagen = "%s|%s|%s" % (fuente, normalizar_titulo(texto), publicada_en or "")
    return hashlib.sha256(preimagen.encode("utf-8")).hexdigest()


def construir(
    fuente: str,
    texto: str,
    *,
    url: str | None,
    publicada_en: str | None,
    fecha_corte,
    extra: dict | None = None,
) -> Senal:
    """Atajo unico para que las seis fuentes construyan igual.

    Centraliza tres cosas que si cada modulo repitiera acabarian divergiendo:
    `detectada_en = str(fecha_corte)` (RQ-07), la serializacion canonica de
    `extra_json` (cierre 4 de RQ-37) y el calculo del hash.
    """
    import json

    return Senal(
        fuente=fuente,
        texto=str(texto).strip(),
        url=url,
        publicada_en=publicada_en,
        detectada_en=str(fecha_corte),
        extra_json=json.dumps(extra or {}, sort_keys=True, ensure_ascii=False),
        hash=hash_senal(fuente, url, texto, publicada_en),
    )


class EtagStore:
    """Acceso a `cache_http`: el `ETag` debe sobrevivir entre corridas (RQ-11).

    **Adicion no prevista en el diseno**: `design.md` nombra `EtagStore` en la
    firma de `recolectar` pero no lo define en ninguna parte, y `db.py` shippeo
    la tabla `cache_http` sin accesor. Se implementa aqui, como envoltorio
    delgado sobre una conexion, para no reabrir `db.py` (que ya esta demostrado)
    y para dejar la direccion de dependencia en un solo sentido.

    `remaining` vive en memoria a proposito: es la cuota *de esta corrida*, leida
    de la respuesta anterior (design section 5.2 punto 4). Persistirla entre
    procesos daria un numero viejo, que es peor que no tener numero.
    """

    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self._conn = conn
        self._memoria: dict[str, tuple[str, str]] = {}
        self.remaining: int | None = None

    def leer(self, url: str) -> tuple[str, str] | None:
        """`(etag, raw_path)` de esa URL, o `None` si nunca se vio."""
        if self._conn is not None:
            fila = self._conn.execute(
                "SELECT etag, raw_path FROM cache_http WHERE url = ?", (url,)
            ).fetchone()
            if fila is not None:
                return (fila["etag"], fila["raw_path"])
            return None
        return self._memoria.get(url)

    def guardar(self, url: str, etag: str, raw_path: str, visto_en: str) -> None:
        if not etag:
            return  # sin ETag no hay nada que ahorrar; no se ensucia la tabla
        if self._conn is not None:
            self._conn.execute(
                """
                INSERT INTO cache_http (url, etag, visto_en, raw_path)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                  etag = excluded.etag, visto_en = excluded.visto_en,
                  raw_path = excluded.raw_path
                """,
                (url, etag, visto_en, raw_path),
            )
            self._conn.commit()
        else:
            self._memoria[url] = (etag, raw_path)
