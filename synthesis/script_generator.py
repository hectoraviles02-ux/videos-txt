from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import requests

from synthesis import guion_por_bloques

logger = logging.getLogger(__name__)


class OllamaConnectionError(Exception):
    """Raised when connection to Ollama fails (refused or timeout)."""


class ScriptGenerationError(Exception):
    """Raised when Ollama returns invalid or unparseable JSON."""


class FuenteInsuficienteError(Exception):
    """La fuente no da para un video y no se va a rellenar el hueco.

    Se lanza ANTES de llamar al modelo. El 2026-08-20 un reel de 15s en un
    idioma que Whisper no reconocio dejo 53 palabras con confianza 0.319; el
    generador las expandio a una narracion completa sobre "una familia en un
    parque" que no tenia relacion con la fuente, y se publico como si fuera
    su contenido. Eso es lo que la Regla 2 del CLAUDE.md prohibe.

    Cuando el usuario QUIERE un guion inventado, existe ``generar_creativo``:
    crea sin limite y marca el resultado como creacion propia. Lo que no
    puede pasar es inventar en silencio dentro de la ruta de extraccion.
    """

    def __init__(self, motivo: str, palabras: int = 0, confianza: float | None = None):
        self.motivo = motivo
        self.palabras = palabras
        self.confianza = confianza
        super().__init__(motivo)


# Debajo de esto no hay video que extraer. Son minimos deliberadamente bajos:
# la compuerta esta para atajar basura evidente, no para exigir calidad.
MIN_PALABRAS_FUENTE = 40
MIN_CONFIANZA_MEDIA = 0.45

# Ritmo de narracion, MEDIDO CONTRA LA VOZ QUE ESTE PROYECTO USA.
#
# Aqui decia 100, tomado de docs/PLANTILLA_GUION_DOCUMENTAL.md. Ese numero
# describe a un narrador humano leyendo en registro documental, y por eso
# parecia razonable. Pero la conversion de minutos a palabras tiene que
# medirse contra quien de verdad habla, y aqui habla VoxCPM2.
#
# MEDIDO el 2026-08-23: las 593 palabras de storyboard_prueba_larga.json
# sintetizadas con VozClonadaAdapter dan 184.5 segundos de audio.
#
#     593 palabras / 184.5 s = 192.8 palabras por minuto
#
# Con 100 aqui, pedir ocho minutos derivaba palabras para poco mas de
# cuatro, y el video salia a la mitad sin que nada avisara. El sintoma se
# confundio dos veces con un tope de escenas, que era otro problema.
#
# Si se cambia la voz, esta constante se vuelve a medir: no es una
# preferencia de estilo, es una propiedad del sintetizador.
PALABRAS_POR_MINUTO = 193

# Palabras que se busca por escena. Sale del techo del modelo, no del gusto:
# synthesis/guion_por_bloques.py mide que entrega ~70 por escena en su mejor
# punto, asi que por encima de esto no se pide mas texto por escena, se
# piden mas escenas.
PALABRAS_POR_ESCENA_OBJETIVO = 65

# Por debajo de esto el guion no cubre lo que se pidio y hay que reclamarlo.
# El modelo entrega corto sin avisar: medido, 192 palabras cuando el pedido
# eran 800 (ver R8 en docs/REGLAS_QUE_NO_SE_PISAN.md).
FRACCION_MINIMA_ENTREGA = 0.7


_STYLE_PROMPTS: dict[str, str] = {
    "youtube": (
        "Write in an engaging documentary style suitable for a YouTube audience. "
        "Use clear, accessible language with a conversational tone."
    ),
    "tutorial": (
        "Write as an educational tutorial. Break down concepts step by step, "
        "use explanatory language, and maintain a helpful tone."
    ),
    "narrative": (
        "Write as a cinematic narrative. Use vivid descriptions, build atmosphere, "
        "and maintain a storytelling rhythm with dramatic pacing."
    ),
}


# Lo que soltaba a la bestia no era subir la temperatura: era quitarle el
# corse. Ollama corre con num_ctx=2048 por defecto y recorta prompts largos
# sin avisar, asi que el modelo no alcanzaba a leer su propia instruccion.
#
# Medido el 2026-08-20 con qwen2.5:14b-q4: a 0.95 el modelo produce frases
# sin sentido ("la IA es miercoles") y se inventa cifras. A 0.8 conserva el
# gancho y deja de delirar. Mas temperatura no es mas creatividad: es mas
# ruido, y en un video publicado un dato falso cuesta mas que una frase sosa.
OPCIONES_CREATIVAS = {
    "temperature": 0.8,
    "top_p": 0.9,
    "top_k": 50,
    # Penaliza repetir las mismas muletillas escena tras escena.
    "repeat_penalty": 1.15,
    "num_ctx": 32768,
    # Un guion de seis minutos son ~650 palabras y sus descripciones visuales.
    # Con 2048 el modelo se quedaba a la mitad y devolvia menos escenas de las
    # pedidas: MEDIDO, 11 turnos cuando se pidieron 26.
    "num_predict": 8192,
}


_ETIQUETAS_ESTRUCTURA = (
    "gancho:", "el giro", "desarrollo:", "cierre:", "giro:", "conclusion:",
)
_RE_CIFRA = re.compile(
    r"\b\d{1,3}\s*(?:%|por ciento)|\b(?:nueve|ocho|siete|seis) de cada\b",
    re.IGNORECASE,
)


def revisar_cifras_contra_fuente(guion: dict, contexto: str) -> list[str]:
    """Compara las cifras del guion contra las del material de origen.

    MEDIDO el 2026-08-22: con un prompt que pedia abrir con una escena, el
    gancho mejoro y las cifras se corrompieron. La fuente decia "225 masas
    solares" y el guion escribio "225 millones de masas solares". Multiplicar
    por un millon no es una licencia narrativa: es un dato falso, y sale con
    la misma cara de verdadero que el resto.

    No se revisa el estilo aqui -eso es `revisar_guion`- sino la unica cosa
    que no se puede dejar pasar: un numero que la fuente no dice.
    """
    if not contexto:
        return []
    de_fuente = {c for c in _cantidades(contexto)}
    if not de_fuente:
        return []
    defectos = []
    for i, escena in enumerate(guion.get("scenes") or [], 1):
        texto = str(escena.get("narration") or "")
        for crudo, normal in _cantidades(texto, con_original=True):
            if normal in de_fuente or len(normal.split("|")[0]) <= 1:
                continue
            defectos.append(
                "la escena %d dice %r y esa cantidad no esta en las fuentes; "
                "usa la de la fuente o quita el dato" % (i, crudo))
    return defectos


# Las palabras que multiplican. Sin ellas la comprobacion es inutil: "225" y
# "225 millones" comparten los mismos digitos y son cosas distintas por un
# factor de un millon.
_MAGNITUDES = ("mil millones", "millones", "millon", "mil", "billones",
               "billon")


def _cantidades(texto: str, con_original: bool = False):
    """Numeros del texto CON su palabra de magnitud, normalizados.

    Devuelve "225|" para "225" y "225|millones" para "225 millones", de modo
    que comparar cadenas ya distinga la magnitud. Los separadores de miles se
    quitan porque el espanol y el ingles los usan al reves.
    """
    salida = []
    for m in re.finditer(r"(\d[\d.,]*)\s*((?:mil\s+millones|millones|millon|"
                         r"mil|billones|billon)?)", texto, re.IGNORECASE):
        crudo = m.group(0).strip()
        digitos = re.sub(r"[.,]", "", m.group(1))
        magnitud = re.sub(r"\s+", " ", (m.group(2) or "").lower().strip())
        normal = "%s|%s" % (digitos, magnitud)
        salida.append((crudo, normal) if con_original else normal)
    return salida


def _json_incrustado(raw: str) -> Any:
    """El objeto JSON que viene envuelto en texto o en cercas de markdown.

    El modelo obedece "devuelve SOLO JSON" casi siempre. Cuando no, envuelve
    la respuesta en ```json ... ``` o le antepone una linea de cortesia. Se
    recorta desde la primera llave hasta la ultima en vez de pedirlo otra
    vez: la respuesta ya esta ahi y una llamada mas cuesta medio minuto.
    """
    inicio = raw.find("{")
    fin = raw.rfind("}")
    if inicio < 0 or fin <= inicio:
        raise ScriptGenerationError(
            "La respuesta no contiene un objeto JSON: %r" % raw[:200])
    return json.loads(raw[inicio:fin + 1])


def palabras_de_narracion(guion: dict) -> int:
    """Palabras habladas del guion. Es lo que decide cuanto dura el video."""
    total = 0
    for escena in guion.get("scenes") or []:
        texto = escena.get("narration") or escena.get("narracion") or ""
        if isinstance(texto, dict):
            texto = texto.get("text") or texto.get("texto") or ""
        total += len(str(texto).split())
    return total


def _es_mejor(actual: dict, candidato: dict,
              cifras_actual: int, cifras_candidato: int,
              defectos_actual: int, defectos_candidato: int) -> bool:
    """¿La correccion mejora al original? En orden, no por conteo.

    Contar defectos era el instrumento equivocado: MEDIDO, la correccion
    subio la narracion de 332 a 424 palabras -28% mas video- y se descarto
    porque las doce escenas seguian bajo el piso y el conteo daba igual. El
    numero era correcto; la magnitud, no.

    El orden importa y no es negociable:

    1. VETO por cifras. Una correccion que introduce una cifra que no esta
       en la fuente pierde por mucho mas larga que sea. Un dato falso
       invalida el video entero; un defecto de estilo solo lo afea.
    2. Mas narracion. Es lo que decide la duracion, que es el problema.
    3. Menos defectos de estilo, para desempatar.
    """
    if cifras_candidato > cifras_actual:
        return False
    pal_actual = palabras_de_narracion(actual)
    pal_candidato = palabras_de_narracion(candidato)
    if pal_candidato != pal_actual:
        return pal_candidato > pal_actual
    return defectos_candidato < defectos_actual


def revisar_extension(guion: dict, piso_escena: int, n_pedidas: int) -> list[str]:
    """Escenas mas cortas de lo pedido, nombradas una por una.

    El modelo entrega por debajo de lo pedido y no lo dice: medido, 192
    palabras cuando el pedido eran 800. Contar lo devuelto es lo unico que
    lo detecta, porque el JSON es valido y las escenas estan todas.

    Se nombra la escena y su cuenta -no "escribe mas largo"- porque un
    defecto observable se corrige y una instruccion repetida no.
    """
    defectos: list[str] = []
    escenas = guion.get("scenes") or []

    if len(escenas) < n_pedidas:
        defectos.append(
            "Faltan escenas: se pidieron %d y llegaron %d. Escribe las %d "
            "que faltan, continuando la historia."
            % (n_pedidas, len(escenas), n_pedidas - len(escenas)))

    cortas = []
    for i, escena in enumerate(escenas, start=1):
        texto = escena.get("narration") or escena.get("narracion") or ""
        if isinstance(texto, dict):
            texto = texto.get("text") or texto.get("texto") or ""
        cuenta = len(str(texto).split())
        if cuenta < piso_escena:
            cortas.append((i, cuenta))

    for i, cuenta in cortas:
        defectos.append(
            "La escena %d tiene %d palabras de narracion y necesita al menos "
            "%d. No la repitas mas largo: desarrolla lo que ya dice -que se "
            "ve, quien lo hace, que pasa despues-." % (i, cuenta, piso_escena))
    return defectos


def revisar_guion(guion: dict) -> list[str]:
    """Defectos concretos de un guion creativo, en lenguaje accionable.

    Es la version en texto del Quality Gate: no dice "esta mal", dice que
    esta mal y en que escena, para que el modelo pueda corregir eso y solo
    eso. Devuelve lista vacia si el guion esta limpio.
    """
    defectos: list[str] = []
    titulo = str(guion.get("title", ""))

    if _RE_CIFRA.search(titulo):
        defectos.append(
            f"El titulo {titulo!r} usa una cifra que nadie verifico. "
            "Reescribelo sin numeros."
        )

    for i, escena in enumerate(guion.get("scenes", []), start=1):
        narracion = str(escena.get("narration", "")).strip()
        bajo = narracion.lower()

        if "..." in narracion or narracion.startswith(("…", ".")):
            defectos.append(
                f"Escena {i} parte la idea con puntos suspensivos "
                f"({narracion[:45]!r}). Reescribela como una frase completa "
                "que se entienda sola."
            )
        for etiqueta in _ETIQUETAS_ESTRUCTURA:
            if etiqueta in bajo:
                defectos.append(
                    f"Escena {i} nombra la estructura ({etiqueta!r}). "
                    "La estructura se aplica, no se anuncia: quita esa "
                    "palabra y di directamente la idea."
                )
                break
        if _RE_CIFRA.search(narracion):
            defectos.append(
                f"Escena {i} afirma una cifra que nadie verifico. "
                "Di la idea sin el numero."
            )
        if len(narracion.split()) < 6:
            defectos.append(
                f"Escena {i} tiene {len(narracion.split())} palabras: es muy "
                "corta para su duracion. Desarrolla la idea."
            )

    return defectos


# El documental dura LO QUE DURE LA FUENTE. El 2026-08-30 el operador lo
# dijo sin lugar a dudas: "necesito videos PRECISOS CON EMOCION
# COMPLETOS... si lleva analisis lo tomes completo, NO IMPORTA CUANTO
# DURE". Estuvo en 0.5 unas horas y fue un error de lectura mio.
# El antecedente que motivo todo esto: MEDIDO el 2026-08-29, con el
# prompt de "5 to 10 scenes" fijo, un video de 489 s daba uno de 51 s -el
# 10%-, que es lo que el usuario describio como "muy mediocre".
PROPORCION_DOCUMENTAL = 1.0
# Piso y techo. El techo es un tope de seguridad de UNA HORA, no un
# criterio de estilo: existe para que una fuente de seis horas no dispare
# una generacion que nadie va a esperar. El costo de computo NO se usa
# como argumento para acortar: eso ya se intento y el operador lo
# rechazo.
MIN_SEG_DOCUMENTAL = 60
MAX_SEG_DOCUMENTAL = 3600
# Palabras por escena. Menos de 60 y la escena no alcanza a decir nada; mas
# de 110 y el cuadro se queda quieto demasiado tiempo sobre la misma imagen.
PALABRAS_POR_ESCENA = 85


class ScriptGenerator:
    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        ollama_model: str = "qwen2.5:14b",
        # 900 y no 300. El 2026-08-30 un documental por URL murio con
        # `Ollama timeout after 300s` escribiendo el guion. La causa no
        # fue Ollama: ese mismo dia el documental paso a durar lo que
        # dura la fuente -hasta 18 escenas en vez de 5-, y nadie le dio
        # mas tiempo para escribirlo. De los cinco ScriptGenerator del
        # bot, solo uno pasaba timeout explicito; los otros cuatro caian
        # aqui. Se sube el DEFAULT para cubrirlos a todos.
        #
        # El 14b corre 60% en CPU con num_ctx 32768: un bloque de guion
        # con contexto grande tarda 80 s MEDIDOS, y los ultimos bloques
        # se alargan porque arrastran mas contexto.
        timeout: int = 900,
    ):
        self.ollama_url = ollama_url.rstrip("/")
        self.ollama_model = ollama_model
        self.timeout = timeout

    def generate(self, extracted_content: dict) -> dict:
        text = self._get_text(extracted_content)
        word_count = len(text.split())
        logger.info(
            "Generating script from %d words (model=%s)",
            word_count,
            self.ollama_model,
        )

        self._compuerta_fuente(text, word_count, extracted_content)

        if word_count < 100:
            logger.info("Content < 100 words, using single-scene expansion")
            return self._generate_single_scene(text, extracted_content)

        n_esc, por_esc, _obj = self._plan_por_fuente(
            extracted_content, word_count)

        # Arriba de lo que el modelo entrega de una sentada, se descompone.
        # MEDIDO el 2026-08-30 por esta misma ruta: se pidieron 11 escenas de
        # ~87 palabras y llegaron 364 en total, el 38%. El techo es del
        # modelo, no del prompt, y `guion_por_bloques` ya lo resuelve: estaba
        # conectado solo a la ruta de tema.
        if n_esc * por_esc > guion_por_bloques.PALABRAS_POR_LLAMADA:
            logger.info(
                "Guion de ~%d palabras desde URL: por encima de lo que el "
                "modelo entrega en una llamada, se genera por bloques",
                n_esc * por_esc)
            tema = (extracted_content.get("titulo")
                    or extracted_content.get("title") or "el contenido")
            # `llamar_plan` es obligatorio: sin el, el arco llega vacio y los
            # bloques salen repetidos y sin cifras (MF-005, 2026-08-22).
            # `por_esc` va SIN inflar: `guion_por_bloques.pedido_para` lo hace
            # adentro.
            return guion_por_bloques.generar(
                self._call_ollama, tema, n_esc, por_esc,
                contexto=text[:6000], opciones=OPCIONES_CREATIVAS,
                llamar_plan=self._call_ollama_sin_escenas)

        prompt = self._build_prompt(extracted_content)
        raw, _ = self._call_ollama_api(prompt)
        return self._parse_response(raw)

    def generate_script(
        self, transcript: str, style: str = "youtube"
    ) -> str:
        """Generate a raw JSON script string from plain text transcript.

        Args:
            transcript: Plain text content to base the script on.
            style: One of ``"youtube"``, ``"tutorial"``, ``"narrative"``.

        Returns:
            Raw JSON string with ``title`` and ``scenes`` array.

        Raises:
            OllamaConnectionError: If Ollama is unreachable.
            ScriptGenerationError: If response is invalid.
        """
        t0 = time.time()
        prompt = self._build_style_prompt(transcript, style)
        raw, metadata = self._call_ollama_api(prompt)
        elapsed = time.time() - t0

        eval_count = metadata.get("eval_count", 0)
        total_duration_ns = metadata.get("total_duration", 0)
        total_duration_s = total_duration_ns / 1e9 if total_duration_ns else 0

        logger.info(
            "Script generated in %.2fs | style=%s | tokens=%d | ollama_duration=%.2fs",
            elapsed,
            style,
            eval_count,
            total_duration_s,
        )

        result = self._parse_response(raw)
        return json.dumps(result, ensure_ascii=False, indent=2)

    def generate_storyboard(self, transcript: str) -> Any:
        """Generate a fully built Storyboard from plain text transcript.

        Internally calls ``generate_script`` then ``build_from_script``.
        """
        from synthesis.storyboard_builder import build_from_script as _build

        script_json_str = self.generate_script(transcript)
        script = json.loads(script_json_str)
        return _build(script)

    def _compuerta_fuente(
        self, text: str, word_count: int, extracted_content: dict
    ) -> None:
        """Se niega a generar cuando la fuente no da. No rellena el hueco."""
        if word_count < MIN_PALABRAS_FUENTE:
            raise FuenteInsuficienteError(
                f"la fuente solo dejo {word_count} palabras "
                f"(minimo {MIN_PALABRAS_FUENTE})",
                palabras=word_count,
            )

        segmentos = extracted_content.get("segmentos") or []
        confianzas = [
            s["confianza"] for s in segmentos
            if isinstance(s.get("confianza"), (int, float))
        ]
        if confianzas:
            media = sum(confianzas) / len(confianzas)
            if media < MIN_CONFIANZA_MEDIA:
                raise FuenteInsuficienteError(
                    f"la transcripcion tiene confianza media {media:.2f} "
                    f"(minimo {MIN_CONFIANZA_MEDIA}): el texto no es fiable",
                    palabras=word_count,
                    confianza=media,
                )

    def _get_text(self, extracted_content: dict) -> str:
        segments = extracted_content.get("segmentos", [])
        if segments:
            return " ".join(
                s.get("texto", "") for s in segments if s.get("texto")
            )
        return extracted_content.get("text", extracted_content.get("texto", ""))

    def _plan_por_fuente(self, extracted_content: dict,
                         word_count: int) -> tuple[int, int, int]:
        """Cuantas escenas y de que largo, segun lo que dure la fuente.

        El 2026-08-29 un video de 8 minutos (489 s) produjo un documental de
        51 segundos: 5 escenas, 193 palabras. No se habia cortado, nacio
        corto, porque el prompt pedia "5 to 10 scenes" fijo y nadie le decia
        cuanto duraba el material. Una fuente de dos horas y una de dos
        minutos daban exactamente lo mismo.

        Ahora el documental apunta a la MITAD de la fuente: ni un resumen de
        una linea ni la transcripcion disfrazada. Con los topes puestos, una
        fuente de 8 minutos da unos 4, y una de dos horas no dispara una
        generacion de media tarde.
        """
        try:
            fuente_seg = int(extracted_content.get("duracion_seg") or 0)
        except (TypeError, ValueError):
            fuente_seg = 0
        if fuente_seg <= 0:
            # Texto sin duracion: se estima a 2.5 palabras por segundo, que
            # es el mismo ritmo que usa storyboard_builder para lo inverso.
            fuente_seg = int(word_count / 2.5)

        objetivo = fuente_seg * PROPORCION_DOCUMENTAL
        objetivo = max(MIN_SEG_DOCUMENTAL, min(MAX_SEG_DOCUMENTAL, objetivo))
        palabras = int(objetivo * 2.5)
        escenas = max(4, min(18, round(palabras / PALABRAS_POR_ESCENA)))
        por_escena = max(40, int(palabras / escenas))
        logger.info("plan de guion: fuente %ds -> objetivo %ds, %d escenas "
                    "de ~%d palabras", fuente_seg, int(objetivo), escenas,
                    por_escena)
        return escenas, por_escena, int(objetivo)

    def _build_prompt(self, extracted_content: dict) -> str:
        text = self._get_text(extracted_content)
        n_esc, por_esc, _obj = self._plan_por_fuente(
            extracted_content, len(text.split()))
        min_esc = max(40, por_esc - 20)
        has_timestamps = any(
            s.get("inicio_seg") is not None
            for s in extracted_content.get("segmentos", [])
        )
        ts_note = (
            "\nThe content is a transcript with timestamps. "
            "Arrange scenes chronologically following the source order.\n"
            if has_timestamps
            else ""
        )

        return (
            "You are a documentary script writer. Generate a video script from the following content.\n"
            f"{ts_note}"
            "\nIMPORTANT: The content is in Spanish. Write the ENTIRE script in Spanish. "
            "All narration and descriptions MUST be in Spanish.\n"
            "\nOutput ONLY valid JSON with this exact structure (no markdown, no explanation):\n"
            "{\n"
            '  "title": "Video title",\n'
            '  "scenes": [\n'
            "    {\n"
            '      "narration": "Narration text (40-120 words per scene)",\n'
            '      "visual_description": "Detailed visual: subject, lighting, composition, color palette",\n'
            '      "search_keywords": "3-5 keywords in ENGLISH separated by commas describing the scene for stock photo search"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "\nRequirements:\n"
            f"- EXACTLY {n_esc} scenes. Not fewer: the source is long "
            f"enough for all of them.\n"
            f"- narration: about {por_esc} words per scene, minimum "
            f"{min_esc}. Neutral documentary tone.\n"
            "- visual_description: detailed enough for AI image generation\n"
            # La misma regla que la ruta de tema (linea ~798), MEDIDA el
            # 2026-08-21: la consulta abstracta trae catalogo abstracto, y
            # el generador de imagen dibuja garabatos cuando se le pide una
            # idea en vez de una cosa.
            "- search_keywords is NOT the topic of the scene: it is what a\n"
            "  CAMERA WOULD SEE. Exactly 4 or 5 English words.\n"
            "- PUT A PERSON IN MOST SCENES, doing something concrete, in a\n"
            "  place. A documentary without people is dead footage.\n"
            '  Good: "woman pouring milk bowl kitchen", "man reading label\n'
            '  supermarket aisle", "worker factory conveyor morning light",\n'
            '  "child eating breakfast table window".\n'
            "- FORBIDDEN, because the image model renders them as illegible\n"
            "  scribbles: chart, graph, table, statistics, infographic,\n"
            "  label closeup, document, text, screen with text, logo,\n"
            "  newspaper, poster, sign. Never ask for anything whose point\n"
            "  is the writing on it.\n"
            "- ALSO FORBIDDEN, words that cannot be photographed:\n"
            "  information, flow, data, processing, transformation,\n"
            "  innovation, efficiency, future, interface, system, solution,\n"
            "  technology, digital, complex, confused, and abstract verbs.\n"
            "- If the narration cites a figure, DO NOT try to show the\n"
            "  figure: show the person or the place it is about. The\n"
            "  subtitle carries the number.\n"
            "- Output ONLY the JSON object. NO markdown fences, NO comments, NO extra text.\n"
            "\nContent:\n"
            f"{text}\n"
        )

    def _build_style_prompt(self, transcript: str, style: str) -> str:
        style_instruction = _STYLE_PROMPTS.get(
            style,
            "Write in a clear, engaging documentary style.",
        )
        return (
            "You are a video script writer.\n"
            f"Style: {style_instruction}\n"
            "\nIMPORTANT: The content is in Spanish. Write the ENTIRE script in Spanish. "
            "All narration and descriptions MUST be in Spanish.\n"
            "\nOutput ONLY valid JSON with this exact structure (no markdown, no explanation):\n"
            "{\n"
            '  "title": "Video title",\n'
            '  "scenes": [\n'
            "    {\n"
            '      "narration": "Narration text (40-120 words per scene)",\n'
            '      "visual_description": "Detailed visual: subject, lighting, composition, color palette",\n'
            '      "search_keywords": "3-5 keywords in ENGLISH separated by commas"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "\nRequirements:\n"
            "- 5 to 10 scenes\n"
            "- narration: 40-120 words per scene\n"
            "- visual_description: detailed enough for AI image generation\n"
            '- search_keywords: concise English keywords for stock photo search (e.g. "cattle ranch, countryside, sunset, livestock")\n'
            "- Output ONLY the JSON object. NO markdown fences, NO comments, NO extra text.\n"
            "\nContent:\n"
            f"{transcript}\n"
        )

    def _call_ollama_api(
        self, prompt: str, options: dict | None = None
    ) -> tuple[str, dict]:
        """Call Ollama, returns (raw_response_string, metadata_dict).

        Metadata includes ``total_duration``, ``eval_count``,
        ``prompt_eval_count`` from the API response.

        ``options`` va tal cual a la API de Ollama. Sin ella el modelo corre
        con `num_ctx` por defecto (2048 tokens), que recorta en silencio
        cualquier prompt largo: el modelo no ve el final de su propia
        instruccion y responde de mas o de menos sin avisar.
        """
        payload: dict[str, Any] = {
            "model": self.ollama_model,
            "prompt": prompt,
            "format": "json",
            "stream": False,
        }
        if options:
            payload["options"] = options

        try:
            resp = requests.post(
                f"{self.ollama_url}/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.ConnectionError:
            raise OllamaConnectionError(
                f"Cannot connect to Ollama at {self.ollama_url}. "
                "Is the server running?"
            )
        except requests.Timeout:
            raise OllamaConnectionError(
                f"Ollama timeout after {self.timeout}s"
            )

        raw = data.get("response", "")
        metadata: dict[str, Any] = {
            "total_duration": data.get("total_duration"),
            "load_duration": data.get("load_duration"),
            "prompt_eval_count": data.get("prompt_eval_count"),
            "eval_count": data.get("eval_count"),
            "eval_duration": data.get("eval_duration"),
        }
        return raw, metadata

    def _call_ollama(self, prompt: str, options: dict | None = None) -> dict:
        raw, _ = self._call_ollama_api(prompt, options)
        return self._parse_response(raw)

    def _call_ollama_sin_escenas(
        self, prompt: str, options: dict | None = None
    ) -> dict:
        """Para respuestas que NO son un guion: el arco, un plan, un indice.

        ``_parse_response`` exige la clave ``scenes`` y reintenta con un
        prompt mas estricto cuando no la encuentra. Para un guion eso esta
        bien. Para un plan es destructivo: MEDIDO el 2026-08-22, el arco de
        doce lineas entraba por ahi, el reintento lo convertia en escenas, y
        el generador por bloques recibia un plan VACIO. Relleno las doce
        lineas con "continua la historia", corrio los tres bloques sin plan
        y devolvio un guion con hechos repetidos y sin una sola cifra. JSON
        valido, doce escenas presentes, ni una excepcion.
        """
        raw, _ = self._call_ollama_api(prompt, options)
        try:
            resultado = json.loads(raw)
        except json.JSONDecodeError:
            resultado = _json_incrustado(raw)
        if not isinstance(resultado, dict):
            raise ScriptGenerationError(
                "Se esperaba un objeto JSON y llego %s" % type(resultado).__name__
            )
        return resultado

    # ── modo creativo ───────────────────────────────────────────────────

    def generar_creativo(
        self,
        tema: str,
        n_escenas: int = 0,
        segundos_por_escena: float = 7.0,
        contexto: str = "",
        minutos: float = 0.0,
    ) -> dict:
        """Guion ORIGINAL a partir de un tema. Sin fuente que respetar.

        `minutos` manda sobre `n_escenas`: se pide una duracion y las escenas
        salen de ella. Antes habia un 6 escrito a mano en dos sitios, y eso
        -no un limite tecnico- era lo que producia videos de 27 segundos.
        Con `n_escenas=0` y sin minutos se toman 6 minutos por defecto, que
        es la duracion de un documental corto.

        Este metodo NO extrae: crea. Se usa cuando el usuario pide un video
        sobre un tema, no cuando pide el contenido de una URL. Por eso el
        resultado sale marcado con ``origen: "creacion_propia"``: un guion
        inventado y uno fiel a una fuente no pueden confundirse despues.

        La diferencia con ``_generate_single_scene`` no es el modelo, es la
        correa: aquel pedia UNA escena de 40-120 palabras en tono documental
        y corria con las opciones de fabrica. Este pide una pieza completa,
        con arco narrativo, y le da al modelo contexto y temperatura para
        construirla.
        """
        # AQUI estaba el tope de verdad: min(n_escenas, 12). Ningun parametro
        # podia cruzarlo, asi que pedir un documental de seis minutos daba lo
        # mismo que pedir uno de uno. Se quita: la duracion la pide quien
        # escribe, no la decide una constante.
        #
        # Sin nada pedido se toman 6 minutos, que es un documental corto.
        # El numero de escenas sale de dos constantes declaradas arriba, no
        # de cifras escritas a mano: aqui habia un 100 duplicado que sobrevivio
        # a la correccion de PALABRAS_POR_MINUTO y seguia produciendo el
        # numero de escenas del valor viejo.
        if not n_escenas:
            mins = minutos if minutos > 0 else 6.0
            n = max(3, round(mins * PALABRAS_POR_MINUTO
                             / PALABRAS_POR_ESCENA_OBJETIVO))
        else:
            n = max(2, int(n_escenas))

        # El SEGUNDO tope, y el que de verdad recortaba: con el default de 7
        # segundos por escena esto daba 30 palabras, asi que doce escenas
        # sumaban 360 y no las 800 de ocho minutos. Quitar el tope de escenas
        # sin derivar tambien su largo solo produce mas escenas cortas.
        #
        # El ritmo sale de PALABRAS_POR_MINUTO, medido contra la voz real
        # del proyecto (ver la constante). Si se piden minutos, el largo de
        # la escena SALE de ahi.
        if minutos > 0:
            palabras_escena = max(30, round(minutos * PALABRAS_POR_MINUTO / n))
        else:
            palabras_escena = max(30, int(segundos_por_escena * 2.6))

        # El modelo entrega por debajo de lo pedido -medido: 16 palabras
        # cuando se le pidieron 30-. Un piso explicito da con que reclamar.
        piso_escena = max(20, int(palabras_escena * FRACCION_MINIMA_ENTREGA))

        prompt = (
            "Eres guionista de documental. Escribes en español de México.\n\n"
            f"TEMA: {tema}\n"
            f"DURACION: {n} escenas de ~{palabras_escena} palabras cada una, "
            f"NUNCA menos de {piso_escena}. Es narracion hablada, no un "
            f"titular: frases completas, encadenadas. Total aproximado: "
            f"{n * palabras_escena} palabras.\n"
            f"Escribe LAS {n}, completas. No resumas ni recortes.\n"
            + (f"\nCONTEXTO ADICIONAL:\n{contexto}\n" if contexto else "")
            + "\nESCRIBE UNA PIEZA COMPLETA, no una descripcion. Estructura:\n"
            "  1. GANCHO: los primeros 3 segundos deciden si siguen viendo.\n"
            "     Abre con una ESCENA, no con el dato. Un lugar, una hora, "
            "algo que se apaga o se enciende, alguien que hace algo. El dato "
            "duro viene DESPUES, cuando ya hay a quien le importe.\n"
            "     Asi NO: 'El 10 de abril de 2019 el Event Horizon Telescope "
            "publico la primera imagen de un agujero negro.' Es cierto y es "
            "una ficha.\n"
            "     Asi SI: 'Ocho telescopios repartidos entre Chile, Mexico y "
            "el Polo Sur apuntan al mismo punto del cielo. Llevan cuatro "
            "noches sin moverse.'\n"
            "     Otro ejemplo del mismo recurso: 'La impresora que registra "
            "en papel cada transaccion del banco central acaba de apagarse. "
            "Los tecnicos creen que es un atasco.'\n"
            "     NUNCA 'En el mundo de hoy' ni 'La tecnologia avanza'.\n"
            "  2. DESARROLLO: una idea por escena, encadenadas. Cada escena "
            "debe dejar una razon para ver la siguiente.\n"
            "  3. GIRO: algo que el espectador no esperaba, o el error comun "
            "que casi todos cometen.\n"
            "  4. CIERRE: una frase que se pueda repetir. Sin despedidas "
            "genericas.\n\n"
            "REGLAS DE ESCRITURA:\n"
            # Medido el 2026-08-22 contra un documental que funciona: 857
            # palabras en 5:09, o sea 166 por minuto, y ningun parrafo
            # explicativo. Cada frase pone algo en escena o suelta un dato,
            # nunca las dos cosas a la vez.
            "- Cada escena PONE ALGO EN ESCENA o SUELTA UN DATO, no las dos "
            "cosas en la misma frase. Alternar entre las dos es lo que da "
            "ritmo; mezclarlas produce parrafos que nadie escucha.\n"
            "- Los datos se dicen en concreto, no en abstracto. 'Ochenta y un "
            "millones de dolares se evaporaron' pega; 'hubo perdidas "
            "millonarias' no dice nada.\n"
            "- Termina alguna escena en suspenso, sin cerrar la idea, para "
            "que la siguiente la recoja. No todas: una o dos.\n"
            "- NUNCA escribas las etiquetas de la estructura. Prohibido que "
            "aparezca 'Gancho:', 'El giro:', 'Desarrollo:' o 'Cierre:' en la "
            "narracion. La estructura se aplica, no se anuncia.\n"
            "- Cada escena es una frase COMPLETA que se entiende sola. "
            "Prohibido partir una idea entre escenas con puntos suspensivos.\n"
            "- Ensena algo concreto: da un ejemplo real de lo que se escribe "
            "mal y como se escribe bien. Sin ejemplos no hay video.\n"
            "- El TITULO tampoco lleva cifras inventadas.\n"
            "- Frases cortas. Verbos concretos. Sin relleno corporativo.\n"
            "- Nada de 'en resumen', 'sin duda', 'es importante destacar'.\n"
            "- Habla de tu a tu, sin sonar a folleto.\n"
            "- Si mencionas un dato, que sea especifico o no lo menciones.\n"
            # Medido contra el catalogo el 2026-08-21: la consulta abstracta
            # trae catalogo abstracto. 'data processing interface information
            # flow' -> 9 clips con personas de 35; 'person watching screen text
            # appearing hands keyboard' -> 32 de 35. Mismo numero de hits, otro
            # material. Por eso la instruccion es explicita y con prohibidos.
            "- search_keywords NO es el tema de la escena: es lo que una "
            "camara veria. El banco de metraje etiqueta lo que se ve, no de "
            "que trata.\n"
            "- FORMATO EXACTO de search_keywords: 4 palabras en ingles, todas "
            "objetos o lugares que se puedan fotografiar. Describe el "
            "ESCENARIO, no a la persona ni la idea. Ej: 'laptop desk office "
            "documents', 'keyboard screen closeup hands', 'computer window "
            "office papers'.\n"
            "- PROHIBIDO en search_keywords cualquier palabra que no se pueda "
            "fotografiar: information, flow, data, processing, transformation, "
            "innovation, efficiency, productivity, future, interface, system, "
            "solution, technology, digital, y todo verbo abstracto "
            "(processes, generates, invokes, transforms).\n"
            "- Las 4 palabras describen el ESCENARIO, no la idea. Dos escenas "
            "distintas pueden compartir vocabulario: la variedad la da el "
            "metraje, no las palabras.\n"
            f"- {n} escenas, cada una de ~{palabras_escena} palabras de "
            f"narracion (nunca menos de {piso_escena}).\n\n"
            "Devuelve SOLO JSON valido, sin markdown ni explicacion:\n"
            "{\n"
            '  "title": "titulo con gancho, maximo 60 caracteres",\n'
            '  "scenes": [\n'
            "    {\n"
            '      "narration": "lo que se escucha en esta escena",\n'
            '      "visual_description": "que se ve, en una frase concreta",\n'
            '      "search_keywords": "ACCION OBSERVABLE en ingles: quien, '
            'que hace, con que. Ej: person typing laptop office"\n'
            "    }\n"
            "  ]\n"
            "}\n"
        )

        # Arriba de lo que el modelo entrega de una sentada, se descompone.
        # No es una preferencia de diseño: MEDIDO, pedir 804 palabras de
        # golpe devuelve 204 y ninguna insistencia lo mueve, porque el techo
        # es del modelo y no del prompt (LongWriter / LIFEBench; ver
        # synthesis/guion_por_bloques.py). Debajo del umbral el monolitico
        # es mejor: una sola llamada y la historia nace entera.
        if n * palabras_escena > guion_por_bloques.PALABRAS_POR_LLAMADA:
            logger.info(
                "Guion de ~%d palabras: por encima de lo que el modelo "
                "entrega en una llamada, se genera por bloques",
                n * palabras_escena)
            resultado = guion_por_bloques.generar(
                self._call_ollama, tema, n, palabras_escena,
                contexto=contexto, opciones=OPCIONES_CREATIVAS,
                llamar_plan=self._call_ollama_sin_escenas)
        else:
            resultado = self._call_ollama(prompt, options=OPCIONES_CREATIVAS)

        # El modelo no obedece todas las prohibiciones a la primera: medido el
        # 2026-08-20, sigue partiendo frases con "..." y escribiendo "el giro".
        # Repetir el prompt no lo arregla; señalarle el defecto concreto, si.
        # Las cifras van primero en la lista: un dato falso invalida el video
        # entero, mientras que un defecto de estilo solo lo afea.
        # `hechos_repetidos` es el defecto PROPIO de la generacion por
        # bloques: cada bloque abre sin ver el texto completo y puede volver
        # a contar lo que otro ya conto. MEDIDO: 2 pares de escenas
        # repetidas, con el arco funcionando y sin el. Detectarlo y no
        # pasarlo al corrector seria un detector desconectado -MF-005-.
        cifras_ini = revisar_cifras_contra_fuente(resultado, contexto or "")
        defectos = (cifras_ini
                    + revisar_guion(resultado)
                    + revisar_extension(resultado, piso_escena, n)
                    + guion_por_bloques.hechos_repetidos(resultado))
        if defectos:
            logger.info("Guion con %d defecto(s), pidiendo correccion", len(defectos))
            correccion = (
                "Este guion tiene defectos concretos. Corrigelos SIN cambiar "
                "el tema ni la cantidad de escenas:\n\n"
                + "\n".join(f"- {d}" for d in defectos)
                + "\n\nGUION A CORREGIR:\n"
                + json.dumps(resultado, ensure_ascii=False)
                + "\n\nDevuelve SOLO el JSON corregido, misma estructura.\n"
            )
            try:
                corregido = self._call_ollama(
                    correccion, options=OPCIONES_CREATIVAS
                )
                if corregido.get("scenes"):
                    cifras_c = revisar_cifras_contra_fuente(
                        corregido, contexto or "")
                    restantes = (
                        cifras_c
                        + revisar_guion(corregido)
                        + revisar_extension(corregido, piso_escena, n)
                        + guion_por_bloques.hechos_repetidos(corregido))
                    if _es_mejor(resultado, corregido,
                                 len(cifras_ini), len(cifras_c),
                                 len(defectos), len(restantes)):
                        resultado = corregido
                        defectos = restantes
            except (OllamaConnectionError, ScriptGenerationError) as e:
                logger.warning("No se pudo corregir el guion: %s", e)
            if defectos:
                # No se oculta lo que quedo mal: viaja con el guion.
                resultado["avisos"] = defectos
        # Marca de origen: sin esto, un guion inventado es indistinguible de
        # uno extraido de una fuente, que es justo como se produjo un video
        # con narracion fabricada el 2026-08-20.
        resultado["origen"] = "creacion_propia"
        resultado["tema"] = tema
        return resultado

    def _parse_response(self, raw: str) -> dict:
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(
                "First Ollama response was not valid JSON; retrying with stricter prompt"
            )
            result = self._retry_strict(raw)

        if not isinstance(result, dict) or not result.get("scenes"):
            logger.warning(
                "Ollama response was valid JSON but missing 'scenes'; retrying with stricter prompt"
            )
            result = self._retry_strict(raw)

        if not isinstance(result, dict) or not result.get("scenes"):
            raise ScriptGenerationError(
                f"Ollama returned invalid script format. "
                f"Expected dict with 'scenes' list. "
                f"Raw: {raw[:500]}"
            )
        return result

    def _retry_strict(self, raw_first_attempt: str) -> dict:
        strict_prompt = (
            "Output ONLY a valid JSON object. No markdown, no backticks, no explanation.\n"
            "IMPORTANT: All text MUST be in Spanish.\n"
            "{\n"
            '  "title": "...",\n'
            '  "scenes": [\n'
            '    { "narration": "...", "visual_description": "..." }\n'
            "  ]\n"
            "}\n"
            f"\nRe-parse this content into the JSON above:\n{raw_first_attempt}"
        )
        try:
            resp = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.ollama_model,
                    "prompt": strict_prompt,
                    "format": "json",
                    "stream": False,
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return json.loads(data.get("response", ""))
        except (requests.ConnectionError, requests.Timeout, json.JSONDecodeError) as e:
            raise ScriptGenerationError(
                f"Retry also failed after invalid JSON from Ollama. "
                f"First raw output: {raw_first_attempt[:500]}. "
                f"Retry error: {e}"
            )

    def _generate_single_scene(self, text: str, extracted_content: dict) -> dict:
        title = (
            extracted_content.get("titulo")
            or extracted_content.get("title")
            or "Video"
        )

        expand_prompt = (
            "You are a documentary script writer. Expand the following short content "
            "into a single video scene.\n"
            "IMPORTANT: The content is in Spanish. Write the ENTIRE script in Spanish. "
            "All narration and descriptions MUST be in Spanish.\n"
            "Output ONLY valid JSON (no markdown, no explanation):\n"
            "{\n"
            '  "title": "Video title",\n'
            '  "scenes": [\n'
            "    {\n"
            '      "narration": "expanded narration (40-120 words)",\n'
            '      "visual_description": "detailed visual description for image generation",\n'
            '      "search_keywords": "3-5 English keywords for stock photo"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            f"\nContent:\n{text}\n"
        )

        try:
            result = self._call_ollama(expand_prompt)
            if result.get("scenes"):
                return result
        except (OllamaConnectionError, ScriptGenerationError):
            logger.warning(
                "Ollama unavailable for single-scene expansion, using fallback"
            )

        return {
            "title": title,
            "scenes": [
                {
                    "narration": text,
                    "visual_description": f"A visual representation of: {title}",
                }
            ],
        }
