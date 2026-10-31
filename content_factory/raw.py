# -*- coding: utf-8 -*-
"""`raw/AAAA-MM-DD/` - originales inmutables y entrada unica del replay.

Dos cosas a la vez, y conviene no confundirlas:

1. **Principio 6 de `CLAUDE.md`**: lo descargado se guarda y no se edita.
2. **Cierre 5 de no-determinismo (RQ-37)**: el `replay` lee de aqui y solo de
   aqui. Si un crudo se sobrescribiera en silencio, el replay de hace tres
   semanas dejaria de reproducir lo que reporto aquel dia, y nadie se enteraria.

De ahi que `guardar_crudo` **nunca** sobrescriba sin pedirlo explicitamente:
sobrescribir es la operacion peligrosa, asi que es la que lleva bandera.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

from content_factory.ingestion.base import ResultadoFuente

RAIZ = Path(__file__).resolve().parent.parent

#: Ignorado por git (ver `.gitignore`): son datos, no codigo.
BASE_RAW = RAIZ / "content_factory" / "raw"

MANIFIESTO = "_manifiesto.json"
DERIVADOS = "_derivados.json"


def _base(base: Path | None) -> Path:
    return Path(base) if base is not None else BASE_RAW


def sanear(fuente: str) -> str:
    """`reddit:r/LocalLLaMA` -> `reddit_r_LocalLLaMA`.

    `/` y `:` no son nombres de archivo validos en Windows; se sustituyen por
    `_`. No se recorta ni se hashea: el nombre debe seguir siendo legible por
    un humano que abre la carpeta a las 2 de la manana.
    """
    limpio = str(fuente)
    for malo in ("/", ":", "\\", "?", "*", '"', "<", ">", "|"):
        limpio = limpio.replace(malo, "_")
    return limpio.strip() or "_sin_nombre"


def dir_dia(fecha_corte: date | str, base: Path | None = None) -> Path:
    return _base(base) / str(fecha_corte)


def ruta_crudo(
    fecha_corte: date | str, fuente: str, ext: str, base: Path | None = None
) -> Path:
    return dir_dia(fecha_corte, base) / ("%s.%s" % (sanear(fuente), ext.lstrip(".")))


def existe_crudo(
    fecha_corte: date | str, fuente: str, ext: str, base: Path | None = None
) -> bool:
    """Si el crudo del dia ya esta, la fuente no se vuelve a pedir.

    Es lo que hace barata la reejecucion y lo que permite el replay sin red.
    """
    return ruta_crudo(fecha_corte, fuente, ext, base).is_file()


def guardar_crudo(
    fecha_corte: date | str,
    fuente: str,
    ext: str,
    cuerpo: str,
    base: Path | None = None,
    *,
    refrescar: bool = False,
) -> str:
    """Escribe el crudo y devuelve su `raw_path` RELATIVO (`AAAA-MM-DD/x.ext`).

    Relativo y no absoluto a proposito: el `raw_path` se persiste en `senales` y
    en `ingestas`, y una ruta absoluta ataria la base de datos a la maquina que
    la escribio.

    `refrescar=False` (el caso normal) respeta el original: si el archivo ya
    existe, no lo toca y devuelve la misma ruta. La bandera es la desviacion
    declarada frente a la firma del diseno, que no la listaba; sin ella no habria
    forma de expresar "nunca sobrescribe SIN `--refrescar`".
    """
    destino = ruta_crudo(fecha_corte, fuente, ext, base)
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.is_file() and not refrescar:
        return relativo(destino, base)
    destino.write_text(cuerpo, encoding="utf-8")
    return relativo(destino, base)


def relativo(ruta: Path, base: Path | None = None) -> str:
    return Path(ruta).relative_to(_base(base)).as_posix()


def leer_crudo(raw_path: str, base: Path | None = None) -> str:
    return (_base(base) / str(raw_path)).read_text(encoding="utf-8")


def escribir_manifiesto(
    fecha_corte: date | str, resultados: list[ResultadoFuente], base: Path | None = None
) -> str:
    """El estado de las fuentes del dia, junto al crudo que lo produjo.

    Se ordena por nombre de fuente y se serializa canonicamente: dos corridas con
    el mismo resultado deben dar el mismo archivo byte a byte (cierre 4 de
    RQ-37).
    """
    destino = dir_dia(fecha_corte, base) / MANIFIESTO
    destino.parent.mkdir(parents=True, exist_ok=True)
    cuerpo = json.dumps(
        [r.como_dict() for r in sorted(resultados, key=lambda x: x.fuente)],
        sort_keys=True,
        ensure_ascii=False,
        indent=2,
    )
    destino.write_text(cuerpo, encoding="utf-8")
    return relativo(destino, base)


def leer_manifiesto(fecha_corte: date | str, base: Path | None = None) -> list[ResultadoFuente]:
    ruta = dir_dia(fecha_corte, base) / MANIFIESTO
    if not ruta.is_file():
        return []
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    return [
        ResultadoFuente(
            fuente=d["fuente"],
            resultado=d["resultado"],
            n_senales=int(d.get("n_senales", 0)),
            detalle=d.get("detalle", ""),
            raw_path=d.get("raw_path"),
        )
        for d in datos
    ]


def leer_ventana(fecha_corte: date | str, base: Path | None = None) -> list[str] | None:
    """Pilares de la ventana de esa noche, o `None` si el archivo no la trae.

    **Por que existe** (extension de D-19, hallada corriendo el replay). La
    seleccion de una noche depende de las piezas de las noches ANTERIORES: la
    compuerta de RQ-26 cuenta sobre las ultimas 10. Esa historia no vive en
    `raw/D/`, vive en la base. Un replay sobre una base temporal vacia la
    perderia, y un dia con historial daria un TOP distinto al que se publico,
    sin que nada avisara.

    Se comprobo midiendo: el replay de tres dias devolvia 5, 5 y 5 piezas cuando
    lo publicado habia sido 5, 1 y 0. Por eso la ventana se persiste junto al
    resto del estado derivado. `None` distingue "archivo viejo, sin el campo" de
    "esa noche la ventana estaba vacia", que es `[]`.
    """
    ruta = dir_dia(fecha_corte, base) / DERIVADOS
    if not ruta.is_file():
        return None
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    if not isinstance(datos, dict) or "ventana" not in datos:
        return None
    return list(datos["ventana"])


def escribir_derivados(
    fecha_corte: date | str,
    por_tema: dict[str, dict],
    base: Path | None = None,
    ventana: list[str] | None = None,
    version_reglas: str | None = None,
) -> str:
    """`raw/D/_derivados.json` - lo que el replay NO debe recalcular (D-19).

    **Escritor UNICO.** El selector llama aqui en vez de escribir por su cuenta:
    dos escritores acabarian discrepando en la forma del archivo y el replay
    leeria en el nivel equivocado sin que ninguna prueba lo notara.

    Envuelve en `{"por_tema": ...}` como fija el diseno, y **intercambia de
    forma atomica**: temporal en el MISMO directorio y `os.replace`. En el mismo
    directorio porque `os.replace` solo es atomico dentro del mismo sistema de
    archivos; un temporal en el temporal del sistema podria caer en otro volumen
    y dejaria de serlo justo cuando importa.

    Nunca puede quedar un `_derivados.json` a medias: o esta el viejo entero o
    esta el nuevo entero. De eso depende que una corrida no quede registrada sin
    su estado derivado (tarea 6.6).
    """
    destino = dir_dia(fecha_corte, base) / DERIVADOS
    destino.parent.mkdir(parents=True, exist_ok=True)
    # `version_reglas` se omite cuando no viene, en vez de escribirse vacia:
    # AUSENTE y VACIA tienen que ser distinguibles. Un archivo sin la llave es
    # legacy; uno con la llave en "" seria una firma inventada.
    datos = {"por_tema": por_tema, "ventana": list(ventana or [])}
    if version_reglas:
        datos["version_reglas"] = str(version_reglas)
    cuerpo = json.dumps(datos, sort_keys=True, ensure_ascii=False, indent=2)
    tmp = destino.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(cuerpo)
    os.replace(tmp, destino)
    return relativo(destino, base)


def leer_version_reglas(fecha_corte: date | str, base: Path | None = None) -> str | None:
    """Identidad de configuracion con la que se materializo `D`, o `None`.

    **Es la AUTORIDAD HISTORICA de D-19 etapa 2A.** Vive junto a los crudos y no
    en la base, porque el replay no abre la base productiva a proposito: si la
    identidad viviera solo alli, no seria alcanzable desde donde hay que
    compararla.

    `None` significa LEGACY: el dia se materializo antes de que esta identidad
    se guardara. **No se rellena.** Inventar la firma de un dia pasado seria
    fabricar la evidencia que este contrato existe para exigir.
    """
    ruta = dir_dia(fecha_corte, base) / DERIVADOS
    if not ruta.is_file():
        return None
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    if not isinstance(datos, dict):
        return None
    valor = datos.get("version_reglas")
    return str(valor) if valor else None


def leer_derivados(fecha_corte: date | str, base: Path | None = None) -> dict[str, dict]:
    """Devuelve el mapa `hash_titulo -> datos`, ya desenvuelto.

    El llamador no deberia tener que saber que el archivo trae una capa
    `por_tema`: si lo supiera, cada sitio que lee tendria que acordarse, y
    tarde o temprano uno se olvidaria.
    """
    ruta = dir_dia(fecha_corte, base) / DERIVADOS
    if not ruta.is_file():
        return {}
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    return datos.get("por_tema", datos) if isinstance(datos, dict) else {}
