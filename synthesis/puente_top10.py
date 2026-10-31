# -*- coding: utf-8 -*-
"""Puente: un elemento del `top10_json` del motor -> la entrada de `generar_creativo`.

Es el nodo que faltaba en la validacion vertical (ver
`docs/operations/VALIDACION_VERTICAL_20260922.md`): el motor produce un TOP y
el generador de guiones espera un tema, y entre los dos no habia nada.

**Transporta, no inventa.** Todo lo que sale de aqui ya venia en el TOP:

- el tema es el `titulo` del elemento;
- el angulo es `angulo_propuesto`, tal cual. Si falta, no se genera otro;
- la procedencia es su `evidencia` (fuente, url, fecha), mas la identidad de
  la corrida (`fecha_corte`, `hash_entradas`, `hash_titulo`, `posicion`) para
  poder volver a la noche exacta que lo decidio.

Lo que el TOP no trae, el puente no lo rellena:

- **`formato` no se convierte a minutos.** Ninguna regla del proyecto dice
  cuanto dura un "corto" (`config/marca.yaml` solo declara la cadencia), asi
  que `minutos` va en 0.0 y la duracion la pone `generar_creativo` con su
  propio valor por omision, igual que cuando el bot recibe un tema sin
  duracion. El formato viaja en la salida para quien si sepa interpretarlo.
- **Un elemento sin evidencia se rechaza.** Un tema sin procedencia es justo
  lo que la regla 2 del proyecto prohibe hacer pasar por dato.

No hay fallback: si el TOP no sirve, se levanta `PuenteInvalido` y la cadena
se detiene ahi. Ningun tema de respaldo, ningun fixture.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


class PuenteInvalido(ValueError):
    """El `top10_json` no permite construir una entrada con procedencia."""


@dataclass(frozen=True)
class Evidencia:
    fuente: str
    url: str
    publicada_en: str | None


@dataclass(frozen=True)
class EntradaGuion:
    """Lo que el puente entrega. Cada campo sale literal del TOP."""

    titulo: str
    angulo: str
    formato: str | None
    posicion: int
    hash_titulo: str
    fecha_corte: str
    hash_entradas: str
    evidencia: tuple[Evidencia, ...]

    @property
    def tema(self) -> str:
        """El `tema` para `generar_creativo`: titulo y angulo, en dos lineas."""
        return "%s\n%s" % (self.titulo, self.angulo)

    def contexto_procedencia(self) -> str:
        """De donde salio el tema. Va en el `contexto` del guion."""
        lineas = ["Procedencia del tema (motor, corrida %s, hash_entradas %s, posicion %d):"
                  % (self.fecha_corte, self.hash_entradas, self.posicion)]
        for e in self.evidencia:
            lineas.append("- %s, publicada %s: %s"
                          % (e.fuente, e.publicada_en or "sin fecha", e.url))
        return "\n".join(lineas)

    def argumentos_generar_creativo(self, contexto_adicional: str = "") -> dict:
        """Argumentos con nombre para `ScriptGenerator.generar_creativo`.

        `contexto_adicional` es lo que aporte una etapa posterior (por ejemplo
        los hechos de la investigacion); se antepone a la procedencia, que
        siempre va.
        """
        contexto = self.contexto_procedencia()
        if contexto_adicional:
            contexto = "%s\n\n%s" % (contexto_adicional, contexto)
        return {
            "tema": self.tema,
            "n_escenas": 0,
            "segundos_por_escena": 7.0,
            "contexto": contexto,
            "minutos": 0.0,
        }


def _texto(valor, campo: str, donde: str) -> str:
    if not isinstance(valor, str) or not valor.strip():
        raise PuenteInvalido("%s: falta `%s` o esta vacio" % (donde, campo))
    return valor


def entrada_desde_top10(top10: dict | str, posicion: int) -> EntradaGuion:
    """El elemento `posicion` del TOP, convertido en entrada de guion.

    Se busca por el campo `posicion` del elemento, no por su lugar en la
    lista: el orden de la lista no es parte del contrato del `top10_json`.
    """
    if isinstance(top10, str):
        try:
            top10 = json.loads(top10)
        except ValueError as exc:
            raise PuenteInvalido("el top10_json no es JSON valido: %s" % exc)
    if not isinstance(top10, dict):
        raise PuenteInvalido("el top10_json debe ser un objeto")

    fecha_corte = _texto(top10.get("fecha_corte"), "fecha_corte", "top10_json")
    hash_entradas = _texto(top10.get("hash_entradas"), "hash_entradas", "top10_json")
    temas = top10.get("temas")
    if not isinstance(temas, list) or not temas:
        raise PuenteInvalido("el top10_json de %s no trae temas" % fecha_corte)

    elegidos = [t for t in temas if isinstance(t, dict) and t.get("posicion") == posicion]
    if not elegidos:
        raise PuenteInvalido("el TOP de %s no tiene posicion %r" % (fecha_corte, posicion))
    if len(elegidos) > 1:
        raise PuenteInvalido("el TOP de %s repite la posicion %r" % (fecha_corte, posicion))
    t = elegidos[0]
    donde = "posicion %d de %s" % (posicion, fecha_corte)

    crudas = t.get("evidencia")
    if not isinstance(crudas, list) or not crudas:
        raise PuenteInvalido("%s: el tema no trae evidencia; sin procedencia no pasa" % donde)
    evidencia = []
    for e in crudas:
        if not isinstance(e, dict):
            raise PuenteInvalido("%s: evidencia con forma invalida" % donde)
        evidencia.append(Evidencia(
            fuente=_texto(e.get("fuente"), "evidencia.fuente", donde),
            url=_texto(e.get("url"), "evidencia.url", donde),
            publicada_en=e.get("publicada_en"),
        ))

    return EntradaGuion(
        titulo=_texto(t.get("titulo"), "titulo", donde),
        angulo=_texto(t.get("angulo_propuesto"), "angulo_propuesto", donde),
        formato=t.get("formato"),
        posicion=posicion,
        hash_titulo=_texto(t.get("hash_titulo"), "hash_titulo", donde),
        fecha_corte=fecha_corte,
        hash_entradas=hash_entradas,
        evidencia=tuple(evidencia),
    )
