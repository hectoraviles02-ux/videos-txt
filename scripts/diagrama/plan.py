# -*- coding: utf-8 -*-
"""Motor «diagrama» — planificador (23-oct-2026). De la narración YA VERIFICADA (revisión de hechos del bot) saca
las frases y le pide a Claude (CLI `claude -p`) un plan de diagramas por escena con un catálogo fijo de piezas, más
cómo debe sonar cada frase (nombres en inglés escritos como suenan, cifras con letra). Todo texto en pantalla debe
ser LITERAL de la narración; el plan se valida y se ensaya (render.ensayar) antes de gastar la voz. Si Claude no
está, falla o su plan no pasa dos veces, se usa un plan de respaldo determinista (listas de fragmentos literales).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
REPO = AQUI.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(AQUI))
import render as RD  # noqa: E402

PROMPT = """Eres diseñador de videos explicativos verticales (estilo pizarra: fondo oscuro, cajas, flechas, listas).
Abajo están las FRASES numeradas de la narración, agrupadas por ESCENA. La narración ya fue verificada contra la
fuente: NO la cambies. Tu trabajo es decidir qué se DIBUJA en cada escena para EXPLICAR lo que dice la voz (el
mecanismo, las cifras, los pasos o acciones), no para decorar.

REGLAS DURAS
- Todo texto en pantalla (titulo, subtitulo, encabezado, texto de cada pieza, valor) debe ser un fragmento COPIADO
  LITERAL de las frases (mismas palabras, mismo orden; puedes cortar en cualquier punto). Nada inventado.
  Excepción: «encabezado» del centro puede ser uno de: "EL MECANISMO", "CRONOLOGÍA", "LOS DATOS", "EN CORTO".
- Si la frase ATRIBUYE lo que dice («afirman que…», «dicen…», «según…», «aseguran…»), el texto en pantalla
  conserva la atribución (p. ej. «afirman que la base trabajadora lo ve como un traidor»): sin ella, la pantalla
  lo afirmaría como hecho del canal.
- titulo ≤ 30 caracteres; subtitulo ≤ 45; textos de cajas e items ≤ 42; valor de número ≤ 6.
- "frase" = número de la frase (global) en la que esa pieza aparece; "palabra" (opcional) = una palabra de esa
  frase, para sincronizar exacto.
- Cada escena tiene tres zonas: "arriba", "centro", "abajo" (alguna puede ser null).
  arriba: {{"tipo":"insignias","items":[{{"texto","frase","palabra"?}}]}} (≤3 nombres) |
          {{"tipo":"numero","valor":"10","texto":"corrigieron 10 fallas","frase":n}} |
          {{"tipo":"cuadricula","n":7,"de":10,"texto":"casi 7 de cada 10 sitios web","frase":n}} |
          {{"tipo":"frase","texto":"…","frase":n}}
  centro: {{"tipo":"contraste","encabezado"?,"izquierda":{{"titulo","texto","fuente"?,"frase","palabra"?}},
             "derecha":{{…igual…}}}} (lo que dice una parte frente a la otra, o frente al dato) |
          {{"tipo":"documento","medio","fecha"?,"cita","frase","palabra"?}} (muestra la cita textual de una fuente) |
          {{"tipo":"barras","encabezado"?,"barras":[{{"etiqueta","valor":8,"texto":"8%","fuente"?,"frase"}}]}}
             (comparar cifras; «texto» literal de la narración; «valor» solo el número) |
          {{"tipo":"flujo","encabezado"?,"pasos":[{{"texto","frase","palabra"?}}]}} (2–4 pasos: causa → efecto) |
          {{"tipo":"linea_tiempo","encabezado"?,"eventos":[{{"texto","frase","palabra"?}}]}} (2–4) |
          {{"tipo":"lista","encabezado"?,"items":[{{"texto","frase","palabra"?}}]}} (≤4)
  abajo:  {{"tipo":"lista","encabezado"?,"items":[…]}} (≤3; ideal para «¿Qué hacer?» o «sin resolver») |
          {{"tipo":"documento","medio","fecha"?,"cita","frase"}} |
          {{"tipo":"frase","texto","frase"}} | {{"tipo":"numero","valor","texto","frase"}}
- Para cada frase da "dice": cómo debe sonar para una voz en español: nombres en inglés escritos como se
  pronuncian (Node.js → «Nóud yei es», JavaScript → «Yava Script», PHP → «pe ache pe», Docker → «Dóquer»),
  cifras y fechas con letra («10» → «diez», «16.4.1» → «dieciséis punto cuatro punto uno»), siglas como se dicen.
  Si no hace falta cambiar nada, repite la frase igual.

Responde SOLO JSON válido:
{{"id":"slug-corto","escenas":[{{"titulo","subtitulo","arriba","centro","abajo"}}], "dice":{{"1":"…","2":"…"}}}}

{contexto}
FRASES
{frases}
"""


def contexto_dossier(dossier: dict | None) -> tuple[str, list[str]]:
    """Del expediente de la investigación: qué medios y qué citas textuales VERIFICADAS se pueden mostrar
    (documento, contraste, etiqueta de fuente). Devuelve (texto para el prompt, textos permitidos en pantalla)."""
    if not dossier:
        return "", []
    medios, citas, lineas = [], [], []
    for c in dossier.get("afirmaciones", []):
        for x in c["fuentes"]:
            medios.append(x["medio"])
            citas.append(x["cita_textual"])
            if x.get("fecha"):
                medios.append(x["fecha"])
        x = c["fuentes"][0]
        lineas.append(f"- [{c['categoria']}; {c.get('parte') or 'dato'}; {c['nivel_certeza']}] {c['afirmacion']}"
                      f"\n    medio: {x['medio']} · fecha: {x.get('fecha') or '—'} · cita: «{x['cita_textual']}»")
    txt = ("EXPEDIENTE VERIFICADO (las piezas «documento» usan una de estas citas COPIADA LITERAL o un tramo de ella; "
           "«fuente»/«medio» usan uno de estos medios tal cual; muestra al menos un «documento» y, si hay dos partes, "
           "un «contraste». Las acusaciones y declaraciones siempre con su parte en el texto):\n"
           + "\n".join(lineas) + "\n")
    return txt, sorted(set(medios)) + citas


def frases_de(storyboard) -> list[dict]:
    """[{escena, texto}] en orden, una por oración de la narración verificada de cada escena."""
    out = []
    escenas = getattr(storyboard, "scenes", None) or storyboard.get("scenes", [])
    for i, s in enumerate(escenas, 1):
        narr = (getattr(s, "narration", None) if not isinstance(s, dict) else s.get("narration")) or ""
        for o in re.split(r"(?<=[.!?])\s+(?=[¿¡A-ZÁÉÍÓÚÑ0-9])", " ".join(narr.split())):
            if o.strip():
                out.append({"escena": i, "texto": o.strip()})
    return out


def _json_de(raw: str) -> dict:
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
    i, j = s.find("{"), s.rfind("}")
    return json.loads(s[i:j + 1])


def dice_respaldo(texto: str) -> str:
    from synthesis.texto_locucion import aplicar_lexico, preparar_para_locucion
    t = re.sub(r"\b(\d+)\.(\d+)\.(\d+)\b", lambda m: " punto ".join(m.groups()), texto)   # versiones 16.4.1
    return aplicar_lexico(preparar_para_locucion(t))


def validar(plan: dict, frases: list[dict], leyenda: str, extras: list[str] | None = None) -> list[str]:
    errores = []
    narr = " ".join(f["texto"] for f in frases)
    n_esc = max(f["escena"] for f in frases)
    if len(plan.get("escenas", [])) != n_esc:
        errores.append(f"se esperaban {n_esc} escenas y llegaron {len(plan.get('escenas', []))}")
        return errores

    def piezas(z):
        if not z:
            return []
        return [z] + list(z.get("items", []) or []) + list(z.get("pasos", []) or []) + list(z.get("eventos", []) or [])
    for k, esc in enumerate(plan["escenas"], 1):
        if not RD.es_literal(esc.get("titulo", ""), narr) or not esc.get("titulo"):
            errores.append(f"escena {k}: titulo «{esc.get('titulo')}» no es literal")
        for zona in ("arriba", "centro", "abajo"):
            for p in piezas(esc.get(zona)):
                if "frase" in p and not (1 <= int(p["frase"]) <= len(frases)):
                    errores.append(f"escena {k} {zona}: frase {p['frase']} no existe")
    if errores:
        return errores
    try:
        RD.ensayar({**plan, "frases": frases}, narr, leyenda, extras)
    except SystemExit as e:
        errores.append(str(e))
    return errores


def respaldo(frases: list[dict]) -> dict:
    """Plan determinista: título = inicio literal de la escena; centro = lista de fragmentos literales."""
    def corte(t, n):
        t = t.rstrip(".")
        for sep in (":", ",", " y "):
            if sep in t and len(t.split(sep)[0]) >= 12:
                t = t.split(sep)[0]
                break
        palabras, out = t.split(), ""
        for p in palabras:
            if len((out + " " + p).strip()) > n:
                break
            out = (out + " " + p).strip()
        return out
    escenas = []
    for e in sorted({f["escena"] for f in frases}):
        fs = [(k + 1, f) for k, f in enumerate(frases) if f["escena"] == e]
        items = [{"texto": corte(f["texto"], 42), "frase": k} for k, f in fs[1:5]]
        escenas.append({"titulo": corte(fs[0][1]["texto"], 30).upper(), "subtitulo": None,
                        "arriba": {"tipo": "frase", "texto": corte(fs[0][1]["texto"], 42), "frase": fs[0][0]},
                        "centro": {"tipo": "lista", "items": items} if items else None, "abajo": None})
    return {"id": "video", "escenas": escenas, "origen_plan": "respaldo"}


def planificar(frases: list[dict], leyenda: str, *, ejecutar=subprocess.run, claude: str | None = None,
               registro=None, dossier: dict | None = None) -> dict:
    claude = claude if claude is not None else shutil.which("claude")
    lista = "\n".join(f"ESCENA {f['escena']} · {k}. {f['texto']}" for k, f in enumerate(frases, 1))
    contexto, extras = contexto_dossier(dossier)
    prompt, plan, errores = PROMPT.format(frases=lista, contexto=contexto), None, ["sin intentar"]
    for intento in (1, 2):
        if not claude:
            errores = ["no está instalado el comando claude"]
            break
        try:
            r = ejecutar([claude, "-p", "--max-turns", "1", "--output-format", "text"], input=prompt,
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=420)
            if r.returncode != 0:
                raise RuntimeError(f"claude salió con {r.returncode}")
            cand = _json_de(r.stdout)
            errores = validar(cand, frases, leyenda, extras)
            if not errores:
                plan = cand
                plan["origen_plan"] = f"claude (intento {intento})"
                break
            prompt = PROMPT.format(frases=lista, contexto=contexto) + "\n\nTU PLAN ANTERIOR FUE RECHAZADO POR:\n- " + "\n- ".join(errores)
        except Exception as e:  # noqa: BLE001 - sin plan de Claude se usa el de respaldo
            errores = [f"{type(e).__name__}: {str(e)[:160]}"]
        if registro:
            registro(f"plan intento {intento}: {errores}")
    if plan is None:
        plan = respaldo(frases)
        plan["motivo_respaldo"] = errores
        er = validar(plan, frases, leyenda, extras)
        if er:
            raise SystemExit(f"ni el plan de respaldo pasó: {er}")
    dice = plan.pop("dice", {}) or {}
    plan["frases"] = [dict(f, dice=str(dice.get(str(k), "")).strip() or dice_respaldo(f["texto"]))
                      for k, f in enumerate(frases, 1)]
    plan["leyenda"] = leyenda
    plan["extras"] = extras
    plan["id"] = re.sub(r"[^a-z0-9_-]", "", (plan.get("id") or "video").lower().replace(" ", "-"))[:40] or "video"
    return plan
