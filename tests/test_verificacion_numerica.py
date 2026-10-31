# -*- coding: utf-8 -*-
"""Etapa 2: verificacion determinista de numeros de la narracion (sin modelo).

Los casos e87/e92 son narraciones REALES del banco (`_banco.json`), copiadas aqui para que la
prueba no dependa de una ruta congelada que el bot reescribe.
"""
from __future__ import annotations

import pytest

from content_factory import verificacion_numerica as vn

E87_CODE = "x = 0\nprint('or:', x or 5)\nprint('if:', x if x is not None else 5)\nprint('dict.get:', {0: 5}.get(x, 5))"
E87_OUT = "or: 5\nif: 0\ndict.get: 5"
E92_OUT = "[3.937007874015748, 5.905511811023622, 7.874015748031496]"
E92_CODE = "potencia = [500, 750, 1000]\nvoltaje = 127\nampere = [p / voltaje for p in potencia]\nprint(ampere)"


def test_las_copias_son_identicas_al_banco():
    """Si el banco cambia o la copia se equivoca, esta prueba lo dice (el 21-oct la copia de e87
    estaba mal escrita: inventaba otra llave)."""
    import json
    from pathlib import Path
    banco = Path(__file__).resolve().parent.parent / "docs/operations/evidencia/poc_terminal_shorts_20261005/_banco.json"
    b = {e["id"]: e for e in json.loads(banco.read_text(encoding="utf-8"))}
    assert (b["e87"]["code"], b["e87"]["output"]) == (E87_CODE, E87_OUT)
    assert (b["e92"]["code"], b["e92"]["output"]) == (E92_CODE, E92_OUT)


@pytest.mark.parametrize("texto, esperado", [
    ("ciento veintisiete", 127), ("setecientos cincuenta", 750), ("treinta y cinco", 35),
    ("dos mil veintiséis", 2026), ("un millón", 1_000_000), ("tres punto nueve", 3.9),
    ("dieciséis", 16), ("127", 127), ("3.94", 3.94),
])
def test_lee_numeros_en_palabras_y_digitos(texto, esperado):
    assert [n["valor"] for n in vn.numeros_narrados(texto)] == [esperado]


def test_una_lista_no_se_funde_en_un_solo_numero():
    nums = vn.numeros_narrados("uno, tres, cinco, seis, siete y nueve; quinientos, setecientos cincuenta y mil")
    assert [n["valor"] for n in nums] == [1, 3, 5, 6, 7, 9, 500, 750, 1000]


def test_articulo_un_no_es_numero():
    assert vn.numeros_narrados("con un solo cambio la huella es otra") == []


def test_e87_descartada_todos_dan_cinco_se_bloquea():
    r = vn.verificar_numeros(E87_CODE, E87_OUT, "Todos dan 5 porque x es 0.")
    assert r["veredicto"] == "BLOQUEO"


def test_e87_corregida_pasa():
    nar = ("Con x igual a cero, la primera línea da cinco, porque or trata el cero como falso. "
           "La segunda conserva el cero. La tercera da cinco, porque esa llave sí existe.")
    assert vn.verificar_numeros(E87_CODE, E87_OUT, nar)["veredicto"] == "OK"


def test_redondeo_hablado_con_unos_pasa():
    nar = "Con quinientos, setecientos cincuenta y mil watts salen unos cuatro, seis y ocho amperes."
    assert vn.verificar_numeros(E92_CODE, E92_OUT, nar)["veredicto"] == "OK"


def test_resultado_inventado_se_bloquea():
    """Mutante: la misma narracion con un resultado que la salida no tiene."""
    nar = "Con quinientos, setecientos cincuenta y mil watts salen unos cuatro, seis y doce amperes."
    r = vn.verificar_numeros(E92_CODE, E92_OUT, nar)
    assert r["veredicto"] == "BLOQUEO"
    assert "doce" in r["hallazgos"][0]["numero"]


def test_numero_sin_rastro_es_aviso_no_bloqueo():
    """e92 corregida: el 15 del fusible no esta en codigo ni salida. Lo decide una persona."""
    nar = "Salen unos cuatro, seis y ocho amperes: por debajo de un fusible de quince."
    r = vn.verificar_numeros(E92_CODE, E92_OUT, nar)
    assert r["veredicto"] == "AVISO"


def test_numero_del_codigo_descrito_como_comportamiento_no_bloquea():
    """e32: "get devuelve cero si no existe" habla de la funcion, no de la salida."""
    code = 'letras = "banana"\nfreq = {}\nfor c in letras:\n    freq[c] = freq.get(c, 0) + 1\nprint(freq)'  # e32 real
    r = vn.verificar_numeros(code, "{'b': 1, 'a': 3, 'n': 2}", "El método get devuelve cero si la letra no existe.")
    assert r["veredicto"] != "BLOQUEO"


def test_digitos_de_un_hash_no_cuentan_como_salida():
    out = "Hash original: 18885f27b5af9012df19e496460f9294d5ab76128824c6f993787004f6d9a7db"
    r = vn.verificar_numeros("import hashlib", out + "\n7", "El resultado es 18885.")
    assert r["veredicto"] == "BLOQUEO"


def test_sin_numeros_no_opina():
    r = vn.verificar_numeros("lista.sort()", "[1, 2]", "Este código ordena una lista.")
    assert r == {"veredicto": "OK", "hallazgos": [], "numeros": 0}


# Revision de operador (21-oct): con print(2) y salida 2, «La salida es cinco» y «El programa
# calcula cinco» solo daban AVISO. Toda frase que atribuye un resultado falso debe BLOQUEAR.
@pytest.mark.parametrize("frase", [
    "El resultado da cinco", "El resultado es cinco", "La salida es cinco", "El programa calcula cinco",
    "El código produce cinco", "Arroja cinco", "Retorna cinco", "Genera un cinco", "Imprime cinco",
    "Aparece un cinco en pantalla", "Muestra el número cinco", "El total es cinco",
    "La respuesta es cinco", "Vemos cinco", "Obtienes cinco", "Termina con cinco", "Nos devuelve cinco",
])
def test_resultado_falso_atribuido_bloquea(frase):
    assert vn.verificar_numeros("print(2)", "2", frase)["veredicto"] == "BLOQUEO"


@pytest.mark.parametrize("frase", ["La salida es dos", "El programa calcula dos", "Imprime 2"])
def test_resultado_verdadero_atribuido_no_bloquea(frase):
    assert vn.verificar_numeros("print(2)", "2", frase)["veredicto"] == "OK"


def test_limite_conocido_valor_final_queda_en_aviso_no_en_ok():
    """«El valor final es cinco» no se reconoce como resultado: no bloquea, pero tampoco pasa
    como OK. Limite declarado en el informe de la Etapa 2."""
    assert vn.verificar_numeros("print(2)", "2", "El valor final es cinco")["veredicto"] == "AVISO"
