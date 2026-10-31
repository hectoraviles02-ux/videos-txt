# -*- coding: utf-8 -*-
"""Frente de senales · fecha canonica. Contrato V2-B, bloque 1.

La fecha de una senal tiene DOS responsabilidades y este frente las separa:

- la fecha CRUDA forma parte de la identidad historica de la senal cuando no
  hay URL, y no se toca;
- la fecha CANONICA es el instante que se usa para decidir: ventanas,
  filtrado, que entra en el dia.

Esta funcion produce la segunda y NO inventa nada: si el instante no se puede
derivar sin ambiguedad, devuelve `None` y la decision cae a `detectada_en`.
"""

from __future__ import annotations

import pytest

from content_factory.ingestion.base import fecha_canonica_utc


@pytest.mark.parametrize("cruda, esperada", [
    # ISO con Z: ya es UTC. Las fracciones de segundo se descartan.
    ("2026-09-14T18:12:00Z", "2026-09-14T18:12:00Z"),
    ("2026-09-13T19:44:12.000Z", "2026-09-13T19:44:12Z"),
    # ISO con offset cero explicito.
    ("2026-08-30T09:15:00+00:00", "2026-08-30T09:15:00Z"),
    # Offset POSITIVO: el instante UTC es ANTERIOR a la hora escrita. Aqui
    # cambia de dia, que es justo el caso que una comparacion textual falla.
    ("2026-09-14T02:00:00+05:00", "2026-09-13T21:00:00Z"),
    # Offset NEGATIVO: el instante UTC es POSTERIOR. Tambien cambia de dia.
    ("2026-09-14T22:30:00-06:00", "2026-09-15T04:30:00Z"),
    # RFC-822 con offset inequivoco: el formato de RSS y de Trends.
    ("Sun, 14 Sep 2026 08:00:00 +0000", "2026-09-14T08:00:00Z"),
    ("Tue, 16 Sep 2026 02:20:00 -0700", "2026-09-16T09:20:00Z"),
])
def test_un_instante_inequivoco_se_lleva_a_utc(cruda, esperada):
    assert fecha_canonica_utc(cruda) == esperada


@pytest.mark.parametrize("cruda, motivo", [
    # RFC 2822 reserva `-0000` para decir "zona DESCONOCIDA". No es UTC: es
    # la ausencia declarada de zona. Tratarlo como +0000 seria inventarla.
    ("Sun, 14 Sep 2026 08:00:00 -0000", "RFC-822 con zona declarada desconocida"),
    # ISO sin zona: no se sabe en que huso se escribio.
    ("2026-09-14T08:00:00", "ISO con hora y sin zona"),
    # Fecha sola sin hora: no es un instante.
    ("2026-09-14", "fecha sin hora"),
    ("no es una fecha", "basura"),
    ("", "cadena vacia"),
    (None, "ausente"),
])
def test_lo_que_no_es_inequivoco_no_se_inventa(cruda, motivo):
    """Todos devuelven `None`, y ese `None` es la declaracion de no saber.

    Si esto devolviera un valor en cualquiera de estos casos, la decision de un
    dia dependeria de una zona horaria que nadie informo.
    """
    assert fecha_canonica_utc(cruda) is None, motivo


def test_la_salida_compara_bien_como_texto():
    """El formato canonico es el que hace valida la comparacion textual.

    Es la propiedad que la fecha cruda NO tenia: `"Mon, 15 Se" <= "2026-09-14"`
    daba falso por la letra, no por la fecha. Con la canonica, el orden de
    texto coincide con el orden temporal, y los diez primeros caracteres son el
    dia UTC.
    """
    a = fecha_canonica_utc("Sun, 14 Sep 2026 08:00:00 +0000")
    b = fecha_canonica_utc("Mon, 15 Sep 2026 12:30:00 +0000")
    assert a < b
    assert a[:10] == "2026-09-14"
    assert a[:10] <= "2026-09-14"
    assert not b[:10] <= "2026-09-14"
