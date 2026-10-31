# -*- coding: utf-8 -*-
"""Texto para LOCUCION: copia del texto editorial con las cifras escritas en palabras.

Problema que resuelve (medido 2026-10-13, ver
docs/operations/MOTOR_DOCUMENTAL_PERIODISTICO/DIAGNOSTICO_NUMEROS_TTS_20261013/INFORME.md):
el TTS recibia «2026» y lo leia como «1906/1996»; «19» se oia «9».

Contrato:
- El texto editorial y los subtitulos NO se tocan; esta funcion devuelve una copia solo para la voz.
- Convierte fechas, anios, horas, porcentajes, decimales, montos, miles, ordinales (1.º) y
  cantidades (con «un/una» ante sustantivo).
- NO convierte: URL, correos, folios/claves/oficios/expedientes, tokens mixtos letra+digito
  (COVID-19, NOM-045-SSA2, H1N1) ni telefonos.
- Convencion mexicana, tolerante: «2.5» y «2,5» son decimales; «1,165» y «1 165» son miles
  (coma o espacio seguidos de exactamente 3 digitos).
"""
from __future__ import annotations

import re

from num2words import num2words

MESES = (r"(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre"
         r"|noviembre|diciembre)")
CLAVE = (r"(?:folio|clave|oficio|expediente|c[oó]digo|matr[ií]cula|NSS|CURP|RFC|cuenta"
         r"|tel[eé]fono|tel\.|ext\.|extensi[oó]n|lote|serie)")

# palabras tras la cifra que NO son el sustantivo contado: «número 1 del», «el 1 y el 2»
NO_SUST = {"del", "de", "la", "el", "los", "las", "y", "o", "u", "e", "en", "por", "a", "al", "que",
           "con", "sin", "para", "se", "es", "fue", "son", "no", "ni", "lo", "le", "su", "sus",
           "como", "más", "menos", "entre", "hasta", "desde"}
# masculinos terminados en -a (día, problema...) y femeninos que no terminan en -a
MASC_EN_A = {"día", "días", "dia", "dias", "problema", "problemas", "tema", "temas", "sistema",
             "sistemas", "programa", "programas", "mapa", "mapas", "idioma", "idiomas", "clima",
             "planeta", "planetas", "dilema", "dilemas", "esquema", "esquemas", "síntoma",
             "síntomas", "diagrama", "diagramas", "poema", "poemas", "drama", "dramas", "pijama",
             "cometa", "cometas", "telegrama", "telegramas", "guardia", "guardias"}
FEM_OTROS = {"muerte", "muertes", "vez", "veces", "madre", "madres", "fuente", "fuentes", "mujer",
             "mujeres", "noche", "noches", "tarde", "tardes", "clave", "claves", "red", "redes",
             "ley", "leyes", "parte", "partes", "imagen", "imágenes", "mano", "manos", "foto",
             "fotos", "moto", "motos", "radio", "sede", "sedes", "llave", "llaves"}
ORD = {1: "primer", 2: "segund", 3: "tercer", 4: "cuart", 5: "quint", 6: "sext", 7: "séptim",
       8: "octav", 9: "noven", 10: "décim"}

PROTEGIDO = re.compile(
    r"https?://\S+|www\.\S+|\S+@\S+\.\w+"
    r"|\b" + CLAVE + r"\.?\s*(?:n[uú]m(?:ero)?\.?|no\.)?\s*[:#]?\s*[\w/.-]*\d[\w/.-]*"
    r"|\b(?=[\w-]*[A-Za-zÁÉÍÓÚÑáéíóúñ])(?=[\w-]*\d)\w+(?:-\w+)+"
    r"|\b(?=\w*[A-Za-zÁÉÍÓÚÑáéíóúñ])(?=\w*\d)\w+\b"
    r"|\b\d{2,3}[ -]\d{3,4}[ -]\d{4}\b",
    re.I)


def _card(n: int) -> str:
    return num2words(n, lang="es")


def _es_fem(sig: str) -> bool:
    s = sig.lower()
    if s in MASC_EN_A:
        return False
    if s in FEM_OTROS:
        return True
    return re.search(r"(?:a|as|ción|ciones|sión|siones|dad|dades|tud|tudes)$", s) is not None


def _cant(n: int, sig: str) -> str:
    """Cantidad ante sustantivo: 1->un/una, 21->veintiún/veintiuna, 31->treinta y un/una."""
    w = _card(n)
    if not (w.endswith("uno") and sig) or sig.lower() in NO_SUST:
        return w
    if sig.lower() == "mil":
        fem = False
    else:
        fem = _es_fem(sig)
    if fem:
        return w[:-3] + "una"
    return "veintiún" if w == "veintiuno" else w[:-3] + "un"


def _num(ent: str, dec: str | None) -> str:
    e = _card(int(ent))
    if not dec:
        return e
    d = " ".join(_card(int(c)) for c in dec) if dec.startswith("0") else _card(int(dec))
    return e + " punto " + d


def _hora(h: int, m: int) -> str:
    base = "una" if h in (1, 13) else ("doce" if h == 0 else _card(h if h <= 12 else h - 12))
    if m == 0:
        return base
    if m == 30:
        return base + " y media"
    if m == 15:
        return base + " y cuarto"
    return base + " con " + _card(m)


def preparar_para_locucion(texto: str) -> str:
    """Devuelve la copia del texto lista para el TTS (cifras en palabras)."""
    if not texto or not re.search(r"\d", texto):
        return texto
    guard: dict[str, str] = {}

    def _g(m):
        k = "" + chr(0xE100 + len(guard)) + ""  # sin digitos
        guard[k] = m.group(0)
        return k
    t = PROTEGIDO.sub(_g, texto)

    # fechas: 19 de septiembre de 2026 / 1 de octubre
    def _fecha(m):
        d = int(m.group(1))
        s = ("primero" if d == 1 else _card(d)) + " de " + m.group(2)
        if m.group(3):
            s += " de " + _card(int(m.group(3)))
        return s
    t = re.sub(r"\b(\d{1,2}) de (" + MESES + r")(?: del? (\d{4}))?\b", _fecha, t, flags=re.I)

    # horas: con «horas» como en noticiero (diez treinta horas); sin sufijo, coloquial
    def _h(m):
        h, mi = int(m.group(1)), int(m.group(2))
        if m.group(3):
            return _card(h) + ("" if mi == 0 else " " + _card(mi)) + " horas"
        return _hora(h, mi)
    t = re.sub(r"\b([01]?\d|2[0-3]):([0-5]\d)(\s?(?:hrs?\.?|horas|h)\b)?", _h, t)

    # montos: $1,500.50
    def _monto(m):
        ent = int(m.group(1).replace(",", ""))
        s = _cant(ent, "pesos") + " pesos"
        if m.group(2) and int(m.group(2)):
            s += " con " + _card(int(m.group(2))) + " centavos"
        return s
    t = re.sub(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?(?:\s?(?:pesos|MXN|M\.N\.))?", _monto, t)

    # porcentajes: 40 %, 12.5%, 2,9%
    t = re.sub(r"\b(\d{1,3}(?:,\d{3})+|\d+)(?:[.,](\d{1,2}))?\s?%",
               lambda m: _num(m.group(1).replace(",", ""), m.group(2)) + " por ciento", t)

    # ordinales: solo º ª ° pegados a la cifra (1.º, 2.ª, 3°)
    t = re.sub(r"\b(10|[1-9])\.?([ºª°])",
               lambda m: ORD[int(m.group(1))] + ("a" if m.group(2) == "ª" else "o"), t)

    # decimales: 2.5 / 2,5 (coma seguida de 1-2 digitos; con 3 digitos es separador de miles)
    t = re.sub(r"\b(\d+)\.(\d+)\b", lambda m: _num(m.group(1), m.group(2)), t)
    t = re.sub(r"\b(\d+),(\d{1,2})(?!\d)", lambda m: _num(m.group(1), m.group(2)), t)

    # anios sueltos: en 2026, desde 2019
    t = re.sub(r"\b(1[5-9]\d\d|20\d\d)\b", lambda m: _card(int(m.group(1))), t)

    # cantidades (con miles 1,165 / 12 000), con apocope ante el sustantivo
    t = re.sub(r"\b(\d{1,3}(?:[, ]\d{3})+|\d+)\b(?=(\s+([A-Za-zÁÉÍÓÚÑáéíóúñü]+))?)",
               lambda m: _cant(int(re.sub(r"[ ,]", "", m.group(1))), m.group(3) or ""), t)

    for k, v in guard.items():
        t = t.replace(k, v)
    return t


# ── LÉXICO DE PRONUNCIACIÓN (el operador, 15-oct-2026: «sigue diciendo pitón, no páiton») ──────────
# La voz en español lee las palabras en inglés como si fueran españolas. Aquí se escribe CÓMO DEBE
# SONAR; el texto en pantalla (subtítulos, tarjetas) no pasa por aquí y sigue diciendo «Python».
LEXICO = {
    "python": "Páiton",
    # Expediente 02 (Knight Capital, 15-oct-2026)
    "power peg": "Páuer Peg",
    "knight capital": "Náit Cápital",
    "knight": "Náit",
    "sec": "ese e ce",
    "hackeo": "jakeo",  # Knight b1: la voz leyó «aqueo» (h muda), el operador 15-oct
    # Expediente 03 (GitLab, 15-oct-2026)
    "pg_dump": "pe ge dump",
    "gitlab": "Guit Lab",
    "youtube": "Yutub",
    # Expediente 04 (CrowdStrike, 15-oct-2026)
    "crowdstrike": "Cráud Stráik",
    "microsoft": "Máicrosoft",
    "windows": "Uíndous",
    # Promo de invitación (17-oct-2026)
    "claude": "Clod",
    "reels": "rils",  # whisper oía «Rebels» (17-oct)
    "reel": "ril",
}


def aplicar_lexico(texto: str) -> str:
    """Sustituye palabras completas del LEXICO (sin distinguir mayúsculas) antes del TTS."""
    for palabra, suena in LEXICO.items():
        texto = re.sub(r"(?<![\wÁÉÍÓÚÑáéíóúñ])%s(?![\wÁÉÍÓÚÑáéíóúñ])" % re.escape(palabra), suena, texto,
                       flags=re.I)
    return texto


# ── LÉXICO DE CÓDIGO PARA REELS DE PYTHON (17-oct-2026) ──────────────────────────────────────────
# Kokoro (lang «e») lee los nombres de Python con reglas del español: range→«ránje», get→«jet»,
# while→«uíle». Comprobado con los fonemas que recibe el modelo, no solo con whisper. Se aplica SOLO a
# la voz de los reels de código (scripts/render_reels_tiktok.py); el texto en pantalla no cambia.
# Valor None = término revisado que ya suena aceptable tal cual (lo exige scripts/validar_reels_tiktok.py).
LEXICO_CODIGO = {
    "range": "réinch", "while": "uáil", "get": "guet", "endswith": "ends uíz", "startswith": "starts uíz",
    "replace": "ripléis", "upper": "áper", "lower": "lóuer", "title": "táitol", "type": "táip",
    "math": "maz", "sorted": "sórted",
    "pi": None, "for": None, "end": None, "divmod": None, "if": None, "else": None, "int": None,
    "split": None, "len": None, "strip": None, "abs": None, "sum": None, "string": None, "round": None,
    "gcd": None, "append": None, "sort": None, "f": None, "n": None, "a": None, "b": None, "c": None,
    "texto": None, "suma": None, "mayor": None, "edad": None, "archivo": None, "es": None,
}


def aplicar_lexico_codigo(texto: str) -> str:
    """Texto que se le da a la voz de un reel de código: LEXICO general («Páiton») + LEXICO_CODIGO."""
    texto = aplicar_lexico(texto)
    for palabra, suena in LEXICO_CODIGO.items():
        if suena:
            texto = re.sub(r"(?<![\wÁÉÍÓÚÑáéíóúñ])%s(?![\wÁÉÍÓÚÑáéíóúñ])" % re.escape(palabra), suena, texto,
                           flags=re.I)
    return texto
