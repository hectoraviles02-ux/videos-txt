# -*- coding: utf-8 -*-
"""Punto unico de salida a la red del motor de temas.

Molde: `synthesis/investigacion.py::_get`. **Desviacion declarada (D-08)**: aquel
devuelve `str | None` y se traga el status y las cabeceras; la ingestion necesita
exactamente eso que se traga —`x-ratelimit-reset` de Reddit, `X-RateLimit-Remaining`
y `ETag` de GitHub, y el 304—. Por eso aqui la respuesta es una dataclass con
status, texto, cabeceras y error.

Invariantes que este modulo sostiene:

- **Nunca levanta por causa de red.** Una excepcion de `requests` se convierte en
  `Respuesta(status=0, error=<mensaje>)`. Es lo que permite RQ-09: una fuente
  caida no derriba la corrida.
- **Las cabeceras llegan siempre en minusculas.** HTTP las define insensibles a
  mayusculas, y el codigo que lee `x-ratelimit-reset` no debe depender de como
  las escribio el servidor ese dia.
- **`requests` se importa dentro de la funcion**, no al cargar el modulo. Asi
  `content_factory.ingestion` puede registrarse sin arrastrar `requests`
  (RQ-53, D-15).
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: User-Agent propio, como `investigacion.CABECERAS`. Se declara quien llama:
#: un raspador anonimo es lo primero que un servidor bloquea.
CABECERAS: dict[str, str] = {
    "User-Agent": "VIDEOS-TXT-content-factory/0.1 (motor de temas)"
}

#: Segundos. Mismo valor que `synthesis/investigacion.TIMEOUT`.
TIMEOUT: int = 30


@dataclass(frozen=True)
class Respuesta:
    """Lo que devuelve un punto de red. `status == 0` significa excepcion."""

    status: int
    texto: str | None = None
    cabeceras: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200


def solicitar(url: str, *, etag: str | None = None, timeout: int = TIMEOUT) -> Respuesta:
    """GET con cabeceras propias. No levanta: la falla viaja en la respuesta.

    `etag` no vacio agrega `If-None-Match`, que es lo que hace que un 304 de
    GitHub no consuma cuota (RQ-11).
    """
    import requests  # perezoso a proposito: ver docstring del modulo

    cabeceras = dict(CABECERAS)
    if etag:
        cabeceras["If-None-Match"] = etag
    try:
        r = requests.get(url, headers=cabeceras, timeout=timeout)
    except requests.RequestException as e:  # timeout, DNS, TLS, conexion
        return Respuesta(status=0, texto=None, cabeceras={}, error=str(e))
    return Respuesta(
        status=int(r.status_code),
        texto=r.text,
        cabeceras={str(k).lower(): str(v) for k, v in r.headers.items()},
        error=None,
    )


def entero_de_cabecera(resp: Respuesta, clave: str, defecto: int) -> int:
    """Lee una cabecera numerica. Reddit manda `0.0` y `55` como texto.

    Cualquier cosa que no se pueda leer como numero devuelve `defecto`: una
    cabecera rara no es motivo para tumbar una corrida nocturna.
    """
    crudo = resp.cabeceras.get(clave.lower())
    if crudo is None:
        return defecto
    try:
        return int(float(str(crudo).strip()))
    except (TypeError, ValueError):
        return defecto
