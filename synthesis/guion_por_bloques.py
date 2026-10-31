# -*- coding: utf-8 -*-
"""Guion largo escrito por partes y unido despues.

POR QUE EXISTE ESTE MODULO
--------------------------
Un modelo local no escribe largo por mas que se le pida. No es un problema
de prompt ni de ventana de contexto: es una propiedad medida del modelo.

  LongWriter (ICLR 2025): "la longitud efectiva de generacion esta
  inherentemente limitada por las muestras que el modelo vio durante el
  fine-tuning supervisado". El techo practico ronda las 2000 palabras y no
  lo mueve ninguna instruccion.

  LIFEBench (NeurIPS 2025, 10800 casos): los modelos "generan salidas
  demasiado cortas, terminan prematuramente o rechazan el pedido", y
  "siguen bien las instrucciones de longitud corta y se deterioran
  bruscamente pasado cierto umbral". Los de contexto largo NO mejoran.

  Y la causa de raiz: un transformer no puede llevar un contador interno de
  palabras. Pedirle 67 y reclamarle cuando entrega 17 es pedirle que use un
  instrumento que no tiene.

MEDIDO EN ESTE PROYECTO, que es lo que manda:

  monolitico, 12 escenas de golpe, piso 50 pal/escena
    llamada 1 (original)     332 palabras   (27 por escena)
    llamada 2 (correccion)   424 palabras   (35 por escena)
    pedidas                  804

  El bucle de correccion mejoro un 28% y siguio a la mitad. El techo no
  estaba en el prompt.

  Antes, en el podcast: se pidieron 26 turnos y llegaron 11. En bloques de
  9 llegaron los 27. Mismo patron, otro contexto.

LA SOLUCION es la de LongWriter -AgentWrite-: descomponer. Primero el arco
completo en una llamada barata (una linea por escena), despues las escenas
en tandas chicas que caben dentro de lo que el modelo SI hace.

El tamaño de bloque no es una preferencia: sale de la medicion. Una sola
llamada entrego 424 palabras comodamente, asi que se pide por debajo de eso
-300- y queda margen. No se baja a una escena por llamada: son doce viajes
de ida y vuelta y cada bloque que abre en frio pierde continuidad.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Techo por llamada, MEDIDO: una sola llamada entrego 424 palabras sin
# esfuerzo. Se pide por debajo para no rozar el limite.
PALABRAS_POR_LLAMADA = 300

# El modelo entrega MENOS de lo que se le pide, y hay que compensarlo en el
# pedido. MEDIDO el 2026-08-22 (4 escenas por punto, mismo tema):
#
#   se pide  67  ->  entrega 25.5   (ratio 0.38)
#   se pide 120  ->  entrega 70.8   (ratio 0.59)
#   se pide 200  ->  entrega 67.5   (ratio 0.34)
#
# Dos cosas que esa tabla dice y que no se adivinan:
#
# 1. NO hay techo duro en 30 palabras, como parecia con los datos previos.
#    Pidiendo 120 entrega 70. El techo real esta cerca de 70 por escena.
# 2. Pedir MAS no da mas. A 200 entrega menos que a 120: el pedido excesivo
#    empeora el resultado en vez de mejorarlo. Por eso hay tope.
#
# 1.75 es 120/70 redondeado: lo que hay que pedir para recibir el objetivo.
FACTOR_DE_PEDIDO = 1.75

# Pedir arriba de esto degrada -medido en 200-. No es prudencia, es la
# medicion.
TOPE_DE_PEDIDO = 130

# Lo que el modelo entrega por escena en su mejor punto. Arriba de esto no
# se pide mas texto por escena: se piden mas escenas.
ENTREGA_MAXIMA_POR_ESCENA = 70

# Debajo de 2 escenas por bloque son doce viajes y la continuidad se rompe;
# arriba de 5 el bloque ya compite con el techo del modelo.
MIN_ESCENAS_BLOQUE = 2
MAX_ESCENAS_BLOQUE = 5

# Cuanta narracion previa se le pasa al bloque siguiente. Sin esto cada
# bloque abre en frio y repite los mismos hechos.
PALABRAS_DE_CONTEXTO = 120


def escenas_por_bloque(palabras_escena: int) -> int:
    """Cuantas escenas caben en una llamada sin rozar el techo del modelo.

    Se calcula sobre lo que el modelo ENTREGA, no sobre lo que se le pide:
    el bloque se llena con el texto que sale, no con el que se pidio.
    """
    if palabras_escena <= 0:
        return MAX_ESCENAS_BLOQUE
    cabe = PALABRAS_POR_LLAMADA // palabras_escena
    return max(MIN_ESCENAS_BLOQUE, min(MAX_ESCENAS_BLOQUE, cabe))


def pedido_para(objetivo: int) -> int:
    """Cuanto hay que PEDIR para recibir ``objetivo`` palabras por escena.

    Pedir lo que se quiere no funciona: el modelo entrega el 38% cuando se
    le pide 67. Hay que pedir de mas, pero no de mas de la cuenta -a 200
    entrega menos que a 120-, asi que el pedido va topado.
    """
    if objetivo <= 0:
        return 0
    return min(TOPE_DE_PEDIDO, max(objetivo, round(objetivo * FACTOR_DE_PEDIDO)))


def _texto_de(escena: dict) -> str:
    t = escena.get("narration") or escena.get("narracion") or ""
    if isinstance(t, dict):
        t = t.get("text") or t.get("texto") or ""
    return str(t)


def _cola(escenas: list[dict], palabras: int = PALABRAS_DE_CONTEXTO) -> str:
    """Las ultimas palabras ya escritas, para que el bloque siguiente enlace."""
    texto = " ".join(_texto_de(e) for e in escenas).split()
    return " ".join(texto[-palabras:]) if texto else ""


def _prompt_arco(tema: str, n: int, contexto: str) -> str:
    """Una linea por escena. Barato, y es lo que mantiene la historia unida.

    Se pide el arco ANTES de escribir porque cada bloque se genera por
    separado: sin un plan comun, el bloque 3 no sabe que el 1 ya conto el
    robo y lo vuelve a contar.
    """
    return (
        "Eres guionista de documental. Escribes en español de México.\n\n"
        f"TEMA: {tema}\n"
        + (f"\nCONTEXTO:\n{contexto}\n" if contexto else "")
        + f"\nNO escribas el guion todavia. Planea el ARCO de {n} escenas.\n"
        "Una linea por escena: que PASA en ella y que deja sin responder.\n"
        "La historia avanza: cada escena revela algo que la anterior no "
        "sabia. NUNCA repitas el mismo hecho en dos escenas.\n\n"
        "Estructura: gancho (una escena), desarrollo con obstaculo, giro, "
        "resolucion.\n\n"
        "Devuelve SOLO JSON valido:\n"
        "{\n"
        '  "title": "titulo con gancho, maximo 60 caracteres",\n'
        '  "arco": ["escena 1: ...", "escena 2: ..."]\n'
        "}\n"
    )


def _prompt_bloque(tema: str, arco: list[str], desde: int, hasta: int,
                   palabras_escena: int, previo: str) -> str:
    """Escribe SOLO las escenas del bloque, con lo ya escrito como enlace."""
    lineas = "\n".join(
        "  escena %d: %s" % (i + 1, arco[i]) for i in range(desde, hasta))
    n = hasta - desde
    return (
        "Eres guionista de documental. Escribes en español de México.\n\n"
        f"TEMA: {tema}\n\n"
        "ARCO COMPLETO (para que sepas donde estas parado):\n"
        + "\n".join("  %d. %s" % (i + 1, a) for i, a in enumerate(arco))
        + "\n\n"
        + (f"YA SE ESCRIBIO, y tu texto debe continuar de aqui:\n"
           f"  \"...{previo}\"\n"
           "NO repitas ningun hecho que ya aparezca arriba.\n\n"
           if previo else "")
        + f"ESCRIBE UNICAMENTE LAS ESCENAS {desde + 1} A {hasta}:\n"
        + lineas + "\n\n"
        + f"Cada una con ~{palabras_escena} palabras de narracion hablada. "
        "Son frases completas encadenadas, no titulares. Describe lo que "
        "pasa: quien hace que, donde, y que se ve.\n"
        "Usa conectores -pero, sin embargo, entonces, hasta que, lo que no "
        "sabian- : son los que convierten hechos sueltos en una historia.\n"
        "Rompe el ritmo: mezcla frases largas con frases de menos de 6 "
        "palabras.\n"
        "NUNCA 'En el mundo de hoy' ni 'La tecnologia avanza'. No anuncies "
        "la estructura ('el giro', 'en conclusion').\n\n"
        f"Devuelve SOLO JSON valido con EXACTAMENTE {n} escenas:\n"
        "{\n"
        '  "scenes": [\n'
        "    {\n"
        '      "narration": "lo que se escucha",\n'
        '      "visual_description": "que se ve, en una frase concreta",\n'
        '      "search_keywords": "4 palabras en ingles, objetos o lugares '
        'fotografiables. Ej: laptop desk office documents"\n'
        "    }\n"
        "  ]\n"
        "}\n"
    )


class ArcoVacioError(RuntimeError):
    """El plan no trajo arco. Sin plan los bloques se repiten entre si."""


def generar(llamar, tema: str, n: int, palabras_escena: int,
            contexto: str = "", opciones: dict | None = None,
            llamar_plan=None) -> dict:
    """Guion completo escrito por bloques.

    ``llamar`` es ``ScriptGenerator._call_ollama``: se recibe en vez de
    importarse para poder probar este modulo sin levantar Ollama.

    ``llamar_plan`` es el canal para el ARCO, que NO devuelve escenas. Hace
    falta separado porque ``_call_ollama`` exige la clave ``scenes`` en toda
    respuesta: MEDIDO el 2026-08-22, el arco entraba por ahi, se reintentaba
    con un prompt "mas estricto" que lo convertia en escenas, y este modulo
    recibia un plan vacio. Los tres bloques corrieron con doce lineas de
    "continua la historia" y el guion salio repetido y sin cifras. El codigo
    estaba escrito y nunca se conecto: MF-005.

    Si falla UN bloque se sigue con los demas -un guion al que le faltan
    tres escenas todavia sirve-. Si falla el ARCO se detiene: sin plan el
    resultado es PEOR que el monolitico, no solo mas corto.
    """
    plan = (llamar_plan or llamar)(_prompt_arco(tema, n, contexto),
                                   options=opciones)
    arco = [str(x) for x in (plan.get("arco") or []) if str(x).strip()]

    # Un arco vacio no se rellena: se detiene. Rellenar con "continua la
    # historia" es lo que produjo el guion repetido -y peor, lo produjo en
    # silencio, con todas las escenas presentes y el JSON valido-.
    if not arco:
        raise ArcoVacioError(
            "El plan no devolvio arco para '%s'. Sin plan cada bloque "
            "reinventa la historia y repite hechos." % tema)

    # Un arco corto SI se completa: faltan lineas, pero las que hay guian.
    if len(arco) < n:
        logger.info("Arco corto (%d de %d), se completa", len(arco), n)
        arco += ["continua la historia" for _ in range(n - len(arco))]
    arco = arco[:n]

    # `palabras_escena` es el OBJETIVO -lo que se quiere recibir-. Lo que se
    # escribe en el prompt es el PEDIDO, que va inflado porque el modelo
    # entrega de menos. El tamaño de bloque se calcula sobre el objetivo:
    # el bloque se llena con el texto que sale, no con el que se pidio.
    por_bloque = escenas_por_bloque(palabras_escena)
    pedido = pedido_para(palabras_escena)
    if pedido != palabras_escena:
        logger.info("Objetivo %d palabras por escena, se piden %d",
                    palabras_escena, pedido)

    escenas: list[dict] = []
    desde = 0
    while desde < n:
        hasta = min(desde + por_bloque, n)
        prompt = _prompt_bloque(tema, arco, desde, hasta,
                                pedido, _cola(escenas))
        try:
            parte = llamar(prompt, options=opciones)
        except Exception as e:            # noqa: BLE001 - se anota y sigue
            logger.warning("Bloque %d-%d fallo: %s", desde + 1, hasta, e)
            desde = hasta
            continue

        nuevas = parte.get("scenes") or []
        if not nuevas:
            logger.warning("Bloque %d-%d vino vacio", desde + 1, hasta)
        escenas.extend(nuevas[:hasta - desde])
        logger.info("Bloque %d-%d: %d escenas, %d palabras",
                    desde + 1, hasta, len(nuevas[:hasta - desde]),
                    sum(len(_texto_de(e).split())
                        for e in nuevas[:hasta - desde]))
        desde = hasta

    return {
        "title": plan.get("title") or tema,
        "scenes": escenas,
        "arco": arco,
        # Queda registrado como se produjo: un guion por bloques y uno
        # monolitico se comportan distinto y no deben confundirse al medir.
        "metodo_generacion": "bloques",
        "escenas_por_bloque": por_bloque,
    }


def hechos_repetidos(guion: dict, minimo: int = 4) -> list[str]:
    """Frases repetidas entre escenas: el modo de falla propio de bloques.

    El monolitico se queda corto; este repite. Cada bloque abre sin ver el
    texto completo, asi que puede volver a contar lo que otro ya conto. Es
    el defecto que hay que vigilar DESPUES de descomponer, porque la cuenta
    de palabras deja de discriminar -se cumple por construccion-.
    """
    def grupos(escena: dict):
        palabras = [p.strip(".,;:¿?¡!\"'").lower()
                    for p in _texto_de(escena).split()]
        for j in range(len(palabras) - minimo + 1):
            grupo = tuple(palabras[j:j + minimo])
            # "de la de la" se repite en cualquier texto y no es un hecho.
            if len(set(grupo)) == minimo:
                yield grupo

    escenas = guion.get("scenes") or []
    primera_vez: dict[tuple, int] = {}
    avisos: list[str] = []
    ya_avisado: set[tuple[int, int]] = set()

    for i, escena in enumerate(escenas, start=1):
        for grupo in grupos(escena):
            origen = primera_vez.setdefault(grupo, i)
            if origen == i or (origen, i) in ya_avisado:
                continue
            ya_avisado.add((origen, i))
            avisos.append(
                "Las escenas %d y %d repiten \"%s\": reescribe la %d con "
                "otro hecho del arco." % (origen, i, " ".join(grupo), i))
    return avisos
