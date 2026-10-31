"""Descarga clips de VIDEO real de stock, uno por escena.

Por que existe: el pipeline venia bajando FOTOS y aplicandoles zoom. Medido con
el quality gate, eso da movimiento 2.62 contra un umbral de 5.0; el metraje real
da 14.37. Las transiciones no arreglan eso: no crean movimiento, solo tapan el
corte. Lo unico que mueve la aguja es metraje filmado.

Fuente: API de video de Pixabay (verificada: 200 OK, clips 1920x1080 de 8-15s).
Usa la misma PIXABAY_API_KEY que ya usa el generador de imagenes.

Devuelve rutas .mp4 con el mismo contrato que StockImageGenerator
(`generate(storyboard, output_dir) -> {scene_id: ruta}`), pero el adapter debe
distinguirlas: un clip NO pasa por Ken Burns.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import requests

logger = logging.getLogger(__name__)

API = "https://pixabay.com/api/videos/"
API_PEXELS = "https://api.pexels.com/videos/search"
TIMEOUT_BUSQUEDA = 60
TIMEOUT_DESCARGA = 180
MIN_ANCHO = 1280  # por debajo de esto se ve blando al escalar a 1080p

# Cuantas palabras de la consulta deben aparecer en las etiquetas del clip.
#
# Sin este filtro el generador aceptaba el PRIMER resultado que devolvia la
# API. Resultado medido (2026-08-19): para "puppy rescue hands" trajo una playa
# aerea con surfistas, y para "veterinarian caring animal" una cabra salvaje.
# El quality gate le puso 91.8/100 porque mide movimiento y nitidez, no
# pertinencia. La relevancia hay que exigirla en el origen.
MIN_COINCIDENCIAS = 1
# Cuantas palabras de la descripcion se le pasan al catalogo.
#
# Antes eran 4, con el comentario "Pixabay funciona mejor con pocas palabras
# clave que con una frase". MEDIDO contra la API el 2026-08-20: es al reves.
# Pixabay hace OR, no AND, asi que cada palabra suma candidatos en vez de
# restarlos:
#
#   "frustrated user"                              ->  33 hits
#   "frustrated user smartphone"                   -> 137 hits
#   "frustrated user smartphone struggle tech"     -> 219 hits
#   "frustrated user smartphone struggle tech issues" -> 226 hits
#
# Con OR, mas palabras traen mas material pero peor ordenado; de eso se
# encarga _elegir_con_modelo, que ahora recibe la descripcion completa.
MAX_PALABRAS_CONSULTA = 8

# Modelo local que elige el clip pertinente. Gratis, sin API.
MODELO_SELECTOR = "qwen2.5:14b-instruct-q4_K_M"

# Palabras que delatan que la escena trata de una persona. Sirven para decidir
# si el video entero se busca dentro de la categoria "people" del catalogo.
_PERSONAS = (
    "persona", "personas", "gente", "hombre", "mujer", "manos", "mano",
    "usuario", "usuaria", "trabajador", "trabajadora", "equipo", "alguien",
    "espectador", "cliente", "empleado", "empleada", "profesional",
    "person", "people", "man", "woman", "hands", "hand", "worker", "human",
    "user", "team", "someone", "employee",
)

# Umbral: si al menos la mitad de las escenas hablan de personas, el video es
# de personas. Se decide UNA vez por storyboard, no por escena, porque las
# escenas abstractas -las que mas necesitan el filtro- son justamente las que
# no nombran a nadie.
FRACCION_PERSONAS = 0.5

# Palabras que el catalogo no puede etiquetar porque no se fotografian, y que
# en una busqueda OR solo sirven para arrastrar material ajeno. La lista sale
# de lo que el modelo escribio de hecho, no de imaginar que podria escribir.
_ABSTRACTAS = frozenset((
    "information", "flow", "data", "processing", "process", "transformation",
    "innovation", "efficiency", "productivity", "future", "interface",
    "system", "solution", "technology", "digital", "displaying", "generates",
    "generating", "invokes", "transforms", "looks", "looking", "surprised",
    "stressed", "showing", "shows", "appearing", "fast", "quick", "modern",
    "smart", "before", "after", "multiple", "many", "perfect", "seamless",
))

# Con el filtro de categoria puesto, 4 palabras de escenario dieron 100%; con
# 5 o mas la busqueda OR vuelve a dispersarse.
TOPE_CON_CATEGORIA = 4

# Palabras que delatan un video tecnico -terminal, codigo, servidor- para el
# que la categoria correcta no es "people" sino "computer".
_TECNICAS = (
    "terminal", "codigo", "pantalla", "servidor", "monitor", "consola",
    "proceso", "script", "log", "logs", "comando", "pipeline", "navegador",
    "portal", "sesion", "archivo", "correo", "correos", "teclado",
    "code", "screen", "server", "console", "command", "browser", "session",
    "file", "keyboard", "laptop", "computer", "software",
)

# Cuando el pozo filtrado queda por debajo de esto, se completa con la
# busqueda sin categoria. MEDIDO el 2026-08-21 sobre el trailer de EXIT 0:
# con category=computer dos escenas quedaron con 3 y 2 candidatos, y la
# guarda que impide repetir clip entre escenas los agota.
MINIMO_CANDIDATOS = 8

# Categoria del catalogo que se aplica cuando el video es de personas.
#
# MEDIDO contra la API el 2026-08-21 sobre las 6 escenas del video de IA,
# contando que fraccion de los candidatos muestra persona Y tecnologia:
#
#   sin categoria, +Pexels, consulta abstracta   ->  41%
#   category=people, consulta abstracta          ->  56%  (gente sin pantallas)
#   category=people, consulta concreta, pp=20    ->  96%
#   category=people, consulta concreta, pp=40    ->  88%
#   category=people, consulta concreta, pp=60    ->  82%
#
# Pixabay ordena por relevancia: pedir mas profundidad trae la cola mala. Por
# eso per_page se queda en 20 y la ganancia viene del filtro, no del volumen.
CATEGORIA_PERSONAS = "people"

# Categoria para los videos tecnicos. MEDIDO el 2026-08-21 sobre las 10
# consultas del trailer de EXIT 0, contando candidatos con etiqueta tecnica:
#
#   sin categoria       54%
#   category=science    93%
#   category=computer   96%
#
# "people" no servia para este video: la heuristica lo detecto y no lo
# aplico, pero sin ninguna categoria dos escenas caian a 0% y 20%.
CATEGORIA_TECNICA = "computer"


class StockVideoGenerator:
    """Genera un clip de video por escena desde Pixabay."""

    def __init__(self, fallback_generator: Any | None = None):
        # Si no hay clip decente para una escena, se cae al generador de
        # imagenes en vez de dejar la escena vacia.
        self._fallback = fallback_generator
        # Cache de BUSQUEDA, no de archivo descargado: guardar la ruta del clip
        # provocaba que dos escenas con la misma consulta recibieran metraje
        # identico, esquivando la guarda `_usados`. Los resultados de la API
        # solo dependen de la consulta, asi que cachearlos ahorra la llamada
        # sin forzar la repeticion del clip.
        self._cache_busqueda: dict[str, list[dict]] = {}
        self._usados: set[int] = set()
        # Se fija al arrancar generate(), viendo el storyboard completo.
        self._categoria: str | None = None

    @property
    def name(self) -> str:
        return "stock-video-pixabay"

    # ── busqueda ────────────────────────────────────────────────────────

    def _consulta(self, scene: Any) -> str:
        """Consulta para el catalogo.

        Se le pide al guion que escriba palabras fotografiables, pero el modelo
        no siempre obedece: medido el 2026-08-21, para una escena escribio
        'screen displaying text person looks surprised' y el catalogo devolvio
        15% de clips con persona y tecnologia. La regla se aplica aqui tambien,
        de forma determinista, para que un guion desobediente no arruine el
        video.

        Dos podas, con motivos distintos:

        - ABSTRACTAS: con semantica OR cada palabra suma resultados, asi que
          'information' o 'surprised' arrastran material ajeno.
        - PERSONAS, solo si el filtro de categoria esta activo: la categoria ya
          garantiza que haya gente, de modo que repetir 'person' desperdicia
          una palabra que deberia describir el escenario. Medido: 'stressed
          desk paperwork' daba 100%; 'person desk paperwork', 50%.

        Con ambas podas y tope de 4 palabras, las 6 escenas del video de prueba
        pasaron de 84% a 100% de candidatos con persona Y tecnologia.
        """
        v = getattr(scene, "visual", None) or {}
        bruto = (v.get("search_keywords") or v.get("visual_description")
                 or getattr(scene, "title", "") or "")
        if isinstance(bruto, list):
            bruto = " ".join(str(x) for x in bruto)

        palabras = [p for p in str(bruto).lower().replace(",", " ").split()
                    if len(p) > 3 and p not in _ABSTRACTAS]
        if self._categoria == CATEGORIA_PERSONAS:
            palabras = [p for p in palabras if p not in _PERSONAS]
        # Sin duplicados: en OR, repetir una palabra no la pondera, solo gasta
        # el cupo.
        palabras = list(dict.fromkeys(palabras))
        if not palabras:
            # Podarlo todo dejaria la escena sin buscar. Un escenario generico
            # es peor que uno bueno, pero infinitamente mejor que nada.
            return "office desk computer"
        tope = (TOPE_CON_CATEGORIA if self._categoria else MAX_PALABRAS_CONSULTA)
        return " ".join(palabras[:tope])

    @staticmethod
    def _descripcion(scene: Any) -> str:
        """La descripcion visual COMPLETA, sin recortar.

        No sirve para buscar -el catalogo no entiende gramatica
        cinematografica- pero si para que el modelo elija entre lo que la
        busqueda si encontro.
        """
        v = getattr(scene, "visual", None) or {}
        desc = v.get("description") or v.get("visual_description") or ""
        if isinstance(desc, list):
            desc = " ".join(str(x) for x in desc)
        return str(desc).strip()

    @classmethod
    def _video_de_personas(cls, storyboard: Any) -> bool:
        """Decide si el video entero se busca dentro de la categoria 'people'.

        Se mira el storyboard completo y no cada escena por separado: las
        escenas abstractas ('la informacion fluye', 'transicion del desorden al
        orden') no nombran a nadie, y son precisamente las que traian catalogo
        sin gente. Decidir por escena habria dejado el filtro apagado justo
        donde hacia falta.
        """
        escenas = list(getattr(storyboard, "scenes", []) or [])
        if not escenas:
            return False
        con_gente = 0
        for s in escenas:
            texto = "%s %s" % (getattr(s, "narration", ""), cls._descripcion(s))
            palabras = set(re.findall(r"[a-zA-Záéíóúñü]+", texto.lower()))
            if palabras & set(_PERSONAS):
                con_gente += 1
        return con_gente >= len(escenas) * FRACCION_PERSONAS

    @classmethod
    def _categoria_del_video(cls, storyboard: Any) -> str | None:
        """Elige UNA categoria del catalogo para todo el video.

        Las personas ganan cuando las hay: medido, un video de gente
        trabajando con computadora sale mejor con 'people' (97% de clips con
        persona Y tecnologia) que con 'computer'. Cuando no hay gente pero el
        tema es tecnico -terminales, servidores, procesos- 'computer' levanta
        la pertinencia del 54% al 96%.

        Si no es ninguna de las dos se devuelve None y se busca sin filtro:
        inventar una categoria para un tema que no encaja seria peor.
        """
        if cls._video_de_personas(storyboard):
            return CATEGORIA_PERSONAS

        escenas = list(getattr(storyboard, "scenes", []) or [])
        if not escenas:
            return None
        tecnicas = 0
        for s in escenas:
            texto = "%s %s" % (getattr(s, "narration", ""), cls._descripcion(s))
            palabras = set(re.findall(r"[a-zA-Záéíóúñü]+", texto.lower()))
            if palabras & set(_TECNICAS):
                tecnicas += 1
        if tecnicas >= len(escenas) * FRACCION_PERSONAS:
            return CATEGORIA_TECNICA
        return None

    def _buscar(self, consulta: str, key: str) -> list[dict]:
        hits = self._pixabay(consulta, key, self._categoria)
        if not self._categoria:
            return hits
        # El pozo filtrado puede quedar demasiado chico para alimentar todas
        # las escenas sin repetir clip. Se completa con la busqueda sin
        # categoria, SIN pisar el orden: lo filtrado va primero, que es lo
        # pertinente, y lo demas queda de reserva.
        if len(hits) < MINIMO_CANDIDATOS:
            vistos = {h.get("id") for h in hits}
            extra = [h for h in self._pixabay(consulta, key, None)
                     if h.get("id") not in vistos]
            if extra:
                logger.info("stock_video: %r solo dio %d en '%s'; se completa "
                            "con %d sin categoria", consulta, len(hits),
                            self._categoria, len(extra))
            hits = hits + extra
        return hits

    @staticmethod
    def _pixabay(consulta: str, key: str, categoria: str | None) -> list[dict]:
        params = {"key": key, "q": consulta, "per_page": 20,
                  "video_type": "film", "safesearch": "true"}
        if categoria:
            params["category"] = categoria
        try:
            r = requests.get(API, params=params, timeout=TIMEOUT_BUSQUEDA)
        except requests.RequestException as e:
            logger.warning("stock_video: fallo la busqueda de %r: %s", consulta, e)
            return []
        if r.status_code != 200:
            logger.warning("stock_video: Pixabay devolvio %d para %r",
                           r.status_code, consulta)
            return []
        return r.json().get("hits", [])

    def _buscar_pexels(self, consulta: str, key: str) -> list[dict]:
        """Mismo formato de salida que Pixabay: {'id', 'tags', 'videos': {...}}."""
        try:
            r = requests.get(
                API_PEXELS,
                headers={"Authorization": key},
                params={"query": consulta, "per_page": 15, "orientation": "landscape"},
                timeout=TIMEOUT_BUSQUEDA,
            )
        except requests.RequestException as e:
            logger.warning("pexels: fallo la busqueda de %r: %s", consulta, e)
            return []
        if r.status_code != 200:
            logger.warning("pexels: devolvio %d para %r", r.status_code, consulta)
            return []

        salida = []
        for v in r.json().get("videos", []):
            archivos = {
                str(i): {"url": f.get("link"), "width": f.get("width", 0),
                         "height": f.get("height", 0)}
                for i, f in enumerate(v.get("video_files", []))
                if f.get("link")
            }
            if not archivos:
                continue
            # Pexels no da tags; se usa la descripcion de la url como texto.
            salida.append({
                "id": "pexels-%s" % v.get("id"),
                "tags": (v.get("url") or "").replace("-", " ").replace("/", " "),
                "videos": archivos,
            })
        return salida

    @staticmethod
    def _es_pertinente(hit: dict, consulta: str) -> bool:
        """Al menos una palabra de la consulta debe aparecer en las etiquetas.

        Es un filtro modesto a proposito: exigir demasiado deja escenas sin
        clip. Pero descarta lo groseramente ajeno, que es lo que pasaba.
        """
        etiquetas = str(hit.get("tags", "")).lower()
        if not etiquetas:
            return True  # sin etiquetas no se puede juzgar; no se penaliza
        palabras = {p for p in consulta.lower().split() if len(p) > 3}
        coincide = sum(1 for p in palabras if p in etiquetas)
        return coincide >= MIN_COINCIDENCIAS

    @staticmethod
    def _mejor_archivo(hit: dict) -> dict | None:
        """El de mayor ancho que llegue al minimo."""
        opciones = [v for v in (hit.get("videos") or {}).values()
                    if isinstance(v, dict) and v.get("url")]
        if not opciones:
            return None
        opciones.sort(key=lambda v: v.get("width", 0), reverse=True)
        mejor = opciones[0]
        return mejor if mejor.get("width", 0) >= MIN_ANCHO else None

    # ── contrato ────────────────────────────────────────────────────────

    def generate(self, storyboard: Any, output_dir: str) -> dict[str, str]:
        os.makedirs(output_dir, exist_ok=True)
        key = (os.environ.get("PIXABAY_API_KEY") or "").strip()

        resultados: dict[str, str] = {}
        sin_clip: list[Any] = []

        self._categoria = self._categoria_del_video(storyboard)
        logger.info("stock_video: categoria del catalogo = %s",
                    self._categoria or "ninguna")

        if not key:
            logger.warning("stock_video: falta PIXABAY_API_KEY; todo va a fallback")
            sin_clip = list(storyboard.scenes)
        else:
            for scene in storyboard.scenes:
                consulta = self._consulta(scene)
                destino = os.path.join(output_dir, f"{scene.id}_stock.mp4")

                ruta = self._descargar_primero(
                    consulta, key, destino,
                    getattr(scene, "narration", ""),
                    self._descripcion(scene),
                )
                if ruta:
                    resultados[scene.id] = ruta
                else:
                    logger.info("stock_video: sin clip para %s (%r)", scene.id, consulta)
                    sin_clip.append(scene)

        # Las escenas sin clip caen al generador de imagenes, si hay.
        if sin_clip and self._fallback is not None:
            class _Parcial:
                scenes = sin_clip
            try:
                resultados.update(self._fallback.generate(_Parcial(), output_dir))
            except Exception as e:
                logger.warning("stock_video: el fallback tambien fallo: %s", e)

        logger.info("stock_video: %d clips, %d por fallback",
                    len(resultados) - len(sin_clip), len(sin_clip))
        return resultados

    def _elegir_con_modelo(self, candidatos: list[dict], tema: str,
                           narracion: str, descripcion: str = "") -> list[dict]:
        """Reordena los candidatos preguntandole al modelo local cual pega.

        El filtro por palabras no alcanza: para "puppy rescue hands" la palabra
        "rescue" coincide igual con un rescate en la playa, uno de bomberos y
        uno de un cachorro. Coincidir palabras no es entender.

        Se usa qwen2.5 local (gratis, sin API). Si Ollama no responde, se
        devuelve el orden original: el fallo degrada, no rompe.
        """
        if not (tema or narracion) or len(candidatos) < 2:
            return candidatos

        lista = "\n".join(
            "%d. %s" % (i + 1, str(c.get("tags", ""))[:110])
            for i, c in enumerate(candidatos[:12])
        )
        # Se le dan AMBAS cosas: el tema concreto (las palabras clave) y la
        # narracion. Con la narracion sola fallaba: para "un sonido debil entre
        # los escombros" el modelo no puede inferir que hay un cachorro, y
        # elegia una playa. Las palabras clave dicen el QUE; la narracion, el
        # tono.
        # La descripcion visual es lo unico que dice QUE debe verse: la consulta
        # al catalogo son palabras sueltas y la narracion dice la idea, no la
        # imagen. Sin esto el modelo elegia por tema, no por lo que se ve.
        bloque_visual = (
            "LO QUE DEBE VERSE: %s\n" % descripcion[:400] if descripcion else ""
        )
        prompt = (
            "Eres director audiovisual. Vas a ELEGIR metraje de archivo, no a "
            "describirlo.\n\n"
            "TEMA de la escena: %s\n"
            "%s"
            "NARRACION: \"%s\"\n\n"
            "Etiquetas de %d clips disponibles:\n%s\n\n"
            "COMO ELEGIR:\n"
            "1. Traduce la narracion a una ACCION OBSERVABLE. Si la frase es "
            "abstracta ('la informacion fluye', 'el trabajo se transforma'), "
            "pregunta que estaria HACIENDO una persona en ese momento.\n"
            "2. PREFIERE: personas interactuando con tecnologia, manos "
            "trabajando, pantallas en uso, movimiento humano con proposito.\n"
            "3. EVITA: metaforas literales de la palabra (maquinaria "
            "industrial para 'maquina', engranajes para 'proceso'), personas "
            "posando sin hacer nada, papel cuando se habla de trabajo digital, "
            "y paisajes o abstracciones cuando hay una accion humana descrita.\n"
            "4. Entre dos clips parecidos, gana el que muestre la accion; "
            "entre uno literal y uno con accion, gana el de la accion.\n\n"
            "Responde SOLO con el numero del clip elegido. Si ninguno sirve, "
            "responde 0. Solo el numero."
            % (tema, bloque_visual, narracion[:200],
               min(len(candidatos), 12), lista)
        )
        try:
            r = requests.post(
                "http://localhost:11434/api/generate",
                json={"model": MODELO_SELECTOR, "prompt": prompt, "stream": False,
                      "options": {"temperature": 0}},
                timeout=90,
            )
            if r.status_code != 200:
                return candidatos
            crudo = "".join(ch for ch in r.json().get("response", "") if ch.isdigit())
            valor = int(crudo[:2]) if crudo else None
        except (requests.RequestException, ValueError, KeyError):
            return candidatos

        if valor == 0:
            # El modelo respondio 0: NINGUN candidato representa la escena. No se
            # baja el primero al azar -eso es lo que metia gimnasia para 'excel'
            # (defecto observado #5/#11/#14/#15, 2026-09-22)-. Se devuelve vacio:
            # `_descargar_primero` no encuentra clip y la escena cae al fallback
            # de imagenes. Un fallo de RED deja `valor` en None y NO entra aqui:
            # ahi se degrada devolviendo el orden original, como antes.
            logger.info("stock_video: el modelo descarto todos los candidatos (0)")
            return []
        if valor is not None and 1 <= valor <= len(candidatos):
            logger.info("stock_video: el modelo eligio el candidato %d", valor)
            return ([candidatos[valor - 1]]
                    + [c for i, c in enumerate(candidatos) if i != valor - 1])
        return candidatos

    def _candidatos(self, consulta: str, key: str, narracion: str = "",
                    descripcion: str = "") -> list[dict]:
        """Une los dos catalogos y descarta lo que no viene al caso."""
        if consulta in self._cache_busqueda:
            hits = list(self._cache_busqueda[consulta])
        else:
            hits = self._buscar(consulta, key)
            # Pexels no tiene categorias, asi que cuando el filtro de personas
            # esta activo diluye lo que el filtro acaba de depurar. Medido el
            # 2026-08-21 sobre las mismas 6 escenas: solo Pixabay filtrado da
            # 96% de clips con persona Y tecnologia; sumando Pexels baja a 86%.
            # Se pierde catalogo (20 en vez de 35 por escena) pero quedan 45
            # clips distintos para 6 escenas, de sobra para no repetir.
            pex = (os.environ.get("PEXELS_API_KEY") or "").strip()
            if pex and not self._categoria:
                hits += self._buscar_pexels(consulta, pex)
            self._cache_busqueda[consulta] = list(hits)

        pertinentes = [h for h in hits if self._es_pertinente(h, consulta)]
        logger.info("stock_video: %r -> %d candidatos, %d pertinentes",
                    consulta, len(hits), len(pertinentes))
        # Si el filtro deja todo afuera se usa el crudo: mejor un clip flojo
        # que una escena vacia.
        base = pertinentes or hits
        return self._elegir_con_modelo(base, consulta, narracion, descripcion)

    def _descargar_primero(self, consulta: str, key: str, destino: str,
                           narracion: str = "",
                           descripcion: str = "") -> str | None:
        for hit in self._candidatos(consulta, key, narracion, descripcion):
            if hit.get("id") in self._usados:
                continue  # no repetir el mismo clip en dos escenas
            archivo = self._mejor_archivo(hit)
            if not archivo:
                continue
            try:
                with requests.get(archivo["url"], stream=True,
                                  timeout=TIMEOUT_DESCARGA) as resp:
                    if resp.status_code != 200:
                        continue
                    with open(destino, "wb") as f:
                        for trozo in resp.iter_content(1 << 16):
                            f.write(trozo)
            except (requests.RequestException, OSError) as e:
                logger.debug("stock_video: fallo la descarga: %s", e)
                continue

            if os.path.getsize(destino) > 100_000:
                self._usados.add(hit.get("id"))
                logger.info("stock_video: %r -> id=%s [%s] %dx%d, %.1f MB",
                            consulta, hit.get("id"),
                            str(hit.get("tags", ""))[:70],
                            archivo.get("width"), archivo.get("height"),
                            os.path.getsize(destino) / 1e6)
                return destino
        return None
