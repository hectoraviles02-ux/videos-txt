# -*- coding: utf-8 -*-
"""Gate de fidelidad con evidencia alineada y tolerancia a errores de transcripción (23-oct-2026).

Caso real: video de PHP/Next.js/Node.js (runs/fidelidad_20261023T080305). El gate anterior frenaba las 10
frases fieles (0/10). Aquí: las 10 pasan y toda alteración (cifra, negación, sujeto, nombre inventado)
se frena. La capa determinista se prueba siempre; con el juez NLI solo si el modelo está en la caché local.
"""
import json
from pathlib import Path

import pytest

from synthesis import gate_fidelidad as G

FUENTE = G.texto_de_fuente(json.loads((Path(__file__).parent / "fixtures" / "fidelidad_php_20261023" /
                                       "fuente.json").read_text(encoding="utf-8")))

FIELES = [
    "En solo una semana aparecieron fallas críticas en PHP, Next.js y Node.js, tres de las tecnologías más usadas de internet.",
    "Si tienes una web o un servidor, esto te interesa.",
    "Significa que alguien, desde internet, podría hacer que tu servidor obedezca sus órdenes, a veces sin usuario ni contraseña.",
    "Con apoyo de inteligencia artificial, unos investigadores encontraron 2 fallas encadenadas que abusan de la forma en que PHP maneja la memoria.",
    "Actualiza hoy, reinicia PHP y revisa tus registros desde el 17 de octubre.",
    "Segundo, Next.js, uno de los frameworks web más populares.",
    "Tuvo dos fallas críticas.",
    "Tercero, Node.js, el motor que ejecuta JavaScript en muchísimos servidores.",
    "Esta vez corrigieron 10 fallas.",
    "Afecta a todas las versiones con soporte, y la 20 o anteriores ya no tendrán parche.",
]
ALTERADAS = [
    "Esta vez corrigieron 12 fallas.",
    "Actualiza hoy, reinicia PHP y revisa tus registros desde el 18 de octubre.",
    "Afecta a todas las versiones con soporte, y la 22 o anteriores ya no tendrán parche.",
    "Afecta a todas las versiones con soporte, y la 20 o anteriores sí tendrán parche.",
    "Afecta a todas las versiones con soporte, y la 20 o anteriores tendrán parche.",
    "No necesitas actualizar ni reiniciar PHP.",
    "Segundo, Node.js, uno de los frameworks web más populares.",
    "Tercero, PHP, el motor que ejecuta JavaScript en muchísimos servidores.",
    "Con apoyo de inteligencia artificial, unos investigadores encontraron 2 fallas encadenadas que abusan de la forma en que Node.js maneja la memoria.",
    "Un sistema trampa de Google registró el ataque 4 días antes del aviso oficial.",
    "Microsoft confirmó que los ataques vienen de Rusia.",
    "Más de 3 millones de servidores ya fueron hackeados.",
]


@pytest.fixture(scope="module")
def juez():
    try:
        from synthesis.juez_nli import juez_nli
        return juez_nli()
    except Exception as e:  # noqa: BLE001
        pytest.skip("modelo NLI no disponible: %s" % e)


def test_la_evidencia_es_la_oracion_que_habla_de_lo_mismo():
    assert G.alinear("Esta vez corrigieron 10 fallas.", FUENTE, 1)[0] == "Corrigieron 10 fallas."


def test_errores_de_oido_cuentan_como_la_misma_palabra_pero_no_otro_nombre():
    toks = G._tokens("Tercero, Note.hot.se, el motor que ejecuta ya va a script")
    assert G._entidad_en("node", toks) and G._entidad_en("javascript", toks)
    assert not G._entidad_en("php", toks) and not G._entidad_en("next", G._tokens("node.hot.se"))
    assert G._parecida("obedezca", "ovedezca") and not G._parecida("diez", "doce")


def test_la_premisa_reparada_no_toca_cifras_ni_negaciones():
    r = G.reparar_premisa("y la 20 o anteriores ya no tendrán parche con suporte", "la 22 sí tendrán parche con soporte")
    assert "20" in r and "no tendrán" in r and "soporte" in r


@pytest.mark.parametrize("texto", ALTERADAS)
def test_determinista_frena_las_alteraciones(texto):
    assert G.verificar_afirmacion(texto, FUENTE).clasificacion in G.BLOQUEAN


def test_con_juez_pasan_las_10_fieles(juez):
    malas = [(t, a.clasificacion, a.motivos) for t in FIELES
             for a in [G.verificar_afirmacion(t, FUENTE, juez(FUENTE, [t])[0])] if a.clasificacion in G.BLOQUEAN]
    assert malas == []


def test_con_juez_se_frenan_todas_las_alteraciones(juez):
    fugas = [t for t in ALTERADAS
             if G.verificar_afirmacion(t, FUENTE, juez(FUENTE, [t])[0]).clasificacion not in G.BLOQUEAN]
    assert fugas == []


def test_el_fragmento_reportado_es_literal(juez):
    for t in FIELES:
        a = G.verificar_afirmacion(t, FUENTE, juez(FUENTE, [t])[0])
        assert G._es_literal(a.fragmento, FUENTE), t


# ── negación trasladada entre dos acciones (revisión de Codex, 23-oct) ────────
# Fuente: «Si no puedes desactiva el progreso de su vida de sesiones.» / «la 20 o anteriores ya no tendrán parche»
NEGACION_FIEL = ["Si no puedes actualizar hoy, desactiva el progreso de sesiones."]
NEGACION_TRASLADADA = [
    "Si puedes actualizar hoy, no desactives el progreso de sesiones.",
    "No afecta a todas las versiones con soporte, y la 20 o anteriores tendrán parche.",
]


@pytest.mark.parametrize("texto", NEGACION_TRASLADADA)
def test_determinista_frena_la_negacion_trasladada(texto):
    a = G.verificar_afirmacion(texto, FUENTE)
    assert a.clasificacion in G.BLOQUEAN and any("negación" in m for m in a.motivos), a.motivos


def test_la_negacion_en_su_lugar_no_dispara_la_alarma():
    for t in NEGACION_FIEL + FIELES:
        assert not any("negación" in m for m in G.verificar_afirmacion(t, FUENTE).motivos), t


def test_con_juez_la_negacion_trasladada_se_frena_y_la_fiel_pasa(juez):
    for t in NEGACION_TRASLADADA:
        assert G.verificar_afirmacion(t, FUENTE, juez(FUENTE, [t])[0]).clasificacion in G.BLOQUEAN, t
    for t in NEGACION_FIEL:
        assert G.verificar_afirmacion(t, FUENTE, juez(FUENTE, [t])[0]).clasificacion not in G.BLOQUEAN, t


# ── intento real de las 11:32 (runs/fidelidad_20261023T113232) ───────────────
FIELES_1132 = [
    "Ejemplos de riesgo: que te roben la base de datos, que borren tu web o que la usen para minar criptomonedas.",
    "La más seria estaba en el descompresor Brotli: un archivo comprimido manipulado podía escribir fuera de la memoria y abrir la puerta a ejecutar código.",
    "PHP, Next.js y Node.js: tres tecnologías distintas, una misma falla de fondo, la ejecución remota de código.",
]
AGREGADO_1132 = ("Actualiza a la última versión de tu rama y, si usas Docker, reconstruye tus imágenes antes de "
                 "volver a ponerlas en marcha.")


def test_nombres_escritos_de_oido_si_pero_no_otros_nombres():
    assert G._nombre_parecido("brotli", "brotley")
    assert not G._nombre_parecido("austria", "australia") and not G._nombre_parecido("next", "node")


def test_negacion_de_otra_cosa_no_dispara_la_alarma():
    a = G.verificar_afirmacion(FIELES_1132[0], FUENTE)
    assert not any("negación" in m for m in a.motivos), a.motivos


def test_con_juez_pasan_las_fieles_de_las_1132_y_se_frena_lo_agregado(juez):
    for t in FIELES_1132:
        a = G.verificar_afirmacion(t, FUENTE, juez(FUENTE, [t])[0])
        assert a.clasificacion not in G.BLOQUEAN, (t, a.motivos)
    assert G.verificar_afirmacion(AGREGADO_1132, FUENTE, juez(FUENTE, [AGREGADO_1132])[0]).clasificacion in G.BLOQUEAN


def test_nombre_trasladado_a_la_frase_vecina_sigue_frenado(juez):
    for t in ("Brotley, el motor que ejecuta JavaScript en muchísimos servidores.",
              "Tercero, Brotli, el motor que ejecuta JavaScript en muchísimos servidores."):
        assert G.verificar_afirmacion(t, FUENTE, juez(FUENTE, [t])[0]).clasificacion in G.BLOQUEAN, t
