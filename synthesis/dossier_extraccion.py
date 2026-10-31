# -*- coding: utf-8 -*-
"""Extracción CONFIABLE del dossier de claims — PROTOTIPO, NO conectado al bot.

Motivo (medido 2026-10-13): qwen 14B, con la nota completa en una sola llamada, generó 19 claims, de
los que 3 pasaron la validación, y omitió 5 de 10 hechos centrales (incluido el 2,9 % / 3,45 % /
3,9 %, el centro de la nota).

Diseño (GO de el operador, 13-oct):
1. La fuente se divide en BLOQUES de oraciones; cada oración guarda su posición exacta.
2. INVENTARIO determinista por bloque: cifras (números, %, fechas) y nombres propios.
3. Por bloque, el LLM devuelve claims atómicos (cita mínima, sujeto, tipo, atribuido_a) y un
   DESCARTE con motivo para cada oración y cada cifra que no use. Lo que no se usa ni se descarta
   deja el bloque INCOMPLETO.
4. Cada claim pasa synthesis.guion_anclado.validar_dossier (literal, tipo, atribución, matices,
   sujeto local, NLI obligatorio) y además reglas de ubicación (la cita está en SU bloque) y de
   atribución con evidencia (quien habla debe aparecer en el bloque; si no, «indeterminado»).
5. Solo los bloques con claims rechazados o con oraciones o cifras sin cubrir se reintentan, con los
   motivos, hasta 2 veces. Las reglas no se relajan.
6. Todo queda trazado: claims aceptados, rechazados con motivo, descartes, llamadas y tiempo.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from synthesis import gate_fidelidad as G
from synthesis import guion_anclado as A

ORACIONES_POR_BLOQUE = 3
MAX_REINTENTOS = 2
INDETERMINADO = "indeterminado"
_NOMBRE = re.compile(r"(?<=[a-záéíóúñ,;:(]\s)(?:[A-ZÁÉÍÓÚÑ][\wáéíóúñ]+(?:\s+(?:de|del|la|y)?\s*[A-ZÁÉÍÓÚÑ][\wáéíóúñ]+)*)"
                     r"|\b[A-ZÁÉÍÓÚÑ]{2,}\b")


@dataclass
class Oracion:
    id: str
    texto: str
    ini: int
    fin: int


@dataclass
class Bloque:
    id: str
    oraciones: list[Oracion]
    cifras: list[str] = field(default_factory=list)
    nombres: list[str] = field(default_factory=list)

    @property
    def texto(self) -> str:
        return " ".join(o.texto for o in self.oraciones)


# ── 1-2) segmentación e inventario ────────────────────────────────────────

def segmentar(texto: str, por_bloque: int = ORACIONES_POR_BLOQUE) -> list[Bloque]:
    t = G._espaciar(texto)
    oraciones, pos = [], 0
    for k, o in enumerate(G._oraciones(t), 1):
        i = t.find(o, pos)
        oraciones.append(Oracion(f"o{k:02d}", o, i, i + len(o)))
        pos = max(pos, i + len(o))
    bloques = []
    for k in range(0, len(oraciones), por_bloque):
        b = Bloque(f"B{k // por_bloque + 1:02d}", oraciones[k:k + por_bloque])
        b.cifras = sorted({tok[1:] for tok in G._tokens(b.texto) if tok.startswith("#")
                           and re.search(r"\d", tok)}, key=lambda x: float(x) if x.replace(".", "").isdigit() else 0)
        b.nombres = sorted({m.group(0).strip() for m in _NOMBRE.finditer(b.texto)})
        bloques.append(b)
    return bloques


# ── 3) llamada al LLM por bloque ──────────────────────────────────────────

def _prompt(b: Bloque, previo: str, motivos: list[str]) -> str:
    oraciones = "\n".join(f"[{o.id}] {o.texto}" for o in b.oraciones)
    reintento = ""
    if motivos:
        reintento = ("\nINTENTO ANTERIOR RECHAZADO. Corrige SOLO esto, sin inventar nada:\n- "
                     + "\n- ".join(motivos[:12]) + "\n")
    return f"""Eres documentalista periodístico. Del BLOQUE de una nota, extrae claims ATÓMICOS (una idea cada uno).
Reglas:
- "cita_textual": el tramo MÍNIMO del bloque, copiado EXACTO, que prueba el claim (no la oración entera si sobra).
- "afirmacion": lo que dice esa cita, sin agregar NADA. No agregues sujetos que la cita no dice.
- "sujeto": de quién se habla. Si la cita usa «su», «ellos», «la mandataria», etc., pon en "sujeto_cita" el tramo EXACTO
  (de este bloque o del CONTEXTO PREVIO) que nombra al sujeto. Si no se puede saber, "sujeto": "{INDETERMINADO}".
- "tipo_enunciado": "hecho" (dato que da el periódico), "testimonio" (lo cuenta una persona), "declaracion"
  (lo dice una institución o autoridad), "compromiso" (promesa a futuro) o "valoracion" (opinión o estimación).
  Si la cita tiene comillas o verbos como dice, explica, denuncian, afirmó, informó, según: NO es "hecho".
- "atribuido_a": quién lo dice, con el nombre tal como aparece en el bloque; vacío si es "hecho".
  Si no se puede saber quién lo dice, "{INDETERMINADO}".
- Conserva los matices entre comillas (p. ej. «legítimas»).
- TODA cifra y TODA oración del bloque debe quedar en algún claim o en "descartes" con su motivo.
  Cifras del bloque: {", ".join(b.cifras) or "ninguna"}.
Responde SOLO JSON:
{{"claims":[{{"oraciones":["o01"],"afirmacion":"...","cita_textual":"...","sujeto":"...","sujeto_cita":"",
"tipo_enunciado":"...","atribuido_a":""}}],
"descartes":[{{"oracion":"o02","cifras":[],"motivo":"..."}}]}}
{reintento}
CONTEXTO PREVIO (solo para identificar sujetos; no extraigas de aquí):
{previo or "(inicio de la nota)"}

BLOQUE {b.id}:
{oraciones}
"""


def llm_ollama(url: str, modelo: str, timeout: int = 900):
    import requests

    def _llm(prompt: str) -> dict:
        r = requests.post(f"{url.rstrip('/')}/api/generate", json={
            "model": modelo, "prompt": prompt, "stream": False, "format": "json",
            "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 2500}}, timeout=timeout)
        r.raise_for_status()
        return json.loads(r.json()["response"])
    _llm.nombre = f"ollama {modelo}"
    return _llm


# ── 4) validación de UN claim en su bloque ────────────────────────────────

def _validar_claim(c: dict, b: Bloque, texto: str, juez) -> list[str]:
    errores = list(A.validar_dossier({"claims": [c]}, texto, juez).errores)
    cita = A._cita(c)
    if cita and not G._es_literal(cita, b.texto):
        errores.append("la cita no está en su bloque (ubicación)")
    atr = (c.get("atribuido_a") or "").strip()
    if atr and atr != INDETERMINADO:
        # quien habla debe aparecer con su nombre en el bloque (no se infiere de fuera)
        nombres = [w for w in re.findall(r"[A-ZÁÉÍÓÚÑ][\wáéíóúñ]+", atr)
                   if w.lower() not in {"el", "la", "los", "las", "según"}]
        bn = G._norm(b.texto)
        if nombres and not all(G._norm(w) in bn for w in nombres):
            errores.append(f"atribuido_a «{atr}» no aparece en el bloque: usar «{INDETERMINADO}»")
    if c.get("sujeto") == INDETERMINADO and c.get("tipo_enunciado") == "hecho" and \
            re.search(r"(?i)\b(su|sus|ella|ellos|ellas)\b", cita):
        errores.append("sujeto indeterminado en un hecho con pronombre: no se puede afirmar")
    return errores


def _cobertura_bloque(b: Bloque, aceptados: list[dict], descartes: list[dict]) -> list[str]:
    """Oraciones y cifras del bloque que no quedaron ni en un claim aceptado ni en un descarte."""
    faltan = []
    usadas = {o for c in aceptados for o in (c.get("oraciones") or [])}
    citas = " ".join(A._cita(c) for c in aceptados)
    for o in b.oraciones:
        cubierta = o.id in usadas or any(
            G._norm(A._cita(c)) and G._norm(A._cita(c)) in G._norm(o.texto) for c in aceptados)
        if not cubierta and not any(d.get("oracion") == o.id and (d.get("motivo") or "").strip()
                                    for d in descartes):
            faltan.append(f"oración {o.id} sin claim ni descarte: «{o.texto[:80]}»")
    cifras_claims = {t[1:] for t in G._tokens(citas) if t.startswith("#")}
    cifras_desc = {str(x) for d in descartes for x in (d.get("cifras") or [])}
    cifras_desc |= {t[1:] for d in descartes for t in G._tokens(" ".join(map(str, d.get("cifras") or [])))
                    if t.startswith("#")}
    for n in b.cifras:
        if n not in cifras_claims and n not in cifras_desc:
            faltan.append(f"cifra {n} sin claim ni descarte")
    return faltan


# ── 5-6) extracción completa ──────────────────────────────────────────────

def extraer(fuente: dict | str, llm, juez, *, medio: str = "", por_bloque: int = ORACIONES_POR_BLOQUE,
            max_reintentos: int = MAX_REINTENTOS) -> dict:
    texto = G.texto_de_fuente(fuente)
    url = fuente.get("source_url", "") if isinstance(fuente, dict) else ""
    medio = medio or (G._medio(fuente) if isinstance(fuente, dict) else "")
    bloques = segmentar(texto, por_bloque)
    aceptados, rechazados, descartes, traza = [], [], [], []
    llamadas, t0 = 0, time.time()
    for i, b in enumerate(bloques):
        previo = " ".join(o.texto for o in (bloques[i - 1].oraciones if i else []))
        motivos: list[str] = []
        acept_b: list[dict] = []
        desc_b: list[dict] = []
        for intento in range(max_reintentos + 1):
            try:
                datos = llm(_prompt(b, previo, motivos))
            except Exception as e:  # el bloque queda sin extraer y se reporta
                llamadas += 1
                traza.append({"bloque": b.id, "intento": intento,
                              "error_llm": f"{type(e).__name__}: {e}"[:200],
                              "sin_cubrir": _cobertura_bloque(b, acept_b, desc_b)})
                break
            llamadas += 1
            motivos = []
            desc_b = [d for d in (datos.get("descartes") or []) if isinstance(d, dict)]
            for k, c in enumerate(datos.get("claims") or [], 1):
                if not isinstance(c, dict):
                    continue
                claim = {
                    "id": f"{b.id}-{intento}-{k:02d}", "afirmacion": str(c.get("afirmacion", "")).strip(),
                    "nivel_certeza": "HECHO_REPORTADO_PRENSA" if c.get("tipo_enunciado", "hecho") == "hecho"
                    else "DECLARACION_ATRIBUIDA",
                    "afirma_causalidad": False,
                    "tipo_enunciado": str(c.get("tipo_enunciado", "hecho")).strip().lower(),
                    "atribuido_a": str(c.get("atribuido_a") or "").strip(),
                    "sujeto": str(c.get("sujeto") or "").strip(),
                    "sujeto_cita": str(c.get("sujeto_cita") or "").strip(),
                    "oraciones": c.get("oraciones") or [], "bloque": b.id,
                    "fuentes": [{"url": url, "medio": medio, "tipo_fuente": "prensa",
                                 "cita_textual": str(c.get("cita_textual", "")).strip()}],
                }
                if not claim["afirmacion"]:
                    continue
                if any(G._norm(claim["afirmacion"]) == G._norm(x["afirmacion"]) for x in acept_b):
                    continue  # repetido
                errs = _validar_claim(claim, b, texto, juez)
                if errs:
                    rechazados.append(dict(claim, motivos_rechazo=errs))
                    motivos.append(f"claim «{claim['afirmacion'][:70]}»: {errs[0]}")
                else:
                    acept_b.append(claim)
            faltan = _cobertura_bloque(b, acept_b, desc_b)
            traza.append({"bloque": b.id, "intento": intento, "aceptados": len(acept_b),
                          "rechazados_en_intento": len(motivos), "sin_cubrir": faltan})
            if not motivos and not faltan:
                break
            motivos += faltan
        aceptados += acept_b
        descartes += [dict(d, bloque=b.id) for d in desc_b]
    sin_cubrir = [f"{t['bloque']}: {x}" for t in traza if t is _ultimo(traza, t["bloque"])
                  for x in t.get("sin_cubrir", [])]
    return {"medio": medio, "url": url, "bloques": len(bloques), "llamadas": llamadas,
            "segundos": round(time.time() - t0, 1), "llm": getattr(llm, "nombre", "llm"),
            "claims": aceptados, "rechazados": rechazados, "descartes": descartes,
            "sin_cubrir": sin_cubrir, "completo": not sin_cubrir, "traza": traza}


def _ultimo(traza: list[dict], bloque: str) -> dict:
    return [t for t in traza if t["bloque"] == bloque][-1]


def como_investigacion(res: dict, caso_id: str) -> dict:
    """Dossier en el formato de schemas/investigacion.schema.json (campos extra opcionales)."""
    return {"caso_id": caso_id, "version": "extraccion-auto-1", "cronologia": [],
            "claims": [{k: v for k, v in c.items() if k not in {"oraciones", "bloque"}} | {"bloque": c["bloque"]}
                       for c in res["claims"]]}
