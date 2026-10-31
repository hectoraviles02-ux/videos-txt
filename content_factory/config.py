# -*- coding: utf-8 -*-
"""Capa 0 — carga declarativa de la identidad, la politica y las fuentes.

Molde: `telegram_bot/config.py::load_config` (dataclass congelada + PyYAML).

Reglas que este modulo hace cumplir, y que no son adorno:

- **RQ-01**: el motor LEE `config/marca.yaml` y nunca escribe en el. Que
  `marca.nombre` valga `PENDIENTE` no bloquea ninguna etapa.
- **RQ-02**: dar de alta o de baja una fuente es editar `config/fuentes.yaml`.
  Si para desactivar `r/podcasting` hubiera que tocar codigo, el requisito
  esta incumplido.
- **RQ-03**: cada fuente declara `pilar_origen`, o se marca `acervo_compartido`.
  Una entrada sin ninguna de las dos cosas es un error de configuracion y
  levanta; no se adivina un valor por omision.
- **RQ-45**: ningun termino de la lista negativa institucional puede aparecer
  en el lexico de un pilar. Si la politica y el lexico se contradicen, la
  carga FALLA nombrando termino y pilar, en vez de callar. Es distinto de
  RQ-44, que filtra senales en tiempo de clasificacion.

Sin imports pesados: solo stdlib y PyYAML (ver `content_factory/__init__.py`).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
RUTA_MARCA = RAIZ / "config" / "marca.yaml"
RUTA_FUENTES = RAIZ / "config" / "fuentes.yaml"

#: Tipos de fuente que la ingestion sabe leer. Una fuente de otro tipo es un
#: error de configuracion: mejor fallar al cargar que descubrirlo de noche.
TIPOS_VALIDOS = frozenset(
    {"trends_mx", "reddit", "hackernews", "github_releases", "rss", "youtube"}
)

#: Parametro obligatorio por tipo. La falta se reporta con nombre y tipo; no
#: existe valor por omision silencioso (tarea 1.3).
PARAMETRO_OBLIGATORIO = {
    "trends_mx": "url",
    "reddit": "sub",
    "hackernews": "url",
    "github_releases": "repo",
    "rss": "url",
    "youtube": "consulta",
}


class ConfigInvalida(ValueError):
    """La configuracion declarada no se puede usar. Se dice, no se disimula."""


@dataclass(frozen=True)
class Pilar:
    clave: str
    nombre: str
    cuota_base: float
    lexico: tuple[str, ...]


@dataclass(frozen=True)
class Pesos:
    w_dem: float
    w_est: float
    k_saturacion: float
    decimales: int


@dataclass(frozen=True)
class Marca:
    nombre: str
    correo: str
    pilares: dict[str, Pilar]
    cadencia: dict[int, str]
    pesos: Pesos
    lista_negativa: tuple[str, ...]
    tope_ventana: int
    tope_pilar_ventana: int
    version_reglas: str


@dataclass(frozen=True)
class FuenteConfig:
    nombre: str
    tipo: str
    pilar_origen: str | None
    parametros: dict[str, str]
    activa: bool


def _leer_yaml(ruta: Path) -> dict:
    if not ruta.is_file():
        raise ConfigInvalida("no existe el archivo de configuracion: %s" % ruta)
    with open(ruta, encoding="utf-8") as fh:
        datos = yaml.safe_load(fh)
    if not isinstance(datos, dict):
        raise ConfigInvalida("%s no contiene un mapa YAML en la raiz" % ruta)
    return datos


def _exigir(datos: dict, clave: str, ruta: Path):
    if clave not in datos:
        raise ConfigInvalida("falta la clave '%s' en %s" % (clave, ruta))
    return datos[clave]


def cargar_marca(ruta: Path | None = None) -> Marca:
    """Lee `config/marca.yaml`. El lexico se cose despues, en `cargar_config`."""
    ruta = ruta or RUTA_MARCA
    datos = _leer_yaml(ruta)

    bloque_marca = _exigir(datos, "marca", ruta)
    bloque_pesos = _exigir(datos, "pesos", ruta)
    bloque_pilares = _exigir(datos, "pilares", ruta)
    bloque_cadencia = _exigir(datos, "cadencia", ruta)

    pesos = Pesos(
        w_dem=float(_exigir(bloque_pesos, "w_dem", ruta)),
        w_est=float(_exigir(bloque_pesos, "w_est", ruta)),
        k_saturacion=float(_exigir(bloque_pesos, "k_saturacion", ruta)),
        decimales=int(_exigir(bloque_pesos, "decimales", ruta)),
    )

    pilares: dict[str, Pilar] = {}
    for clave, cuerpo in bloque_pilares.items():
        if not isinstance(cuerpo, dict):
            raise ConfigInvalida("el pilar %s no es un mapa en %s" % (clave, ruta))
        pilares[clave] = Pilar(
            clave=clave,
            nombre=str(_exigir(cuerpo, "nombre", ruta)),
            cuota_base=float(_exigir(cuerpo, "cuota_base", ruta)),
            lexico=(),  # lo llena cargar_config desde fuentes.yaml
        )
    if not pilares:
        raise ConfigInvalida("no hay pilares declarados en %s" % ruta)

    # weekday() de Python: 0 = lunes. Las llaves del YAML pueden venir como
    # enteros o como cadenas; se normalizan a int para que el lector no dependa
    # de como las serializo el editor.
    cadencia = {int(k): str(v) for k, v in bloque_cadencia.items()}
    faltan = sorted(set(range(7)) - set(cadencia))
    if faltan:
        raise ConfigInvalida(
            "la cadencia de %s no cubre los dias %s (weekday 0=lunes)" % (ruta, faltan)
        )

    lista_negativa = tuple(
        str(t).strip().lower() for t in datos.get("lista_negativa", []) if str(t).strip()
    )

    return Marca(
        nombre=str(_exigir(bloque_marca, "nombre", ruta)),
        correo=str(_exigir(bloque_marca, "correo", ruta)),
        pilares=pilares,
        cadencia=cadencia,
        pesos=pesos,
        lista_negativa=lista_negativa,
        tope_ventana=int(_exigir(datos, "tope_ventana", ruta)),
        tope_pilar_ventana=int(_exigir(datos, "tope_pilar_ventana", ruta)),
        version_reglas=str(_exigir(datos, "version_reglas", ruta)),
    )


def cargar_fuentes(ruta: Path | None = None) -> tuple[FuenteConfig, ...]:
    """Lee `config/fuentes.yaml` y devuelve las fuentes **activas**, en orden estable.

    El orden es alfabetico por `nombre` a proposito: el orden de la lista en el
    archivo no debe poder alterar el resultado de una corrida.
    """
    ruta = ruta or RUTA_FUENTES
    datos = _leer_yaml(ruta)
    crudas = _exigir(datos, "fuentes", ruta)
    if not isinstance(crudas, list) or not crudas:
        raise ConfigInvalida("'fuentes' de %s debe ser una lista no vacia" % ruta)

    fuentes: list[FuenteConfig] = []
    vistos: set[str] = set()
    for cruda in crudas:
        if not isinstance(cruda, dict):
            raise ConfigInvalida("una entrada de 'fuentes' no es un mapa en %s" % ruta)

        nombre = str(_exigir(cruda, "nombre", ruta))
        if nombre in vistos:
            raise ConfigInvalida("fuente duplicada '%s' en %s" % (nombre, ruta))
        vistos.add(nombre)

        tipo = str(_exigir(cruda, "tipo", ruta))
        if tipo not in TIPOS_VALIDOS:
            raise ConfigInvalida(
                "la fuente '%s' declara tipo '%s', que no existe. Validos: %s"
                % (nombre, tipo, sorted(TIPOS_VALIDOS))
            )

        pilar_origen = cruda.get("pilar_origen")
        acervo = bool(cruda.get("acervo_compartido", False))
        if pilar_origen is None and not acervo:
            raise ConfigInvalida(
                "la fuente '%s' no declara 'pilar_origen' ni 'acervo_compartido: true'. "
                "Una de las dos es obligatoria (RQ-03)" % nombre
            )
        if pilar_origen is not None and acervo:
            raise ConfigInvalida(
                "la fuente '%s' declara 'pilar_origen' Y 'acervo_compartido': "
                "son excluyentes (RQ-03)" % nombre
            )

        parametros = cruda.get("parametros") or {}
        if not isinstance(parametros, dict):
            raise ConfigInvalida("'parametros' de '%s' no es un mapa en %s" % (nombre, ruta))
        parametros = {str(k): str(v) for k, v in parametros.items()}

        obligatorio = PARAMETRO_OBLIGATORIO[tipo]
        if not parametros.get(obligatorio):
            raise ConfigInvalida(
                "la fuente '%s' de tipo '%s' no trae el parametro obligatorio '%s'"
                % (nombre, tipo, obligatorio)
            )

        if not bool(cruda.get("activa", True)):
            continue  # RQ-02: desactivar es editar el YAML, no tocar codigo

        fuentes.append(
            FuenteConfig(
                nombre=nombre,
                tipo=tipo,
                pilar_origen=str(pilar_origen) if pilar_origen is not None else None,
                parametros=parametros,
                activa=True,
            )
        )

    return tuple(sorted(fuentes, key=lambda f: f.nombre))


def _cargar_lexico(ruta: Path) -> dict[str, tuple[str, ...]]:
    datos = _leer_yaml(ruta)
    crudo = datos.get("lexico") or {}
    if not isinstance(crudo, dict):
        raise ConfigInvalida("'lexico' de %s debe ser un mapa pilar -> lista" % ruta)
    return {
        str(pilar): tuple(str(t).strip().lower() for t in (terminos or []) if str(t).strip())
        for pilar, terminos in crudo.items()
    }


def cargar_config(
    ruta_marca: Path | None = None,
    ruta_fuentes: Path | None = None,
) -> tuple[Marca, tuple[FuenteConfig, ...]]:
    """Carga marca y fuentes, cose el lexico sobre los pilares y valida RQ-45.

    Devuelve la marca con `Pilar.lexico` ya lleno. Es el punto de entrada que
    debe usar el resto del motor; `cargar_marca` sola deja el lexico vacio.
    """
    ruta_marca = ruta_marca or RUTA_MARCA
    ruta_fuentes = ruta_fuentes or RUTA_FUENTES

    marca = cargar_marca(ruta_marca)
    fuentes = cargar_fuentes(ruta_fuentes)
    lexico = _cargar_lexico(ruta_fuentes)

    # RQ-45: la politica y el lexico no pueden contradecirse. Se falla nombrando
    # el termino y el pilar, porque un error de configuracion que no dice donde
    # esta cuesta mas que el error mismo.
    for pilar, terminos in lexico.items():
        for termino in terminos:
            if termino in marca.lista_negativa:
                raise ConfigInvalida(
                    "el termino institucional '%s' aparece en el lexico del pilar %s de %s; "
                    "esta en marca.lista_negativa y no puede usarse para clasificar (RQ-45)"
                    % (termino, pilar, ruta_fuentes)
                )

    desconocidos = sorted(set(lexico) - set(marca.pilares))
    if desconocidos:
        raise ConfigInvalida(
            "el lexico de %s declara pilares que no existen en %s: %s"
            % (ruta_fuentes, ruta_marca, desconocidos)
        )

    for fuente in fuentes:
        if fuente.pilar_origen is not None and fuente.pilar_origen not in marca.pilares:
            raise ConfigInvalida(
                "la fuente '%s' apunta al pilar '%s', que no existe en %s"
                % (fuente.nombre, fuente.pilar_origen, ruta_marca)
            )

    pilares = {
        clave: Pilar(
            clave=p.clave,
            nombre=p.nombre,
            cuota_base=p.cuota_base,
            lexico=lexico.get(clave, ()),
        )
        for clave, p in marca.pilares.items()
    }
    marca_completa = Marca(
        nombre=marca.nombre,
        correo=marca.correo,
        pilares=pilares,
        cadencia=marca.cadencia,
        pesos=marca.pesos,
        lista_negativa=marca.lista_negativa,
        tope_ventana=marca.tope_ventana,
        tope_pilar_ventana=marca.tope_pilar_ventana,
        version_reglas=marca.version_reglas,
    )
    return marca_completa, fuentes


def version_reglas(marca: Marca, fuentes: tuple[FuenteConfig, ...]) -> str:
    """Firma de las reglas vigentes: `<version_reglas>+<sha256[:12]>`.

    La etiqueta declarada la pone el operador y puede olvidarse de subirla; el
    digest no. Dos corridas con pesos, cuotas, topes, lexico o fuentes distintos
    producen firmas distintas aunque `version_reglas` no haya cambiado, y asi
    `scores.version_reglas` nunca miente sobre con que reglas se puntuo.
    """
    huella = {
        "version_reglas": marca.version_reglas,
        "tope_ventana": marca.tope_ventana,
        "tope_pilar_ventana": marca.tope_pilar_ventana,
        "pesos": [
            marca.pesos.w_dem,
            marca.pesos.w_est,
            marca.pesos.k_saturacion,
            marca.pesos.decimales,
        ],
        "cadencia": [marca.cadencia[d] for d in sorted(marca.cadencia)],
        "lista_negativa": sorted(marca.lista_negativa),
        "pilares": [
            [p.clave, p.nombre, p.cuota_base, sorted(p.lexico)]
            for p in sorted(marca.pilares.values(), key=lambda x: x.clave)
        ],
        "fuentes": [
            [f.nombre, f.tipo, f.pilar_origen, sorted(f.parametros.items())]
            for f in sorted(fuentes, key=lambda x: x.nombre)
        ],
    }
    serializado = json.dumps(huella, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(serializado.encode("utf-8")).hexdigest()[:12]
    return "%s+%s" % (marca.version_reglas, digest)
