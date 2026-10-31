# -*- coding: utf-8 -*-
"""Comprueba que la voz DIJO el texto del guion: palabras, números y unidades. Sin modelos: compara el texto del guion
con lo que oyó whisper.

Antes (hasta 22-oct-2026) gen_voz.py solo comparaba CUÁNTAS palabras había (±2): «el gato duerme bajo una mesa verde»
pasaba por «la nave llegó a Marte sin problemas» (prueba de Codex), y «57» contra «cincuenta y siete» se rechazaba.
Ahora, para aceptar una frase se exigen las tres cosas:
  1. palabras: parecido ≥ UMBRAL entre las letras de las palabras sin números (difflib, sin acentos ni espacios),
     y cada nombre propio del guion («Marte») se oye;
  2. números: la MISMA secuencia de valores. «57» = «cincuenta y siete», «4.45» = «cuatro punto cuatro cinco»
     (lector de content_factory/verificacion_numerica.py, Etapa 2);
  3. unidades: la MISMA secuencia, con equivalencias explícitas (km = kilómetros), sin parecido aproximado;
  4. palabra por palabra y en su posición: un tramo distinto que toque una negación o una unidad se rechaza;
     todos los tramos distintos se devuelven en «diferencias» para revisión.
Uso como módulo: verificar(texto_guion, texto_oido) -> dict con "ok" y "motivo".
"""
from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from content_factory.verificacion_numerica import _es_palabra_numero, _sin_acentos, numeros_narrados  # noqa: E402

UMBRAL = 0.80  # parecido mínimo de letras; calibrado con las voces reales (tests/test_expediente3d_compuertas.py)
PARECIDO_NOMBRE = 0.7  # whisper escribe mal algunos NOMBRES propios: basta una palabra parecida (no aplica a unidades)
# Una negación cambia el sentido aunque el parecido sea alto («no llegó» / «llegó», revisión de Codex 22-oct): se cuentan exactas
NEGACIONES = {"no", "nunca", "jamas", "ni", "nadie", "ninguno", "ninguna", "ningun", "tampoco", "nada"}
# «ciento» y «millones» no van como unidades: son parte del número y ya los cubre la comparación de valores
UNIDADES = {"kilometros", "metros", "milimetros", "segundos", "milisegundos", "minutos", "horas", "dias", "meses",
            "anos", "bits", "bytes", "gigabits", "terabytes", "megabytes", "newtons", "libras", "dolares",
            "pesos", "grados", "litros", "kilos", "toneladas", "veces"}
ABREVIATURAS = {"km": "kilometros", "m": "metros", "ms": "milisegundos", "s": "segundos", "seg": "segundos",
                "min": "minutos", "h": "horas", "hrs": "horas", "kg": "kilos", "tb": "terabytes",
                "gb": "gigabytes", "mb": "megabytes", "n": "newtons", "lb": "libras", "lbs": "libras",
                # singulares y errores de whisper medidos: equivalencias EXPLÍCITAS, sin parecido aproximado
                # («metros» nunca debe valer por «kilómetros», revisión de Codex 22-oct)
                "kilometro": "kilometros", "metro": "metros", "milimetro": "milimetros", "segundo": "segundos",
                "milisegundo": "milisegundos", "minuto": "minutos", "hora": "horas", "dia": "dias", "mes": "meses",
                "ano": "anos", "bit": "bits", "byte": "bytes", "gigabit": "gigabits", "terabyte": "terabytes",
                "terabites": "terabytes", "megabyte": "megabytes", "newton": "newtons", "libra": "libras",
                "dolar": "dolares", "peso": "pesos", "grado": "grados", "litro": "litros", "kilo": "kilos",
                "tonelada": "toneladas", "vez": "veces"}


def _preparar(texto: str) -> str:
    """Iguala la forma de escribir: miles con punto («2.000» → 2000), «%» y abreviaturas de unidades."""
    t = re.sub(r"(\d) ([.,]\d)", r"\1\2", texto)  # las palabras de whisper parten «8.5» en «8 .5»
    t = re.sub(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3})+)(?![\d])", lambda m: re.sub(r"[.,]", "", m.group(1)), t)
    t = re.sub(r"\b[Pp]rimero de\b", "uno de", t)  # «primero de agosto» = «1 de agosto»
    t = re.sub(r"(?<![\d.,])\b1\s+(?!de\b)(?=[a-záéíóúñ])", "un ", t)  # «1 segundo» = «un segundo» (artículo)
    t = t.replace("%", " por ciento")
    # «5 millones» que whisper escribe con dígito: el lector necesita la palabra para multiplicar
    t = re.sub(r"(?<![\d.,])(\d+)\s+(millones|millon|millón|mil)\b", lambda m: "%s %s" % (_palabras_de(int(m.group(1))), m.group(2)), t)
    return t


def _palabras_de(n: int) -> str:
    from num2words import num2words
    return num2words(n, lang="es")


def _tokens(texto: str) -> list[str]:
    return re.findall(r"\d+(?:[.,]\d+)?|[a-zñ%]+", _sin_acentos(texto))


def _sin_numeros(toks: list[str]) -> list[str]:
    return [t for t in toks if not re.fullmatch(r"\d+(?:[.,]\d+)?", t) and not _es_palabra_numero(t)
            and t not in ("punto", "coma")]


def _unidades(toks: list[str]) -> list[str]:
    """Unidades en el orden en que se dicen, ya normalizadas con la tabla explícita. Una abreviatura de UNA letra
    («s», «m», «h», «n») solo cuenta después de una cifra: «la S .S.» de whisper (por «SEC») no son segundos."""
    salida = []
    for i, t in enumerate(toks):
        u = ABREVIATURAS.get(t, t)
        if u in UNIDADES and (len(t) > 1 or (i and re.fullmatch(r"\d+(?:[.,]\d+)?", toks[i - 1]))):
            salida.append(u)
    return salida


def _esta(palabra: str, toks: list[str]) -> bool:
    # «Power Peg» del guion contra «PowerPeg» de whisper: también se busca dentro de las palabras oídas pegadas
    return (palabra in toks or bool(difflib.get_close_matches(palabra, toks, n=1, cutoff=PARECIDO_NOMBRE))
            or (len(palabra) >= 3 and palabra in "".join(toks)))


def _nombres_propios(texto: str) -> list[str]:
    """Palabras con mayúscula que no abren frase («Marte», «Orchestrator»); las siglas (SEC) quedan fuera: whisper las
    deletrea («S .S.») y su significado ya lo cubre el parecido general."""
    salida = []
    for m in re.finditer(r"(?<![.!?¿¡:]\s)(?<!^)\b([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ]*[a-záéíóúñ][A-Za-zÁÉÍÓÚÑáéíóúñ]*)", texto.strip()):
        salida.append(_sin_acentos(m.group(1)))
    return salida


def verificar(guion: str, oido: str) -> dict:
    g, o = _preparar(guion), _preparar(oido)
    tg, to = _tokens(g), _tokens(o)
    pg, po = _sin_numeros(tg), _sin_numeros(to)
    # letra por letra y sin espacios: whisper parte o junta palabras («Power Peg» / «PowerPeg») sin cambiar lo dicho
    parecido = difflib.SequenceMatcher(a="".join(pg), b="".join(po), autojunk=False).ratio() if pg or po else 1.0
    ng = [round(x["valor"], 6) for x in numeros_narrados(g)]
    no = [round(x["valor"], 6) for x in numeros_narrados(o)]
    ug, uo = _unidades(tg), _unidades(to)
    # Palabra por palabra y en su POSICIÓN (revisión de Codex, 22-oct): «no falló … funcionó» contra «falló … no
    # funcionó» tiene las mismas negaciones contadas, pero el «no» cambió de lugar. Cada tramo distinto se lista; si toca
    # una negación o una unidad, se rechaza.
    # (una letra suelta no se traduce a unidad: «la S .S.» de whisper no son segundos; ver _unidades)
    eq = [[ABREVIATURAS.get(t, t) if len(t) > 1 else t for t in x] for x in (pg, po)]
    diferencias, criticas = [], []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=eq[0], b=eq[1], autojunk=False).get_opcodes():
        if op == "equal":
            continue
        a, b = eq[0][i1:i2], eq[1][j1:j2]
        diferencias.append("%s → %s" % (" ".join(a) or "∅", " ".join(b) or "∅"))
        if any(w in NEGACIONES or w in UNIDADES for w in a + b):
            criticas.append(diferencias[-1])
        elif len(b) - len(a) >= 2:  # dos o más palabras que el guion no tiene: balbuceo del TTS («¿qué ella?», 22-oct)
            criticas.append(diferencias[-1])
    faltan_n = [n for n in _nombres_propios(guion) if not _esta(n, to)]
    motivos = []
    if parecido < UMBRAL:
        motivos.append("palabras distintas (parecido %.2f < %.2f)" % (parecido, UMBRAL))
    if ng != no:
        motivos.append("números distintos: guion %s, oído %s" % (ng, no))
    if ug != uo:
        motivos.append("unidades distintas: guion %s, oído %s" % (ug, uo))
    if criticas:
        motivos.append("cambia una negación o unidad, o sobran palabras: %s" % criticas)
    if faltan_n:
        motivos.append("faltan nombres: %s" % faltan_n)
    return {"ok": not motivos, "parecido": round(parecido, 3), "numeros_guion": ng, "numeros_oidos": no,
            "diferencias": diferencias,  # todas, para revisión humana aunque la frase pase
            "motivo": "; ".join(motivos) or "coincide"}


COLA_MAX = 2.0  # s de sonido después de la última palabra; las 74 frases buenas medidas llegan a 1.5 s


def verificar_cola(dur: float, palabras: list[dict]) -> str | None:
    """Sonido que whisper NO reconoce como palabras al final (balbuceo del TTS): el verificador de texto no lo ve.
    Medido 22-oct-2026, Mars 3D b11: 7 palabras terminan en 2.7 s y el audio dura 7.4 s, sin silencio."""
    if not palabras:
        return "no se oyó ninguna palabra"
    cola = dur - palabras[-1]["fin"]
    return "%.1f s de sonido sin palabras al final (máx. %.1f)" % (cola, COLA_MAX) if cola > COLA_MAX else None


if __name__ == "__main__":
    print(verificar(sys.argv[1], sys.argv[2]))
