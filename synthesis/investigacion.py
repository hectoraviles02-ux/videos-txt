# -*- coding: utf-8 -*-
"""Investigacion de un tema en FUENTES OFICIALES, con la cita pegada al dato.

Resuelve el caso en que el material no esta en `procesado/`: se pide un
documental sobre un tema y hay que ir a buscarlo. La regla del proyecto no
cambia -todo dato debe poder rastrearse- solo cambia de donde sale.

**Por que no una enciclopedia.** Un documental se sostiene en quien firma el
dato. "Segun el Event Horizon Telescope" pesa; "segun una enciclopedia
abierta" no. Todas las fuentes de aqui son institucionales y cada hecho sale
con el nombre de la institucion, la URL y la fecha.

**Que se comprobo el 2026-08-22 desde esta maquina**, sin ninguna llave:

    arXiv          responde  |  papers con fecha y resumen tecnico
    NASA           responde  |  material con descripcion y fecha
    CrossRef       responde  |  metadatos de articulos revisados por pares
    Europe PMC     responde  |  biomedicina
    ESO (RSS)      FALLA     |  certificado SSL rechazado

arXiv quedo como fuente principal para ciencia: devuelve resumenes densos en
cifras, esta al dia -el primer resultado de la prueba era del dia anterior- y
cada trabajo tiene identificador permanente.

Lo que este modulo NO hace: juzgar si la fuente tiene razon. Devuelve hechos
**atribuidos**. Esa es la unica promesa, y por eso la atribucion viaja pegada
al texto y no en una lista aparte.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

CABECERAS = {"User-Agent": "VIDEOS-TXT/1.0 (generador de documentales)"}
TIMEOUT = 30

OLLAMA_URL = "http://localhost:11434/api/generate"
MODELO = "qwen2.5:14b-instruct-q4_K_M"

# Documentos por fuente. Se subio de 5 a 15: un documental de seis minutos
# necesita mucho mas material del que da un video de treinta segundos, y el
# guion elige de lo que hay. Traer de mas cuesta segundos; quedarse corto
# cuesta un video pobre.
DOCS_POR_FUENTE = 15
HECHOS_POR_DOC = 8

# Cuantos hechos se consideran material suficiente. Por DEBAJO ya no se
# bloquea: se avisa.
#
# MEDIDO el 2026-08-22 y por eso cambio: "el robo al Banco de Bangladesh" dio
# 4 hechos y el sistema se NEGO a producir; "ciberataques al sistema
# financiero" dio 18, de paises distintos, y produjo una lista sin historia.
# O sea que la compuerta bloqueaba justo el tema que SI tenia historia y
# dejaba pasar el que no la tenia. Contar hechos no mide si cuentan lo mismo.
#
# La honestidad no se pierde: el guion sin material suficiente sale marcado
# como creacion propia, que es lo que la regla del proyecto pide.
MINIMO_HECHOS = 8

# Largo maximo de la consulta que se manda a las fuentes. MEDIDO el
# 2026-09-01: un tema de 3000 caracteres hizo que OpenAlex y arXiv
# respondieran 400 y PubMed 414 (URI demasiado larga). Ninguna busqueda se
# llego a hacer y el resultado -0 hechos- parecia decir que el tema no tenia
# fuentes. 240 caracteres son unas 35 palabras: de sobra para un tema, y por
# debajo de lo que cualquiera de las tres APIs rechaza.
MAX_CONSULTA = 240


def _consulta_util(texto: str) -> str:
    """Recorta el tema a algo que un buscador academico pueda recibir.

    Se corta en el ultimo espacio para no partir una palabra a la mitad: media
    palabra en una busqueda por relevancia hace ruido.
    """
    t = " ".join(str(texto or "").split())
    if len(t) <= MAX_CONSULTA:
        return t
    corte = t[:MAX_CONSULTA]
    espacio = corte.rfind(" ")
    return corte[:espacio] if espacio > MAX_CONSULTA // 2 else corte


@dataclass
class Documento:
    titulo: str
    texto: str
    url: str
    institucion: str
    fecha: str = ""


@dataclass
class Hecho:
    """Un dato con quien lo firma. Sin fuente no es un hecho, es un rumor."""

    texto: str
    institucion: str
    url: str
    fecha: str = ""

    def cita(self) -> str:
        return "%s%s" % (self.institucion, ", %s" % self.fecha[:4]
                         if self.fecha else "")

    def como_dict(self) -> dict:
        return {"h": self.texto, "institucion": self.institucion,
                "url": self.url, "fecha": self.fecha}


@dataclass
class Investigacion:
    tema: str
    hechos: list[Hecho] = field(default_factory=list)
    documentos: list[dict] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def fuentes(self) -> list[str]:
        return sorted({"%s — %s" % (h.institucion, h.url) for h in self.hechos})


class SinFuentes(RuntimeError):
    """No hay material suficiente. Se dice; no se inventa un documental."""


# ── fuentes ──────────────────────────────────────────────────────────────

def _get(url: str) -> str | None:
    try:
        r = requests.get(url, headers=CABECERAS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        logger.warning("investigacion: fallo %s (%s)", url[:70], e)
        return None


_CACHE_INGLES: dict[str, str] = {}


# A partir de aqui, lo que llega ya no es un tema: es un texto. MEDIDO el
# 2026-09-03, doce palabras es donde deja de tener sentido traducir y empieza
# a tenerlo extraer.
PALABRAS_PARA_TEXTO = 12


def terminos_de(texto: str) -> str:
    """Los terminos de busqueda academica que representan a un texto.

    Se usa cuando el usuario pega un articulo en vez de escribir un tema. La
    traduccion literal de una oracion no sirve como consulta: un buscador
    academico indexa terminos, no frases. Si el modelo no responde se cae a
    `a_ingles`, que al menos traduce.
    """
    try:
        r = _pedir(
            "De este texto saca los terminos con los que se buscaria en una "
            "base academica (PubMed, arXiv). EN INGLES.\n"
            "- Entre 3 y 6 terminos, separados por espacios.\n"
            "- Conceptos del campo, no palabras de la oracion: si el texto "
            "habla de cereal de caja y azucar, los terminos son "
            "'breakfast cereal glycemic index added sugars'.\n"
            "- Nada de verbos, articulos ni conectores.\n\n"
            "TEXTO: %s\n\n"
            'Responde JSON: {"terminos":"..."}' % texto[:1500],
            num_predict=60,
        )
        v = " ".join((r.get("terminos") or "").split())
    except (requests.RequestException, ValueError, KeyError) as e:
        logger.warning("investigacion: no se pudieron sacar terminos (%s)", e)
        v = ""
    if not v:
        return a_ingles(texto)
    logger.info("investigacion: texto de %d palabras -> terminos %r",
                len(texto.split()), v)
    return v


def a_ingles(tema: str) -> str:
    """Traduce el tema al ingles para consultar las fuentes.

    Las tres fuentes son de habla inglesa y el usuario escribe en espanol.
    MEDIDO el 2026-08-22 contra arXiv, contando resultados:

        "agujeros negros supermasivos"  entre comillas ->  0
        agujeros negros supermasivos    sin comillas   ->  5
        supermassive black holes        en ingles      ->  5

    Sin comillas ya devuelve algo, pero por coincidencia de palabras sueltas.
    Traducido, los resultados son del tema. Si el modelo no responde se
    devuelve el original: perder pertinencia es mejor que no buscar.
    """
    clave = tema.strip().lower()
    if clave in _CACHE_INGLES:
        return _CACHE_INGLES[clave]
    try:
        r = _pedir(
            "Traduce este tema de busqueda cientifica al ingles. Solo la "
            "traduccion, sin explicar. Si ya esta en ingles, devuelvelo "
            "igual.\n\nTEMA: %s\n\n"
            'Responde JSON: {"ingles":"..."}' % tema,
            num_predict=80,
        )
        v = (r.get("ingles") or "").strip()
    except (requests.RequestException, ValueError, KeyError) as e:
        logger.warning("investigacion: no se pudo traducir %r (%s)", tema, e)
        v = ""
    salida = v if v else tema
    _CACHE_INGLES[clave] = salida
    if salida != tema:
        logger.info("investigacion: %r -> %r", tema, salida)
    return salida


def fuente_arxiv(tema: str, limite: int = DOCS_POR_FUENTE) -> list[Documento]:
    """Papers de arXiv. Es la fuente densa: resumenes llenos de cifras.

    SIN comillas a proposito: exigir frase exacta devolvia cero para
    cualquier tema de mas de dos palabras. Ver `a_ingles`.
    """
    url = ("http://export.arxiv.org/api/query?search_query=all:%s"
           "&sortBy=relevance&max_results=%d"
           % (urllib.parse.quote(tema), limite))
    xml = _get(url)
    if not xml:
        return []
    docs = []
    for m in re.finditer(r"<entry>(.*?)</entry>", xml, re.S):
        e = m.group(1)
        t = re.search(r"<title>(.*?)</title>", e, re.S)
        s = re.search(r"<summary>(.*?)</summary>", e, re.S)
        i = re.search(r"<id>(.*?)</id>", e)
        p = re.search(r"<published>(.*?)</published>", e)
        if not (t and s and i):
            continue
        docs.append(Documento(
            titulo=re.sub(r"\s+", " ", t.group(1)).strip(),
            texto=re.sub(r"\s+", " ", s.group(1)).strip(),
            url=i.group(1).strip(),
            institucion="arXiv",
            fecha=p.group(1)[:10] if p else "",
        ))
    return docs


def fuente_nasa(tema: str, limite: int = DOCS_POR_FUENTE) -> list[Documento]:
    """Material de la NASA. Menos denso que un paper, mas divulgativo."""
    crudo = _get("https://images-api.nasa.gov/search?q=%s&media_type=image"
                 % urllib.parse.quote(tema))
    if not crudo:
        return []
    try:
        items = json.loads(crudo)["collection"]["items"]
    except (ValueError, KeyError):
        return []
    docs = []
    for it in items[:limite]:
        d = (it.get("data") or [{}])[0]
        desc = re.sub(r"\s+", " ", d.get("description") or "").strip()
        if len(desc.split()) < 25:
            continue
        docs.append(Documento(
            titulo=(d.get("title") or "").strip(),
            texto=desc,
            url=(it.get("href") or "https://images.nasa.gov/"),
            institucion="NASA",
            fecha=(d.get("date_created") or "")[:10],
        ))
    return docs


def fuente_europepmc(tema: str,
                     limite: int = DOCS_POR_FUENTE) -> list[Documento]:
    """Literatura biomedica revisada por pares."""
    crudo = _get("https://www.ebi.ac.uk/europepmc/webservices/rest/search"
                 "?query=%s&format=json&resultType=core&pageSize=%d"
                 % (urllib.parse.quote(tema), limite))
    if not crudo:
        return []
    try:
        res = json.loads(crudo)["resultList"]["result"]
    except (ValueError, KeyError):
        return []
    docs = []
    for r in res:
        resumen = re.sub(r"\s+", " ", r.get("abstractText") or "").strip()
        if len(resumen.split()) < 30:
            continue
        doi = r.get("doi")
        docs.append(Documento(
            titulo=(r.get("title") or "").strip(),
            texto=resumen,
            url="https://doi.org/%s" % doi if doi
                else "https://europepmc.org/article/%s/%s"
                     % (r.get("source", "MED"), r.get("id", "")),
            institucion=(r.get("journalTitle") or "Europe PMC").strip(),
            fecha=(r.get("firstPublicationDate") or "")[:10],
        ))
    return docs


def _texto_openalex(w: dict) -> str:
    """OpenAlex guarda el resumen como indice invertido, no como texto.

    Es un diccionario palabra -> posiciones. Hay que rearmarlo.
    """
    ai = w.get("abstract_inverted_index")
    if not ai:
        return ""
    try:
        largo = max(max(v) for v in ai.values() if v) + 1
    except ValueError:
        return ""
    palabras = [""] * largo
    for palabra, posiciones in ai.items():
        for i in posiciones:
            if 0 <= i < largo:
                palabras[i] = palabra
    return " ".join(p for p in palabras if p)


def fuente_openalex(tema: str, limite: int = DOCS_POR_FUENTE) -> list[Documento]:
    """OpenAlex: trabajos academicos de TODAS las disciplinas.

    Es la fuente general. arXiv cubre fisica y computacion; Europe PMC,
    biomedicina. Un documental sobre economia, derecho o historia no
    encontraba nada en ninguna de las dos, y el bot respondia "no hay
    material" cuando el problema eran las fuentes, no el tema.

    MEDIDO el 2026-08-22 con la consulta traducida y ordenada por relevancia:
    "Mexico labor reform" -> 102,037 trabajos; "Spanish conquest of Mexico
    history" -> 22,486, con resultados del tema y no coincidencias sueltas.

    Se filtra por `has_abstract`: un titulo suelto no da un hecho.
    """
    url = ("https://api.openalex.org/works?search=%s&per-page=%d"
           "&filter=has_abstract:true"
           % (urllib.parse.quote(tema), limite))
    crudo = _get(url)
    if not crudo:
        return []
    try:
        res = json.loads(crudo).get("results") or []
    except ValueError:
        return []
    docs = []
    for w in res:
        texto = _texto_openalex(w)
        if len(texto.split()) < 30:
            continue
        fuente = ((w.get("primary_location") or {}).get("source") or {})
        docs.append(Documento(
            titulo=(w.get("title") or "").strip(),
            texto=texto,
            url=w.get("doi") or w.get("id") or "https://openalex.org/",
            institucion=(fuente.get("display_name") or "OpenAlex").strip(),
            fecha=str(w.get("publication_year") or ""),
        ))
    return docs


def fuente_banco_mundial(tema: str,
                         limite: int = DOCS_POR_FUENTE) -> list[Documento]:
    """Indicadores del Banco Mundial: cifras duras, no prosa.

    No devuelve articulos sino series de datos. Sirve cuando el documental
    necesita una magnitud -PIB, poblacion, esperanza de vida- y no una
    explicacion. Se busca el indicador por nombre y se traen sus ultimos
    valores para Mexico y el mundo.
    """
    # La API no tiene busqueda de texto sobre indicadores, asi que se trae el
    # catalogo de la fuente 2 (World Development Indicators) y se filtra aqui.
    # Con una pagina chica solo salian los primeros alfabeticamente y nunca
    # coincidia nada: verificado, "population growth" devolvia cero.
    busca = _get("https://api.worldbank.org/v2/indicator?format=json"
                 "&per_page=2000&source=2")
    if not busca:
        return []
    try:
        datos = json.loads(busca)
        indicadores = datos[1] if len(datos) > 1 else []
    except (ValueError, IndexError, KeyError):
        return []
    claves = {p for p in re.findall(r"\w+", tema.lower()) if len(p) > 4}
    if not claves:
        return []
    # Se exigen DOS coincidencias, no una. Con una sola, "population growth"
    # devolvia "Access to clean fuels for cooking, rural (% of rural
    # population)": comparte "population" y no tiene nada que ver.
    elegidos = []
    for i in indicadores:
        nombre = set(re.findall(r"\w+", (i.get("name") or "").lower()))
        comunes = len(claves & nombre)
        if comunes >= min(2, len(claves)):
            elegidos.append(i)
        if len(elegidos) >= limite:
            break
    docs = []
    for ind in elegidos:
        cod = ind.get("id")
        serie = _get("https://api.worldbank.org/v2/country/MX/indicator/%s"
                     "?format=json&per_page=3" % cod)
        if not serie:
            continue
        try:
            filas = json.loads(serie)[1] or []
        except (ValueError, IndexError, TypeError):
            continue
        valores = [f for f in filas if f.get("value") is not None]
        if not valores:
            continue
        texto = "%s. Valores para Mexico: %s." % (
            ind.get("name"),
            "; ".join("%s: %s" % (f.get("date"), f.get("value"))
                      for f in valores[:3]))
        docs.append(Documento(
            titulo=ind.get("name") or cod,
            texto=texto,
            url="https://data.worldbank.org/indicator/%s" % cod,
            institucion="Banco Mundial",
            fecha=str(valores[0].get("date") or ""),
        ))
    return docs


def fuente_pubmed(tema: str, limite: int = DOCS_POR_FUENTE) -> list[Documento]:
    """PubMed, de los Institutos Nacionales de Salud de Estados Unidos."""
    busca = _get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
                 "?db=pubmed&term=%s&retmode=json&retmax=%d"
                 % (urllib.parse.quote(tema), limite))
    if not busca:
        return []
    try:
        ids = json.loads(busca)["esearchresult"]["idlist"]
    except (ValueError, KeyError):
        return []
    if not ids:
        return []
    resumenes = _get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary."
                     "fcgi?db=pubmed&id=%s&retmode=json" % ",".join(ids))
    if not resumenes:
        return []
    try:
        r = json.loads(resumenes)["result"]
    except (ValueError, KeyError):
        return []
    docs = []
    for pid in ids:
        d = r.get(pid) or {}
        titulo = (d.get("title") or "").strip()
        if len(titulo.split()) < 6:
            continue
        revista = (d.get("fulljournalname") or d.get("source")
                   or "PubMed").strip()
        docs.append(Documento(
            titulo=titulo,
            # esummary no trae el resumen completo; el titulo de un trabajo
            # medico ya suele traer el hallazgo y la poblacion estudiada.
            texto="%s. Publicado en %s." % (titulo, revista),
            url="https://pubmed.ncbi.nlm.nih.gov/%s/" % pid,
            institucion=revista,
            fecha=(d.get("pubdate") or "")[:10],
        ))
    return docs


# Todas se consultan y sus hechos se juntan; las que no devuelvan nada dejan
# un aviso y no interrumpen. OpenAlex va primero porque es la unica que cubre
# cualquier disciplina: las demas son especialistas.
#
# ESO quedo fuera por su certificado SSL. Comprobado el 2026-08-22 y tampoco
# alcanzables sin llave o desde esta red: OMS/GHO (agota el tiempo), INEGI
# (400), datos.gob.mx (inalcanzable), FMI (403), Semantic Scholar (429),
# UNESCO (404).
FUENTES = {
    "openalex": fuente_openalex,
    "arxiv": fuente_arxiv,
    "pubmed": fuente_pubmed,
    "nasa": fuente_nasa,
    "europepmc": fuente_europepmc,
    "banco_mundial": fuente_banco_mundial,
}


# ── extraccion de hechos ─────────────────────────────────────────────────

def _pedir(prompt: str, num_predict: int = 3000) -> dict:
    r = requests.post(
        OLLAMA_URL,
        json={"model": MODELO, "prompt": prompt, "stream": False,
              "format": "json",
              "options": {"temperature": 0.25, "top_p": 0.9,
                          "num_ctx": 32768, "num_predict": num_predict}},
        timeout=900,
    )
    r.raise_for_status()
    return json.loads(r.json()["response"])


def hechos_de(doc: Documento, tema: str) -> list[Hecho]:
    """El modelo lee el documento y saca hechos concretos, en espanol.

    Las fuentes cientificas estan en ingles y el documental es en espanol, asi
    que aqui tambien se traduce. Traducir NO es inventar mientras el dato siga
    siendo el mismo; por eso se le exige copiar las cifras textuales.
    """
    prompt = (
        "Estas preparando un documental en espanol sobre: %s\n\n"
        "FUENTE: %s (%s)\nTITULO: %s\n\nTEXTO:\n%s\n\n"
        "PRIMERO decide si este documento habla DEL TEMA. Un documento "
        "puede estar lleno de cifras y no tener NADA que ver: si el tema "
        "es una salsa picante y el texto es de fisica de particulas, la "
        "respuesta correcta es lista vacia, por mucha cifra que traiga.\n"
        "- Si el documento NO trata del tema, devuelve una lista vacia. "
        "No busques como conectarlo. No lo fuerces.\n\n"
        "Si SI trata del tema, saca los hechos CONCRETOS: cifras, fechas, "
        "nombres propios, "
        "mediciones, descubrimientos con su ano. Nada de generalidades ni "
        "definiciones de diccionario.\n"
        "- Escribelos EN ESPANOL, aunque la fuente este en ingles.\n"
        "- Copia las cifras TEXTUALES. No las redondees ni las conviertas.\n"
        "- Si el texto no trae nada concreto, devuelve lista vacia. Es "
        "preferible a inventar.\n\n"
        'Responde JSON: {"hechos":["...","..."]} Maximo %d.'
        % (tema, doc.institucion, doc.fecha or "sin fecha", doc.titulo,
           doc.texto[:4000], HECHOS_POR_DOC)
    )
    try:
        r = _pedir(prompt)
    except (requests.RequestException, ValueError, KeyError) as e:
        logger.warning("investigacion: %r sin hechos (%s)", doc.titulo[:50], e)
        return []
    salida = []
    for h in r.get("hechos") or []:
        if isinstance(h, str) and len(h.split()) >= 5:
            salida.append(Hecho(h.strip(), doc.institucion, doc.url, doc.fecha))
    return salida


def investigar(tema: str, fuentes: list[str] | None = None) -> Investigacion:
    """De un tema a hechos con cita institucional.

    Levanta `SinFuentes` si no hay material suficiente. La compuerta es
    deliberada: un documental armado sobre cuatro generalidades es peor que no
    hacerlo, y quien lo pidio merece saber que el tema no dio.
    """
    inv = Investigacion(tema=tema)
    fuera_de_tema = 0
    # Las fuentes son de habla inglesa; el tema llega en espanol. Y se
    # recorta ANTES de traducir: traducir 3000 caracteres tambien reventaba
    # -el modelo se quedaba sin num_predict a mitad del JSON- y ademas no
    # hace falta, porque lo que se va a buscar es solo el principio.
    tema_busqueda = _consulta_util(tema)
    if tema_busqueda != tema:
        inv.avisos.append(
            "el tema traia %d caracteres; se busco con los primeros %d"
            % (len(tema), len(tema_busqueda)))
        logger.warning(
            "investigacion: tema de %d caracteres recortado a %d para "
            "poder buscar", len(tema), len(tema_busqueda))
    # Un tema corto se traduce; un texto se destila en terminos. Mandar
    # una oracion traducida a un buscador academico devuelve cero, y ese
    # cero se lee como 'el tema no tiene fuentes', que es falso.
    if len(tema.split()) > PALABRAS_PARA_TEXTO:
        consulta = _consulta_util(terminos_de(tema))
        inv.avisos.append(
            "lo que mandaste es un texto, no un tema: se busco con los terminos %r" % consulta)
    else:
        consulta = _consulta_util(a_ingles(tema_busqueda))
    if consulta != tema_busqueda:
        inv.avisos.append("se busco como %r" % consulta)
    nombres = fuentes or list(FUENTES)
    for nombre in nombres:
        buscar = FUENTES.get(nombre)
        if not buscar:
            inv.avisos.append("fuente desconocida: %s" % nombre)
            continue
        docs = buscar(consulta)
        if not docs:
            inv.avisos.append("%s no devolvio material" % nombre)
            continue
        for d in docs:
            inv.documentos.append({"titulo": d.titulo, "url": d.url,
                                   "institucion": d.institucion,
                                   "fecha": d.fecha})
            nuevos = hechos_de(d, tema)
            if not nuevos:
                # El documento existe y tiene texto, pero no es DEL TEMA.
                # La proporcion de descartados es la senal de que la
                # busqueda trajo material de otro campo.
                fuera_de_tema += 1
            inv.hechos.extend(nuevos)
        logger.info("investigacion: %s -> %d documentos", nombre, len(docs))

    inv.hechos = _sin_repetidos(inv.hechos)
    logger.info("investigacion: %d hechos de %d documentos "
                "(%d docs fuera de tema)", len(inv.hechos),
                len(inv.documentos), fuera_de_tema)
    # Contar hechos no mide si los hechos cuentan LA MISMA HISTORIA. Ese
    # fue el diagnostico del review de fiabilidad, y el 2026-08-29 se
    # cobro: "salsa Valentina" se busco en arXiv, arXiv solo tiene fisica,
    # y salieron 71 hechos impecablemente concretos sobre desintegracion
    # de mesones. El guion se titulo "La Salsa de Valentina y el Misterio
    # del B^0_s a Muones" y no menciona la salsa ni una vez. La compuerta
    # de cantidad lo dejo pasar porque 71 > 8.
    total_docs = len(inv.documentos)
    if total_docs and fuera_de_tema >= total_docs * 0.6:
        inv.avisos.append(
            "%d de %d documentos no hablaban de %r y se descartaron: la "
            "busqueda trajo material de otro campo. Lo que quede puede no "
            "alcanzar para un documental del tema."
            % (fuera_de_tema, total_docs, tema))
        logger.warning("investigacion: %d/%d documentos fuera de tema",
                       fuera_de_tema, total_docs)
    if len(inv.hechos) < MINIMO_HECHOS:
        # Se avisa y se sigue. Bloquear aqui impedia justo los temas
        # concretos, que son los que tienen historia.
        inv.avisos.append(
            "solo %d hechos de fuente sobre %r; el guion va a apoyarse mas en "
            "creacion propia que en material citable"
            % (len(inv.hechos), tema))
        logger.warning("investigacion: material escaso (%d hechos)",
                       len(inv.hechos))
    if not inv.hechos:
        raise SinFuentes(
            "ninguna fuente devolvio material sobre %r. Se puede escribir el "
            "guion igual, pero sera creacion propia sin nada que citar." % tema)
    return inv


def _sin_repetidos(hechos: list[Hecho]) -> list[Hecho]:
    """Varios trabajos sobre un mismo tema repiten los datos principales."""
    vistos: set[str] = set()
    salida: list[Hecho] = []
    for h in hechos:
        clave = " ".join(re.findall(r"\w+", h.texto.lower())[:12])
        if clave in vistos:
            continue
        vistos.add(clave)
        salida.append(h)
    return salida
