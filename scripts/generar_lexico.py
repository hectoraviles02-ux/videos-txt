# -*- coding: utf-8 -*-
"""Construye el indice de acentuacion del espanol desde el diccionario RLA-ES.

QUE PROBLEMA RESUELVE

El pipeline escribe texto en espanol en tres lugares distintos y solo dos
estan cubiertos:

  - el guion que produce qwen2.5:14b   -> acentua bien (verificado)
  - la transcripcion de Whisper        -> acentua bien (verificado)
  - texto escrito A MANO en scripts    -> NADIE lo verifica

El tercero es el que falla. El 2026-08-25 se sintetizo con Kokoro la frase
"La impresora del banco central dejo de imprimir": sin tilde, el fonetizador
del espanol acentua DE-jo (presente) en vez de de-JO (pasado), y la voz dice
una palabra distinta de la que el guion queria. El subtitulo salio correcto
porque Whisper transcribio el AUDIO, no el texto de entrada, asi que el error
es invisible en la imagen y solo se oye.

COMO LO DETECTA

Para cada palabra se compara su version sin tildes contra el indice:

  informacion  -> solo existe "informacion" con tilde  -> ERROR SEGURO
  dejo         -> existen "dejo" y "dejo" con tilde    -> AMBIGUO, avisa
  impresora    -> existe tal cual, sin variante        -> correcta

NUNCA corrige solo. "esta/esta", "publico/publico/publico" y "dejo/dejo" son
todas palabras reales del espanol: un reemplazo ciego corrompe el texto. El
validador reporta y decide una persona.
"""
import hashlib
import io
import json
import os
import sys
import unicodedata

# Se corre desde la raiz del repositorio.
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FUENTE = "datos/lexico_es/fuente"
DESTINO = "datos/lexico_es"
FECHA = "2026-08-25"

TILDES = "áéíóúüñÁÉÍÓÚÜÑ"


def sin_tildes(p):
    """Quita tildes pero CONSERVA la enie: 'ano' y 'ano' no son lo mismo."""
    p = p.replace("ñ", "\x01").replace("Ñ", "\x02")
    p = "".join(c for c in unicodedata.normalize("NFD", p)
                if unicodedata.category(c) != "Mn")
    return p.replace("\x01", "ñ").replace("\x02", "Ñ")


def sha256(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for b in iter(lambda: f.read(65536), b""):
            h.update(b)
    return h.hexdigest()


def expandir():
    """Aplica las reglas de afijos del .aff a cada raiz del .dic."""
    from spylls.hunspell import Dictionary
    d = Dictionary.from_files(os.path.join(FUENTE, "es_ES"))

    formas = set()
    for w in d.dic.words:
        stem = w.stem
        formas.add(stem)
        sufijadas = {stem}

        # ── sufijos ──────────────────────────────────────────────────
        for flag in w.flags:
            for regla in d.aff.SFX.get(flag, ()):
                if not regla.cond_regexp.search(stem):
                    continue
                base = stem[:-len(regla.strip)] if regla.strip else stem
                nueva = base + regla.add
                formas.add(nueva)
                if regla.crossproduct:
                    sufijadas.add(nueva)

        # ── prefijos, tambien sobre las formas ya sufijadas ──────────
        for flag in w.flags:
            for regla in d.aff.PFX.get(flag, ()):
                objetivo = sufijadas if regla.crossproduct else {stem}
                for s in objetivo:
                    if not regla.cond_regexp.search(s):
                        continue
                    base = s[len(regla.strip):] if regla.strip else s
                    formas.add(regla.add + base)

    return formas, len(d.dic.words)


def main():
    os.makedirs(DESTINO, exist_ok=True)
    print("[lex] expandiendo afijos...", flush=True)
    formas, n_raices = expandir()
    print("[lex] formas generadas: %d (desde %d raices)" % (len(formas), n_raices),
          flush=True)

    # ── indice: clave sin tildes -> formas reales ────────────────────
    indice = {}
    for f in formas:
        if not f or not f.isalpha():
            continue
        indice.setdefault(sin_tildes(f.lower()), set()).add(f.lower())

    # Solo interesa lo que puede fallar por una tilde. Si para una clave no
    # hay ninguna forma acentuada, esa entrada no aporta nada al problema y
    # solo hace el archivo mas grande.
    util = {}
    for clave, variantes in indice.items():
        if any(any(c in TILDES for c in v) for v in variantes):
            util[clave] = sorted(variantes)

    ambiguas = {k: v for k, v in util.items() if len(v) > 1}
    print("[lex] claves con al menos una forma acentuada: %d" % len(util),
          flush=True)
    print("[lex] de esas, ambiguas (mas de una variante): %d" % len(ambiguas),
          flush=True)

    doc = {
        "schema": "videos-txt.lexico-acentuacion/v1",
        "generado": FECHA,
        "proposito": ("Detectar palabras espanolas mal acentuadas en texto "
                      "escrito a mano antes de sintetizarlo con TTS. NO "
                      "corrige automaticamente: reporta y decide una persona."),
        "fuente": {
            "nombre": "RLA-ES - Recursos Linguisticos Abiertos del Espanol",
            "aclaracion": ("NO es un producto de la Real Academia Espanola. "
                           "La RAE no publica el DLE como datos descargables; "
                           "solo ofrece una aplicacion de consulta sin "
                           "conexion. RLA-ES es el lexico libre del espanol "
                           "que usan LibreOffice y Firefox, desarrollado de "
                           "forma independiente por Santiago Bosio."),
            "autor": "Santiago Bosio",
            "licencia": "GNU LGPL",
            "proyecto": "https://github.com/sbosio/rla-es",
            "descargado_de": ("https://raw.githubusercontent.com/elastic/"
                              "hunspell/master/dicts/es_ES/"),
            "fecha_descarga": FECHA,
            "archivos": {},
        },
        "cobertura": {
            "raices_en_el_diccionario": n_raices,
            "formas_tras_expandir_afijos": len(formas),
            "claves_con_forma_acentuada": len(util),
            "claves_ambiguas": len(ambiguas),
        },
        "como_leer_el_indice": {
            "clave": "la palabra en minusculas y SIN tildes (la enie se conserva)",
            "valor": "todas las formas reales del espanol que se escriben asi",
            "una_sola_variante_con_tilde": ("si el texto trae la version sin "
                                            "tilde, es un error seguro"),
            "varias_variantes": ("ambiguo: las dos existen y solo el contexto "
                                 "decide; se avisa, no se corrige"),
        },
        "indice": util,
    }

    for nombre in ("es_ES.dic", "es_ES.aff"):
        ruta = os.path.join(FUENTE, nombre)
        doc["fuente"]["archivos"][nombre] = {
            "bytes": os.path.getsize(ruta),
            "sha256": sha256(ruta),
        }

    salida = os.path.join(DESTINO, "lexico_acentuacion_es.json")
    with io.open(salida, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    print("[lex] escrito: %s (%.1f MB)"
          % (salida, os.path.getsize(salida) / 1e6), flush=True)

    # ── prueba de humo sobre el caso real que motivo esto ────────────
    print("\n[lex] comprobacion sobre el error real del 2026-08-25:")
    for w in ("dejo", "noto", "informacion", "impresora", "dieciseis",
              "unica", "esta"):
        v = util.get(w)
        if v is None:
            estado = "sin variante acentuada (o desconocida)"
        elif len(v) == 1:
            estado = "ERROR SEGURO -> %s" % v[0]
        else:
            estado = "AMBIGUO -> %s" % ", ".join(v)
        print("   %-12s %s" % (w, estado))
    return 0


if __name__ == "__main__":
    sys.exit(main())
