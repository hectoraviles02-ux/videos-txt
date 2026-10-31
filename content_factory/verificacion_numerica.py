# -*- coding: utf-8 -*-
"""Verificacion DETERMINISTA de los numeros que dice la narracion de un reel de codigo.

Etapa 2 (21-oct-2026). No usa ningun modelo: compara cada numero que dice la voz (en digitos o
en palabras: "ciento veintisiete", "tres punto nueve") contra los numeros del CODIGO y de la
SALIDA REAL. Dos niveles:

- BLOQUEO: divergencia comprobada. La narracion atribuye un RESULTADO ("da cinco", "salen
  unos cuatro", "todos dan 5") y ese numero no esta en la salida real, ni redondeado.
- AVISO:   un numero que no se puede rastrear ni al codigo ni a la salida (p. ej. "un fusible
  de quince" cuando el 15 no aparece). No prueba error, pero el CLAUDE.md exige que todo dato
  sea rastreable: lo decide una persona. Los numeros 0 a 3 sin verbo de resultado no se avisan
  (en las 97 narraciones del banco eran cantidades o pronombres: "dos funciones", "uno nuevo").

Limite declarado: solo ve numeros. Una afirmacion falsa sin numeros ("ordena la lista") se le
escapa; para eso esta el juez local y, sobre todo, la revision humana.
"""
from __future__ import annotations

import re
import unicodedata

UNIDADES = {"cero": 0, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
            "siete": 7, "ocho": 8, "nueve": 9}
ESPECIALES = {"diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
              "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
              "veintiuno": 21, "veintiun": 21, "veintidos": 22, "veintitres": 23,
              "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26, "veintisiete": 27,
              "veintiocho": 28, "veintinueve": 29}
DECENAS = {"treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70,
           "ochenta": 80, "noventa": 90}
CENTENAS = {"cien": 100, "ciento": 100, "doscientos": 200, "doscientas": 200, "trescientos": 300,
            "trescientas": 300, "cuatrocientos": 400, "cuatrocientas": 400, "quinientos": 500,
            "quinientas": 500, "seiscientos": 600, "seiscientas": 600, "setecientos": 700,
            "setecientas": 700, "ochocientos": 800, "ochocientas": 800, "novecientos": 900,
            "novecientas": 900}
MULTIPLICADORES = {"mil": 1000, "millon": 1_000_000, "millones": 1_000_000}

# Verbos y sustantivos con los que la voz atribuye un resultado a la ejecucion. Ampliada el
# 21-oct tras la revision de el operador («La salida es cinco», «El programa calcula cinco» solo
# daban AVISO); cada palabra nueva se midio contra las 101 narraciones reales del banco.
VERBOS_RESULTADO = {"da", "dan", "dara", "daria", "devuelve", "devuelven", "regresa", "regresan",
                    "retorna", "retornan", "imprime", "imprimen", "muestra", "muestran", "sale",
                    "salen", "resulta", "resultan", "obtenemos", "obtiene", "obtienen", "obtienes",
                    "vale", "valen", "queda", "quedan", "calcula", "calculan", "produce", "producen",
                    "arroja", "arrojan", "genera", "generan", "aparece", "aparecen", "vemos", "ves",
                    "termina", "terminan", "entrega", "entregan",
                    "resultado", "resultados", "salida", "respuesta", "total"}
APROXIMACION = {"unos", "unas", "aproximadamente", "casi", "cerca", "alrededor", "aprox"}
TOTALIZADORES = {"todos", "todas", "ambos", "ambas", "siempre"}
# Palabras que pueden ir entre el verbo y el numero, o entre numeros de una lista.
PUENTE = {"y", ",", "el", "la", "los", "las", "un", "una", "en", "a", "de", "igual", "es",
          "son", "como", "mas", "o", "menos", "exactamente", "justo", "numero", "valor", "final",
          "con", "nos", "te", "se", "pantalla", "sera", "fue"} | APROXIMACION
FIN_DE_FRASE = {".", ";", ":", "!", "?"}


def _sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower())
                   if unicodedata.category(c) != "Mn")


def _tokens(texto: str) -> list[str]:
    return re.findall(r"\d+(?:[.,]\d+)?|[a-zñ]+|[,;.:!?]", _sin_acentos(texto))


def _es_palabra_numero(t: str) -> bool:
    return t in UNIDADES or t in ESPECIALES or t in DECENAS or t in CENTENAS or t in MULTIPLICADORES


def numeros_narrados(texto: str) -> list[dict]:
    """Cada numero de la narracion: valor, posicion (indice de token) y si fue escrito en palabras."""
    toks = _tokens(texto)
    salida, i = [], 0
    while i < len(toks):
        t = toks[i]
        if re.fullmatch(r"\d+(?:[.,]\d+)?", t):
            salida.append({"valor": float(t.replace(",", ".")), "pos": i, "texto": t})
            i += 1
            continue
        siguiente = toks[i + 1] if i + 1 < len(toks) else ""
        # "un"/"una" son articulo, salvo "un mil" / "un millon".
        if not _es_palabra_numero(t) and not (t in ("un", "una") and siguiente in MULTIPLICADORES):
            i += 1
            continue
        inicio, total, actual, previo = i, 0, 0, None
        while i < len(toks):
            t = toks[i]
            if t in ("un", "una") and i + 1 < len(toks) and toks[i + 1] in MULTIPLICADORES:
                actual += 1
            elif t in UNIDADES:
                if previo in ("unidad", "especial"):
                    break  # "uno, tres" o "dos tres": son numeros distintos
                actual += UNIDADES[t]
                previo = "unidad"
            elif t in ESPECIALES:
                if previo in ("unidad", "especial", "decena"):
                    break
                actual += ESPECIALES[t]
                previo = "especial"
            elif t in DECENAS:
                if previo in ("unidad", "especial", "decena"):
                    break
                actual += DECENAS[t]
                previo = "decena"
            elif t in CENTENAS:
                if previo is not None and previo != "mil":
                    break
                actual += CENTENAS[t]
                previo = "centena"
            elif t in MULTIPLICADORES:
                total += (actual or 1) * MULTIPLICADORES[t]
                actual, previo = 0, "mil"
            elif t == "y" and previo == "decena" and i + 1 < len(toks) and toks[i + 1] in UNIDADES:
                pass  # "treinta y cinco"; el "y" de una lista ("seis y ocho") corta el numero
            else:
                break
            i += 1
        valor = float(total + actual)
        # Decimales hablados: "tres punto nueve", "tres coma cinco"
        if i + 1 < len(toks) and toks[i] in ("punto", "coma") and toks[i + 1] in UNIDADES:
            digitos = ""
            i += 1
            while i < len(toks) and toks[i] in UNIDADES:
                digitos += str(UNIDADES[toks[i]])
                i += 1
            valor = float("%d.%s" % (int(valor), digitos))
        salida.append({"valor": valor, "pos": inicio, "texto": " ".join(toks[inicio:i])})
    return salida


def numeros_de_salida(texto: str) -> list[float]:
    """Numeros sueltos de la salida real. Los pegados a letras (hashes, ids) no cuentan."""
    return [float(m) for m in re.findall(r"(?<![A-Za-z0-9_.])-?\d+(?:\.\d+)?(?![A-Za-z0-9_])", texto)]


def numeros_de_codigo(texto: str) -> list[float]:
    """Numeros del codigo, incluidos los de un nombre (`sha256` -> 256), que la voz puede leer."""
    return [float(m) for m in re.findall(r"\d+(?:\.\d+)?", texto)]


def _coincide(n: float, valores: list[float], aproximado: bool) -> bool:
    for v in valores:
        if abs(n - v) < 1e-9:
            return True
        decimales = len(repr(n).split(".")[1].rstrip("0")) if n != int(n) else 0
        if decimales and round(v, decimales) == n:
            return True  # "3.94" por 3.937
        if not decimales and v != int(v) and abs(v - n) <= 0.5:
            return True  # redondeo hablado: "cuatro" por 3.93
        if aproximado and v and abs(v - n) / abs(v) <= 0.05:
            return True
    return False


def _contexto(toks: list[str], pos: int) -> dict:
    """Mira hacia atras desde un numero: ¿lo introduce un verbo de resultado? ¿hay 'unos'? ¿'todos'?"""
    ctx = {"resultado": False, "aproximado": False, "totalizador": False}
    j = pos - 1
    while j >= 0 and toks[j] not in FIN_DE_FRASE:
        t = toks[j]
        if t in APROXIMACION:
            ctx["aproximado"] = True
        if t in VERBOS_RESULTADO:
            ctx["resultado"] = True
            k = j - 1
            while k >= 0 and k >= j - 3 and toks[k] not in FIN_DE_FRASE:
                if toks[k] in TOTALIZADORES:
                    ctx["totalizador"] = True
                k -= 1
            return ctx
        if not (t in PUENTE or _es_palabra_numero(t) or re.fullmatch(r"\d+(?:[.,]\d+)?", t)
                or t in ("punto", "coma")):
            return {"resultado": False, "aproximado": ctx["aproximado"], "totalizador": False}
        j -= 1
    return {"resultado": False, "aproximado": ctx["aproximado"], "totalizador": False}


def verificar_numeros(code: str, output: str, narracion: str) -> dict:
    """Devuelve {"veredicto": OK | AVISO | BLOQUEO, "hallazgos": [...], "numeros": n}."""
    toks = _tokens(narracion)
    en_salida = numeros_de_salida(output)
    en_codigo = numeros_de_codigo(code)
    hallazgos = []
    narrados = numeros_narrados(narracion)
    for num in narrados:
        n = num["valor"]
        ctx = _contexto(toks, num["pos"])
        if ctx["resultado"] and en_salida:
            if not _coincide(n, en_salida, ctx["aproximado"]):
                # Si el numero esta en el CODIGO, la voz puede estar describiendo lo que hace una
                # funcion ("get devuelve cero si no existe"), no la salida: no esta comprobado.
                nivel = "AVISO" if _coincide(n, en_codigo, ctx["aproximado"]) else "BLOQUEO"
                hallazgos.append({"nivel": nivel, "numero": num["texto"],
                                  "motivo": "la voz dice que el resultado es %s y la salida real no lo muestra"
                                            % num["texto"]})
                continue
            if ctx["totalizador"] and any(not _coincide(n, [v], ctx["aproximado"]) for v in en_salida):
                hallazgos.append({"nivel": "BLOQUEO", "numero": num["texto"],
                                  "motivo": "la voz dice que todos dan %s y la salida real tiene valores distintos"
                                            % num["texto"]})
                continue
        if n in (0, 1, 2, 3) and n == int(n):
            continue  # "dos funciones", "uno nuevo", "el cero": cantidades y pronombres, no datos
        if not _coincide(n, en_salida + en_codigo, ctx["aproximado"]):
            hallazgos.append({"nivel": "AVISO", "numero": num["texto"],
                              "motivo": "el numero %s no aparece ni en el codigo ni en la salida real"
                                        % num["texto"]})
    if any(h["nivel"] == "BLOQUEO" for h in hallazgos):
        veredicto = "BLOQUEO"
    elif hallazgos:
        veredicto = "AVISO"
    else:
        veredicto = "OK"
    return {"veredicto": veredicto, "hallazgos": hallazgos, "numeros": len(narrados)}
