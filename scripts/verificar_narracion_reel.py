# -*- coding: utf-8 -*-
"""Revisa que la NARRACIÓN de un reel de código diga solo lo que el código y su SALIDA REAL
muestran (memoria «reels-narracion-verificada»: el LLM local describe mal el código).

Juez: Ollama local, respuesta JSON estricta. Doble pasada: si las dos no dicen OK, es FALLA.
Lo dudoso NUNCA se publica solo: va a Telegram con botones para revisión humana.

Uso:  python scripts/verificar_narracion_reel.py e92 [e93 …]        -> juzga entradas del banco
      python scripts/verificar_narracion_reel.py --calibrar         -> mide al juez con los casos
            conocidos (narraciones descartadas = deben FALLAR; corregidas = deben pasar)
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BANCO = REPO / "docs/operations/evidencia/poc_terminal_shorts_20261005/_banco.json"
OLLAMA = "http://localhost:11434/api/chat"
MODELO = "qwen2.5:14b-instruct-q4_K_M"

SISTEMA = (
    "Eres un verificador estricto de hechos para reels educativos de Python. Recibes CÓDIGO, su SALIDA REAL "
    "(lo que imprimió al ejecutarse) y una NARRACIÓN en español. Tu única tarea: decidir si TODO lo que afirma "
    "la narración es verdadero según el código y la salida. Reglas:\n"
    "- FALLA si la narración afirma una operación que el código no hace (p. ej. 'compara contra 15' y no hay "
    "comparación), un resultado distinto de la salida real, o un comportamiento falso de Python.\n"
    "- FALLA si dice que todos los casos dan lo mismo y la salida muestra valores distintos.\n"
    "- Redondeos razonables hablados ('unos cuatro' por 3.93) son OK. Omitir detalles es OK.\n"
    "- Números escritos con palabras equivalen a dígitos.\n"
    'Responde SOLO JSON: {"veredicto": "OK" o "FALLA", "motivo": "una frase en español"}'
)


def _juez(code: str, output: str, narracion: str, temperatura: float) -> dict:
    import requests
    user = "CÓDIGO:\n%s\n\nSALIDA REAL:\n%s\n\nNARRACIÓN:\n%s" % (code, output, narracion)
    try:
        r = requests.post(OLLAMA, timeout=300, json={
            "model": MODELO, "stream": False, "format": "json",
            "options": {"temperature": temperatura},
            "messages": [{"role": "system", "content": SISTEMA}, {"role": "user", "content": user}]})
        d = json.loads((r.json().get("message") or {}).get("content", "{}"))
        v = str(d.get("veredicto", "")).upper()
        return {"veredicto": "OK" if v == "OK" else "FALLA", "motivo": str(d.get("motivo", ""))[:200]}
    except Exception as e:  # sin juez no hay aprobación
        return {"veredicto": "FALLA", "motivo": "juez no disponible: %s" % str(e)[:120]}


ALCANCE = ("OK solo significa que la narracion puede pasar a REVISION HUMANA; no aprueba ni autoriza "
           "publicar (la aprobacion nace unicamente del boton de el operador en Telegram, Etapa 1)")


def juez_llm(code: str, output: str, narracion: str) -> dict:
    """Solo el modelo local, doble pasada: OK si las dos (temperatura 0 y 0.4) dicen OK."""
    import time
    t0 = time.monotonic()
    a = _juez(code, output, narracion, 0.0)
    b = _juez(code, output, narracion, 0.4) if a["veredicto"] == "OK" else a
    ok = a["veredicto"] == "OK" and b["veredicto"] == "OK"
    caido = any(x["motivo"].startswith("juez no disponible") for x in (a, b))
    return {"veredicto": "OK" if ok else "FALLA", "juez_caido": caido,
            "motivo": a["motivo"] if a["veredicto"] != "OK" else b["motivo"],
            "pasadas": 1 if a is b else 2, "segundos": round(time.monotonic() - t0, 1)}


def verificar(code: str, output: str, narracion: str) -> dict:
    """Etapa 2: primero la revision MATEMATICA determinista (sin modelo); un BLOQUEO es FALLA
    sin preguntar al modelo. Despues la revision EDITORIAL del modelo local. Las llaves
    `veredicto` y `motivo` se conservan porque `ciclo_diario` las lee."""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from content_factory.verificacion_numerica import verificar_numeros
    mate = verificar_numeros(code, output, narracion)
    if mate["veredicto"] == "BLOQUEO":
        return {"veredicto": "FALLA", "motivo": "numeros: " + mate["hallazgos"][0]["motivo"],
                "matematica": mate, "matematica_completa": False, "editorial": None, "alcance": ALCANCE}
    edit = juez_llm(code, output, narracion)
    motivo = edit["motivo"]
    veredicto = edit["veredicto"]
    if mate["veredicto"] == "AVISO":
        # Un AVISO no es validacion matematica: queda PENDIENTE de una persona, aunque el modelo
        # diga OK. Lo que no sea "OK" detiene la ruta automatica (ciclo_diario: != "OK").
        motivo += " | AVISO numeros (revisar a mano): " + "; ".join(h["motivo"] for h in mate["hallazgos"])
        if veredicto == "OK":
            veredicto = "REVISAR"
    return {"veredicto": veredicto, "motivo": motivo, "matematica": mate,
            "matematica_completa": mate["veredicto"] == "OK", "editorial": edit, "alcance": ALCANCE}


def _banco() -> dict:
    return {e["id"]: e for e in json.loads(BANCO.read_text(encoding="utf-8"))}


def calibrar() -> int:
    """Casos con verdad conocida: narración descartada (debe FALLAR) y la corregida a mano (debe pasar)."""
    casos = []
    for e in _banco().values():
        if e.get("narracion_llm_descartada"):
            casos.append((e["id"], "descartada", "FALLA", e["code"], e["output"], e["narracion_llm_descartada"]))
            casos.append((e["id"], "corregida", "OK", e["code"], e["output"], e["narracion"]))
    aciertos = 0
    for eid, tipo, esperado, code, out, nar in casos:
        r = verificar(code, out, nar)
        bien = r["veredicto"] == esperado
        aciertos += bien
        print("%s %-10s esperado=%-5s juez=%-5s %s | %s" % (eid, tipo, esperado, r["veredicto"],
                                                          "✓" if bien else "✗", r["motivo"][:110]), flush=True)
    print("CALIBRACIÓN: %d/%d aciertos" % (aciertos, len(casos)))
    return 0 if aciertos == len(casos) else 1


if __name__ == "__main__":
    if "--calibrar" in sys.argv:
        sys.exit(calibrar())
    b = _banco()
    for eid in sys.argv[1:]:
        e = b[eid]
        print(eid, json.dumps(verificar(e["code"], e["output"], e["narracion"]), ensure_ascii=False), flush=True)
