# -*- coding: utf-8 -*-
"""Coordinador minimo de investigacion (paso 2 de AUDITORIA_INVESTIGACION_AUTOMATICA_20261022.md).

NO es un motor nuevo: solo ENCADENA lo que ya existe.
  buscar (telegram_bot/investigar_web.buscar) -> extraer (scripts/procesar_url.py, con la rama PDF)
  -> pasajes del caso (seleccion determinista por palabras clave) -> afirmaciones con cita literal
  (synthesis/dossier_extraccion.extraer) -> FIDELIDAD (la cita esta, literal, en la pagina citada)
  -> VERACIDAD (synthesis/gate_fidelidad.verificar_afirmacion + juez NLI: la cita dice eso)
  -> contraste entre fuentes (NLI sobre los pasajes de las otras fuentes; eco de una misma
  declaracion = no independiente) -> certeza (scripts/expediente.clasificar_certeza)
  -> cronologia (solo fechas que aparecen literales en la cita) -> sondas falsas (tasa de aceptacion
  falsa) -> expediente/<caso>/expediente.json + EXPEDIENTE.md (10 secciones) + criterios A1-A9.

Reglas (texto de Codex pegado por operador el 22-oct):
- Fidelidad y veracidad son DOS revisiones distintas: pasar la primera no sube la certeza.
- Una cita textual del modelo no hace verdadera la afirmacion; una coincidencia textual no corrobora.
- Wikipedia y blogs son PISTAS: nunca cuentan como fuente.
- Cifras distintas entre fuentes se marcan «posible contradiccion — revisar referente», no EN-DISPUTA.
- Si la evidencia no alcanza o el verificador acepta algo falso: expediente INCOMPLETO / EXPERIMENTAL.
- No toca motores de video, no publica, no renderiza.

Uso:  python scripts/investigar_tema.py expediente/<caso>/caso.json
"""
from __future__ import annotations

import glob
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "scripts"))

import expediente as E  # noqa: E402
from synthesis import gate_fidelidad as G  # noqa: E402

# ── tipo de fuente por dominio (determinista) ─────────────────────────────
PRIMARIA = ("justice.gov", ".gov", "uscourts.gov", "courtlistener.com", "senate.gov.ph", "bb.org.bd",
            "newyorkfed.org", "swift.com", "sec.gov", "nasa.gov", "esa.int", ".gob.mx", "inegi.org.mx", "banxico.org.mx")
PRENSA = ("bbc.", "reuters.com", "apnews.com", "nytimes.com", "wsj.com", "theguardian.com", "pcij.org",
          "gmanetwork.com", "philstar.com", "bdnews24.com", "thedailystar.net", "inquirer.net",
          "rappler.com", "wired.com", "ft.com", "bloomberg.com", "washingtonpost.com", "elpais.com",
          "isaca.org", "dhakatribune.com", "abs-cbn.com", "nikkei.com", "economist.com",
          # prensa mexicana (23-oct-2026, investigación de enlaces del bot)
          "eluniversal.com.mx", "expansion.mx", "infobae.com", "jornada.com.mx", "milenio.com", "proceso.com.mx",
          "animalpolitico.com", "elfinanciero.com.mx", "excelsior.com.mx", "reforma.com", "eleconomista.com.mx",
          "sinembargo.mx", "aristeguinoticias.com", "latinus.us", "nmas.com.mx", "radioformula.com.mx",
          "elsoldemexico.com.mx", "forbes.com.mx", "cnnespanol.cnn.com", "elpais.com")
TERCIARIA = ("wikipedia.org", "scribd.com", "wikiwand.com")
CUENTAN = {"primaria", "prensa"}          # solo estas pueden respaldar una afirmacion
DESAFIO = re.compile(r"(?i)bm-verify|captcha|access denied|enable javascript|are you a robot|"
                     r"verify you are human|request unsuccessful")
ECO = re.compile(r"(?i)\b(FBI|Justice Department|Department of Justice|DOJ|complaint|indictment|"
                 r"prosecutors?|charged)\b")
MESES = ("January|February|March|April|May|June|July|August|September|October|November|December|"
         "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre")
FECHA = re.compile(rf"\b(?:(?:{MESES})\s+\d{{1,2}},?\s+(?:19|20)\d\d|\d{{1,2}}\s+(?:de\s+)?(?:{MESES})"
                   rf"(?:\s+de)?\s+(?:19|20)\d\d|(?:{MESES})(?:\s+de|,)?\s+(?:19|20)\d\d)\b", re.I)
NUMERO_MES = {m: i + 1 for i, m in enumerate("january february march april may june july august "
                                             "september october november december".split())}
NUMERO_MES.update({m: i + 1 for i, m in enumerate("enero febrero marzo abril mayo junio julio agosto "
                                                  "septiembre octubre noviembre diciembre".split())})


def tipo_fuente(url: str) -> str:
    dom = urlparse(url).netloc.lower()
    if any(d in dom for d in TERCIARIA):
        return "terciaria"
    if any(dom.endswith(d) or d in dom for d in PRIMARIA if d.startswith(".")) or \
            any(d in dom for d in PRIMARIA if not d.startswith(".")):
        return "primaria"
    if any(d in dom for d in PRENSA):
        return "prensa"
    return "blog"          # desconocido: pista, no fuente (no se infla la independencia)


# ── bitacora de tiempos ───────────────────────────────────────────────────
class Bitacora:
    def __init__(self, ruta: Path):
        self.ruta, self.etapas, self._t = ruta, [], None

    def linea(self, texto: str):
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        with self.ruta.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {texto}\n")
        print(texto, flush=True)

    def etapa(self, nombre: str):
        b = self

        class _Ctx:
            def __enter__(self_):
                self_.t0 = time.time()
                b.linea(f"INICIO {nombre}")

            def __exit__(self_, *exc):
                s = round(time.time() - self_.t0, 1)
                b.etapas.append({"etapa": nombre, "segundos": s, "error": repr(exc[1]) if exc[1] else None})
                b.linea(f"FIN {nombre} ({s} s){' ERROR ' + repr(exc[1]) if exc[1] else ''}")
        return _Ctx()


# ── 1-2) fuentes: obtener y validar ───────────────────────────────────────
def _json_de(url: str) -> Path | None:
    sys.path.insert(0, str(BASE / "scripts"))
    from comun import calcular_hash
    h = calcular_hash(url)
    hallados = sorted(glob.glob(str(BASE / "procesado" / f"*_{h}" / "*.json")))
    return Path(hallados[-1]) if hallados else None


def obtener(url: str, ejecutar=subprocess.run) -> dict:
    """Extrae con procesar_url (o reutiliza la extraccion previa). Devuelve {estado, json?, motivo}."""
    previo = _json_de(url)
    if previo is None:
        r = ejecutar([sys.executable, str(BASE / "scripts" / "procesar_url.py"), url], capture_output=True,
                     text=True, encoding="utf-8", errors="replace", timeout=900,
                     env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
        salida = (r.stdout or "") + (r.stderr or "")
        if "PENDIENTE" in salida and "no resuelve" in salida:
            return {"estado": "no_resuelve", "motivo": salida.strip().splitlines()[-1][:200]}
        previo = _json_de(url)
        if previo is None:
            return {"estado": "fallo_extraccion", "motivo": salida.strip()[-200:]}
    reg = json.loads(previo.read_text(encoding="utf-8"))
    texto = G.texto_de_fuente(reg)
    if not reg.get("segmentos"):
        return {"estado": "sin_texto", "json": str(previo), "motivo": "; ".join(reg.get("avisos", []))[:200]}
    if len(texto) < 800 or DESAFIO.search(texto[:3000]):
        return {"estado": "pagina_no_valida", "json": str(previo),
                "motivo": f"{len(texto)} caracteres o pagina de verificacion anti-robot"}
    return {"estado": "ok", "json": str(previo), "reg": reg}


# ── 3) pasajes del caso (determinista) ────────────────────────────────────
def pasajes(reg: dict, palabras_caso: list[str], subpreguntas: dict, maximo: int) -> tuple[list[dict], dict]:
    """Ventanas de 3 oraciones que nombran el caso y tocan alguna subpregunta. Se elige por puntaje,
    sin solaparse. Devuelve (pasajes, cobertura) para declarar lo que NO se leyo."""
    candidatos = []
    for i, s in enumerate(reg.get("segmentos") or []):
        texto = " ".join(str(s.get("texto", "")).split())
        ors = G._oraciones(texto)
        for k in range(len(ors)):
            ventana = " ".join(ors[k:k + 3])
            vn = G._norm(ventana)
            if not any(G._norm(p) in vn for p in palabras_caso):
                continue
            temas = sorted(t for t, pals in subpreguntas.items() if any(G._norm(p) in vn for p in pals))
            if not temas:
                continue
            puntos = len(temas) + 0.5 * len(re.findall(r"\d", ventana)[:6]) / 6
            candidatos.append({"seg": i, "pagina": s.get("pagina"), "k": k, "texto": ventana,
                               "temas": temas, "puntos": puntos})
    elegidos, usados = [], set()
    for c in sorted(candidatos, key=lambda c: -c["puntos"]):
        clave = {(c["seg"], c["k"] + d) for d in range(3)}
        if clave & usados:
            continue
        elegidos.append(c)
        usados |= clave
        if len(elegidos) >= maximo:
            break
    paginas = {s.get("pagina") for s in reg.get("segmentos") or []}
    con_caso = {c["pagina"] for c in candidatos}
    leidas = {c["pagina"] for c in elegidos}
    cobertura = {"candidatos": len(candidatos), "elegidos": len(elegidos),
                 "paginas_con_el_caso": sorted(p for p in con_caso if p is not None),
                 "paginas_con_el_caso_no_leidas": sorted(p for p in con_caso - leidas if p is not None),
                 "paginas_total": len(paginas)}
    return sorted(elegidos, key=lambda c: (c["seg"], c["k"])), cobertura


# ── 4-5) fidelidad y veracidad (dos revisiones distintas) ─────────────────
def fidelidad(cita: str, reg: dict, seg: int) -> dict:
    """¿La cita existe literal en el segmento (pagina) del que dice venir? Solo eso."""
    texto = " ".join(str((reg.get("segmentos") or [])[seg].get("texto", "")).split())
    ok = bool(cita.strip()) and len(G._norm(cita)) >= 15 and G._es_literal(cita, texto)
    return {"ok": ok, "pagina": (reg.get("segmentos") or [])[seg].get("pagina"), "segmento": seg}


def veracidad(afirmacion: str, cita: str, juez) -> dict:
    """¿La cita dice lo que afirma la afirmacion? NLI + cifras con referente + vocabulario."""
    try:
        v = (juez(cita, [afirmacion]) or [{}])[0]
    except G.JuezNoDisponible as e:
        return {"clasificacion": G.SIN_EVIDENCIA, "motivo": f"verificador no disponible: {e}"}
    a = G.verificar_afirmacion(afirmacion, cita, v)
    return {"clasificacion": a.clasificacion, "motivo": "; ".join(a.motivos)[:200],
            "nli": v.get("puntajes")}


def certeza(claim: dict) -> str:
    if not claim["fidelidad"]["ok"] or claim["veracidad"]["clasificacion"] != G.RESPALDADA:
        return "NO-CONFIRMADO"          # pasar solo una de las dos revisiones no sube la certeza
    return E.clasificar_certeza(claim)


# ── 6) contraste entre fuentes ────────────────────────────────────────────
def contrastar(claim: dict, otras: list[dict], juez) -> None:
    """Busca la afirmacion en los pasajes de OTRAS fuentes que cuentan. Respaldo con fragmento literal
    -> fuente adicional (eco si el fragmento remite a la acusacion/FBI). Contradiccion -> se marca para
    revisar el referente; no se decide sola."""
    for f in otras:
        if f["url"] == claim["fuentes"][0]["url"] or f["tipo"] not in CUENTAN or not f.get("texto_pasajes"):
            continue
        try:
            v = (juez(f["texto_pasajes"], [claim["afirmacion"]]) or [{}])[0]
        except G.JuezNoDisponible:
            continue
        a = G.verificar_afirmacion(claim["afirmacion"], f["texto_pasajes"], v)
        frag = a.fragmento or v.get("fragmento") or ""
        if a.clasificacion == G.RESPALDADA and frag and G._es_literal(frag, f["texto_pasajes"]):
            claim["fuentes"].append({"url": f["url"], "medio": f["medio"], "tipo_fuente": f["tipo"],
                                     "cita_textual": frag, "eco_declaracion": bool(ECO.search(frag)),
                                     "revisar_eco": bool(ECO.search(frag))})
        elif v.get("clasificacion") == "CONTRADICHA" and frag:
            claim.setdefault("posibles_contradicciones", []).append(
                {"url": f["url"], "medio": f["medio"], "fragmento": frag,
                 "nota": "posible contradiccion — revisar referente (intento/transferido/recuperado)"})


# ── 7) cronologia ─────────────────────────────────────────────────────────
def _orden(fecha: str) -> str:
    t = fecha.lower().replace(",", " ").replace(" de ", " ").split()
    anio = next((x for x in t if re.fullmatch(r"(19|20)\d\d", x)), "0000")
    mes = next((NUMERO_MES[x] for x in t if x in NUMERO_MES), 0)
    dia = next((int(x) for x in t if re.fullmatch(r"\d{1,2}", x)), 0)
    return f"{anio}-{mes:02d}-{dia:02d}"


def cronologia(claims: list[dict]) -> list[dict]:
    """Solo fechas que aparecen LITERALES en la cita de un claim fiel y verdadero."""
    ev = []
    for c in claims:
        if c["nivel_certeza"] == "NO-CONFIRMADO":
            continue
        cita = c["fuentes"][0]["cita_textual"]
        for m in FECHA.finditer(cita):
            ev.append({"fecha": m.group(0), "orden": _orden(m.group(0)), "hecho": c["afirmacion"],
                       "claim_id": c["id"], "certeza": c["nivel_certeza"], "url": c["fuentes"][0]["url"]})
    vistos, out = set(), []
    for e in sorted(ev, key=lambda e: (e["orden"], e["claim_id"])):
        if (e["orden"], e["claim_id"]) not in vistos:
            vistos.add((e["orden"], e["claim_id"]))
            out.append(e)
    return out


# ── 8) sondas falsas: cuanto acepta el verificador que NO deberia ─────────
def mutaciones(afirmacion: str) -> list[tuple[str, str]]:
    out = []
    m = re.search(r"\d[\d,\.]*\d|\d", afirmacion)
    if m:
        num = m.group(0)
        digito = num[0]
        otro = str((int(digito) + 3) % 10 or 7)
        out.append(("cifra", afirmacion[:m.start()] + otro + num[1:] + afirmacion[m.end():]))
    a = re.search(r"\b(19|20)\d\d\b", afirmacion)
    if a:
        out.append(("año", afirmacion[:a.start()] + str(int(a.group(0)) - 3) + afirmacion[a.end():]))
    n = re.search(r"\b(was|were|is|are|has|have|had|did)\b", afirmacion)
    if n:
        out.append(("negacion", afirmacion[:n.end()] + " not" + afirmacion[n.end():]))
    return out


def sondas_mutadas(claims: list[dict], juez, maximo: int = 60) -> dict:
    """Cada mutacion es FALSA por construccion (cambia cifra, año o niega). Pasa por la MISMA revision
    de veracidad que acepto el claim original. Si alguna sale RESPALDADA = aceptacion falsa."""
    pruebas, falsas = [], []
    for c in claims:
        if c["nivel_certeza"] == "NO-CONFIRMADO":
            continue
        for tipo, texto in mutaciones(c["afirmacion"]):
            if len(pruebas) >= maximo:
                break
            r = veracidad(texto, c["fuentes"][0]["cita_textual"], juez)
            p = {"claim_id": c["id"], "tipo": tipo, "mutacion": texto, "resultado": r["clasificacion"]}
            pruebas.append(p)
            if r["clasificacion"] == G.RESPALDADA:
                falsas.append(p)
    return {"pruebas": len(pruebas), "aceptadas_falsas": len(falsas),
            "tasa": round(len(falsas) / len(pruebas), 3) if pruebas else None, "detalle": pruebas}


_EQUIV = {"filipinas": "philippines", "corea": "korea", "singapur": "singapore", "malasia": "malaysia",
          "manila": "manila", "lazarus": "lazarus", "swift": "swift", "sri": "sri", "lanka": "lanka",
          "basanta": "basanta", "sql": "sql", "casino": "casino", "casinos": "casino", "impresora": "printer"}


def anclas(texto: str) -> set[str]:
    """Cifras y nombres de la sonda que el fragmento debe compartir para que el veredicto signifique algo.
    El NLI multilingue «contradice» con alta confianza pares que no tratan el mismo tema (corrida 1)."""
    out = {t[1:] for t in G._tokens(texto) if t.startswith("#")}
    for w in __import__("re").findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]+", texto):
        if G._norm(w) in _EQUIV:
            out.add(_EQUIV[G._norm(w)])
    return out


def sondas_agosto(sondas: list[dict], fuentes: list[dict], juez) -> list[dict]:
    """Afirmaciones del video de agosto: se tratan como SONDAS, nunca como fuente. NLI multilingue
    contra los pasajes de cada fuente que cuenta; se informa el mejor resultado."""
    out = []
    for s in sondas:
        mejor = {"clasificacion": "SIN_EVIDENCIA", "fragmento": "", "url": "", "puntajes": None}
        for f in fuentes:
            if f["tipo"] not in CUENTAN or not f.get("texto_pasajes"):
                continue
            try:
                v = (juez(f["texto_pasajes"], [s["texto"]]) or [{}])[0]
            except G.JuezNoDisponible:
                continue
            rango = {"CONTRADICHA": 2, "RESPALDADA": 1}.get(v.get("clasificacion"), 0)
            if rango > {"CONTRADICHA": 2, "RESPALDADA": 1}.get(mejor["clasificacion"], 0):
                mejor = {"clasificacion": v["clasificacion"], "fragmento": v.get("fragmento", ""),
                         "url": f["url"], "puntajes": v.get("puntajes")}
        a = anclas(s["texto"])
        frag = G._norm(mejor.get("fragmento") or "")
        comparte = sorted(x for x in a if G._norm(x) in frag or x in {t[1:] for t in G._tokens(frag)
                                                                       if t.startswith("#")})
        if mejor["clasificacion"] != "SIN_EVIDENCIA" and not comparte:
            mejor["veredicto_nli"] = mejor["clasificacion"]
            mejor["clasificacion"] = "SIN_RELACION — revisar a mano"
        out.append({**s, **mejor, "anclas": sorted(a), "anclas_compartidas": comparte})
    return out


# ── orquestacion ──────────────────────────────────────────────────────────
def correr(caso_json: Path, *, llm=None, juez=None, buscar=None, obtener_fn=obtener, extraer=None) -> dict:
    caso = json.loads(Path(caso_json).read_text(encoding="utf-8"))
    cid = caso["caso"]
    log = Bitacora(BASE / "logs" / f"investigacion_{cid}_{datetime.now():%Y%m%d}.log")
    log.linea(f"=== corrida {cid}: {caso['pregunta']}")
    exp = E.nuevo(cid, datetime.now().strftime("%Y-%m-%d"))
    exp["pregunta_central"] = caso["pregunta"]
    exp["intervenciones_humanas"] = list(caso.get("intervenciones_humanas", []))

    if buscar is None:
        from telegram_bot.investigar_web import buscar  # noqa: F811
    if extraer is None:
        from synthesis import dossier_extraccion as D
        extraer = D.extraer
    if llm is None:
        from synthesis import dossier_extraccion as D
        llm = D.llm_ollama("http://localhost:11434", caso.get("modelo", "qwen2.5:7b-instruct-q4_K_M"))
    if juez is None:
        from synthesis.juez_nli import juez_nli
        juez = juez_nli()

    # 1) fuentes: semillas + busqueda (cada busqueda queda en la bitacora)
    urls: dict[str, dict] = {}
    for s in caso.get("semillas", []):
        urls.setdefault(s["url"], {"url": s["url"], "origen": "semilla", "nota": s.get("nota", "")})
    with log.etapa("busqueda"):
        for q in caso.get("consultas", []):
            res = buscar(q, caso.get("resultados_por_consulta", 6))
            if not any(r.get("url") for r in res):     # DuckDuckGo limita rafagas: una espera y un reintento
                time.sleep(caso.get("espera_busqueda_s", 30))
                res = buscar(q, caso.get("resultados_por_consulta", 6))
            time.sleep(caso.get("pausa_entre_busquedas_s", 8))
            vistos = [r["url"] for r in res if r.get("url")]
            E.registrar_busqueda(exp, q, "duckduckgo-html", len(vistos), vistos,
                                 "; ".join(r["error"] for r in res if r.get("error")))
            for u in vistos:
                urls.setdefault(u, {"url": u, "origen": f"busqueda: {q}"})
    fuentes = []
    for u, f in urls.items():
        f["tipo"] = tipo_fuente(u)
        f["medio"] = urlparse(u).netloc.replace("www.", "")
        fuentes.append(f)

    # 2) extraccion (solo las que pueden contar; pistas se registran sin extraer)
    with log.etapa("extraccion"):
        max_fuentes = caso.get("max_fuentes_prensa", 6)
        n_prensa = 0
        for f in fuentes:
            if f["tipo"] not in CUENTAN:
                f["estado"] = "pista_no_cuenta"
                continue
            if f["tipo"] == "prensa":
                if n_prensa >= max_fuentes:
                    f["estado"] = "no_leida_por_limite"
                    continue
                n_prensa += 1
            r = obtener_fn(f["url"])
            f.update({k: v for k, v in r.items() if k != "reg"})
            f["_reg"] = r.get("reg")
            log.linea(f"  fuente {f['tipo']:9s} {r['estado']:18s} {f['url'][:100]}")
    validas = [f for f in fuentes if f.get("estado") == "ok"]

    # 3) pasajes
    with log.etapa("pasajes"):
        for f in validas:
            maximo = caso.get("pasajes_primaria", 12) if f["tipo"] == "primaria" else caso.get("pasajes_prensa", 4)
            f["pasajes"], f["cobertura"] = pasajes(f["_reg"], caso["palabras_caso"], caso["subpreguntas"], maximo)
            f["texto_pasajes"] = " ".join(p["texto"] for p in f["pasajes"])
            log.linea(f"  {f['medio']}: {len(f['pasajes'])} pasajes; paginas con el caso sin leer: "
                      f"{f['cobertura']['paginas_con_el_caso_no_leidas'][:20]}")

    # 4-5) afirmaciones + fidelidad + veracidad
    claims, rechazados, llamadas = [], 0, 0
    with log.etapa("afirmaciones"):
        for f in validas:
            for p in f["pasajes"]:
                r = extraer({"source_url": f["url"], "segmentos": [{"texto": p["texto"]}]}, llm, juez,
                            medio=f["medio"], max_reintentos=caso.get("reintentos", 1))
                llamadas += r.get("llamadas", 0)
                rechazados += len(r.get("rechazados", []))
                for c in r.get("claims", []):
                    cita = c["fuentes"][0]["cita_textual"]
                    claim = {"afirmacion": c["afirmacion"], "tipo_enunciado": c.get("tipo_enunciado", "hecho"),
                             "atribuido_a": c.get("atribuido_a", ""), "subpreguntas": p["temas"],
                             "fuentes": [{"url": f["url"], "medio": f["medio"], "tipo_fuente": f["tipo"],
                                          "cita_textual": cita, "pagina": p["pagina"],
                                          "procesado": f.get("json")}],
                             "observable_publicamente": False}
                    claim["fidelidad"] = fidelidad(cita, f["_reg"], p["seg"])
                    claim["veracidad"] = veracidad(claim["afirmacion"], cita, juez)
                    claims.append(claim)
        log.linea(f"  {len(claims)} afirmaciones aceptadas por el extractor, {rechazados} rechazadas, "
                  f"{llamadas} llamadas al modelo")

    # 6) contraste + certeza
    with log.etapa("contraste"):
        for c in claims:
            if c["fidelidad"]["ok"] and c["veracidad"]["clasificacion"] == G.RESPALDADA:
                contrastar(c, validas, juez)
            c["nivel_certeza"] = certeza(c)
            E.upsert_claim(exp, c, recalcular_certeza=False)
    claims = exp["claims"]

    # 7) cronologia
    exp["cronologia"] = cronologia(claims)

    # 8) sondas
    with log.etapa("sondas"):
        exp["sondas_mutadas"] = sondas_mutadas(claims, juez, caso.get("max_mutaciones", 60))
        exp["sondas_agosto"] = sondas_agosto(caso.get("sondas", []), validas, juez)

    exp["fuentes_detalle"] = [{k: v for k, v in f.items() if not k.startswith("_") and k != "texto_pasajes"}
                              for f in fuentes]
    exp["preguntas_abiertas"] = caso.get("preguntas_abiertas", [])
    exp["metricas"] = {"etapas": log.etapas, "llamadas_llm": llamadas, "rechazados_extractor": rechazados,
                       "segundos_automaticos": round(sum(e["segundos"] for e in log.etapas), 1)}
    exp["criterios"] = criterios(exp)
    exp["estado"] = "COMPLETO" if all(v["ok"] for k, v in exp["criterios"].items() if k != "A8") \
        else "INCOMPLETO"
    if exp["sondas_mutadas"]["aceptadas_falsas"]:
        exp["estado"] = "EXPERIMENTAL — el verificador aceptó afirmaciones falsas"
    E.guardar(exp)
    md = E.ruta(cid).parent / "EXPEDIENTE.md"
    md.write_text(informe(exp), encoding="utf-8")
    log.linea(f"=== estado {exp['estado']} · {md}")
    return exp


# ── criterios A1-A9 ───────────────────────────────────────────────────────
def criterios(exp: dict) -> dict:
    fd = exp.get("fuentes_detalle", [])
    cl = exp["claims"]
    validos = [c for c in cl if c["nivel_certeza"] != "NO-CONFIRMADO"]
    prim = [f for f in fd if f["tipo"] == "primaria" and f.get("estado") == "ok"]
    sin_fecha = [e for e in exp["cronologia"] if not e.get("claim_id")]
    num = re.compile(r"\d")
    cifras_sin = [c["id"] for c in validos
                  if num.search(c["afirmacion"]) and not num.search(c["fuentes"][0]["cita_textual"])]
    sondas = exp.get("sondas_agosto", [])
    return {
        "A1": {"ok": bool(prim), "valor": f"{len(prim)} fuente(s) primaria(s) extraída(s)"},
        "A2": {"ok": bool(cl) and all(c["fidelidad"]["ok"] for c in validos),
               "valor": f"{sum(c['fidelidad']['ok'] for c in cl)}/{len(cl)} citas literales en su página"},
        "A3": {"ok": not cifras_sin, "valor": f"{len(cifras_sin)} cifras sin cita que las contenga"},
        "A4": {"ok": len(exp["cronologia"]) >= 8 and not sin_fecha,
               "valor": f"{len(exp['cronologia'])} eventos con fecha literal"},
        "A5": {"ok": bool(sondas), "valor": f"{len(sondas)} afirmaciones de agosto clasificadas"},
        "A6": {"ok": True, "valor": f"{sum(len(c.get('posibles_contradicciones', [])) for c in cl)} posibles"},
        "A7": {"ok": True, "valor": "material de terceros ROJO por defecto; propio VERDE (ver sección 8)"},
        "A8": {"ok": False, "valor": "no evaluable: esta etapa no genera guion (fuera de alcance)"},
        "A9": {"ok": True, "valor": f"{exp['metricas']['segundos_automaticos']} s automáticos; "
                                    f"{len(exp.get('intervenciones_humanas', []))} intervenciones humanas"},
    }


# ── EXPEDIENTE.md (10 secciones) ──────────────────────────────────────────
def informe(exp: dict) -> str:
    L = [f"# Expediente: {exp['caso_id']}", "",
         f"**Estado:** {exp['estado']} · corte {exp['fecha_corte']} · completitud {exp['completitud']}",
         "", "> Generado por `scripts/investigar_tema.py`. Fidelidad = la cita está literal en la página "
         "citada. Veracidad = la cita dice lo que afirma la afirmación (NLI + cifras). Son revisiones "
         "distintas; la certeza solo sube si pasan las dos.", ""]
    L += ["## 1. Pregunta central", "", exp.get("pregunta_central", ""), ""]
    fd = exp.get("fuentes_detalle", [])
    L += ["## 2. Fuente primaria", ""]
    prim = [f for f in fd if f["tipo"] == "primaria"]
    L += [f"- [{f.get('estado')}] {f['url']}" + (f" — {f.get('motivo')}" if f.get("motivo") else "")
          for f in prim] or ["- **No se obtuvo ninguna fuente primaria.**"]
    for f in prim:
        cob = f.get("cobertura")
        if cob:
            L.append(f"  - Páginas que nombran el caso: {len(cob['paginas_con_el_caso'])}; leídas por el "
                     f"extractor: {cob['elegidos']} pasajes; páginas con el caso **no leídas**: "
                     f"{cob['paginas_con_el_caso_no_leidas']}")
    L += ["", "## 3. Fuentes de contraste y pistas", "", "| Tipo | Estado | Fuente | Nota |", "|---|---|---|---|"]
    for f in fd:
        if f["tipo"] == "primaria":
            continue
        L.append(f"| {f['tipo']} | {f.get('estado', '')} | {f['url'][:90]} | {(f.get('motivo') or f.get('origen', ''))[:70]} |")
    L += ["", "Wikipedia y blogs son pistas: no cuentan como fuente.", "", "### Bitácora de búsquedas", ""]
    L += [f"- «{b['consulta']}» → {b['n_resultados']} resultados" + (f" ({b['limitaciones']})" if b['limitaciones'] else "")
          for b in exp.get("bitacora_busquedas", [])]
    L += ["", "## 4. Cronología", "", "| Fecha (literal en la cita) | Hecho | Certeza | Claim |", "|---|---|---|---|"]
    L += [f"| {e['fecha']} | {e['hecho'][:120]} | {e['certeza']} | {e['claim_id']} |" for e in exp["cronologia"]]
    L += ["", "## 5. Afirmaciones y cifras con su evidencia", "",
          "| Claim | Afirmación | Fidelidad | Veracidad | Fuentes indep. | Certeza | Cita (pág.) |", "|---|---|---|---|---|---|---|"]
    for c in exp["claims"]:
        f0 = c["fuentes"][0]
        L.append(f"| {c['id']} | {c['afirmacion'][:110]} | {'✔' if c['fidelidad']['ok'] else '✘'} | "
                 f"{c['veracidad']['clasificacion']} | {E.fuentes_independientes(c)} | {c['nivel_certeza']} | "
                 f"«{f0['cita_textual'][:90]}» (p. {f0.get('pagina')}, {f0['medio']}) |")
    L += ["", "## 6. Contradicciones o diferencias entre fuentes", ""]
    contr = [(c, x) for c in exp["claims"] for x in c.get("posibles_contradicciones", [])]
    L += [f"- {c['id']} «{c['afirmacion'][:90]}» vs {x['medio']}: «{x['fragmento'][:110]}» — {x['nota']}"
          for c, x in contr] or ["- Ninguna detectada automáticamente (no prueba que no existan)."]
    L += ["", "## 7. Confirmado · parcial · no confirmado", ""]
    from collections import Counter
    L += [f"- {k}: {v}" for k, v in Counter(c["nivel_certeza"] for c in exp["claims"]).items()]
    L += ["", "### Afirmaciones del video de agosto (sondas, nunca fuente)", "",
          "| Afirmación de agosto | Resultado NLI | Fragmento | Fuente |", "|---|---|---|---|"]
    L += [f"| {s['texto'][:90]} | {s['clasificacion']} | «{(s.get('fragmento') or '')[:90]}» | {s.get('url', '')[:50]} |"
          for s in exp.get("sondas_agosto", [])]
    sm = exp.get("sondas_mutadas", {})
    L += ["", f"### Sondas falsas por mutación: {sm.get('aceptadas_falsas')} aceptadas de {sm.get('pruebas')} "
          f"(tasa {sm.get('tasa')})", ""]
    L += [f"- ACEPTADA FALSA: {p['claim_id']} ({p['tipo']}): {p['mutacion'][:120]}"
          for p in sm.get("detalle", []) if p["resultado"] == G.RESPALDADA]
    L += ["", "## 8. Material audiovisual y derechos", "",
          "- Material de terceros (fotos, video, documentos escaneados de medios): **ROJO** por defecto; "
          "público no es reutilizable. Requiere licencia o permiso.",
          "- Material propio (animación 3D o pizarra que reconstruye hechos de la sección 5 con su claim): **VERDE**.",
          "- Documentos oficiales (p. ej. páginas de la acusación) como cita en pantalla: **AMARILLO** — "
          "revisar uso como cita editorial.", ""]
    L += ["## 9. Ángulo narrativo (propuesta, requiere aprobación humana)", ""]
    fuertes = sorted([c for c in exp["claims"] if c["nivel_certeza"] in ("CONFIRMADO", "RESPALDADO")],
                     key=lambda c: -E.fuentes_independientes(c))[:3]
    L += [f"- Dato fuerte: «{c['afirmacion'][:140]}» ({c['id']}, {E.fuentes_independientes(c)} fuente(s))"
          for c in fuertes] or ["- Sin datos respaldados suficientes para proponer un ángulo."]
    L += ["", "## 10. Escenas y guion sustentado", "",
          "Una escena por evento de la cronología, cada una atada a su claim. **El guion no se genera en esta "
          "etapa**: requiere el validador previo al motor (paso 5 del plan), no autorizado todavía.", ""]
    L += [f"- Escena {i}: {e['fecha']} — {e['hecho'][:110]} [{e['claim_id']}]"
          for i, e in enumerate(exp["cronologia"], 1)]
    L += ["", "## Preguntas abiertas", ""] + [f"- {p}" for p in exp.get("preguntas_abiertas", [])]
    L += ["", "## Criterios de aceptación A1–A9", "", "| | ¿Cumple? | Valor |", "|---|---|---|"]
    L += [f"| {k} | {'sí' if v['ok'] else 'NO'} | {v['valor']} |" for k, v in exp["criterios"].items()]
    L += ["", "## Tiempo y trabajo humano", ""]
    L += [f"- {e['etapa']}: {e['segundos']} s" for e in exp["metricas"]["etapas"]]
    L += [f"- Intervención humana: {h}" for h in exp.get("intervenciones_humanas", [])]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) < 2:
        sys.exit("Uso: python scripts/investigar_tema.py expediente/<caso>/caso.json")
    r = correr(Path(sys.argv[1]))
    print(json.dumps({"estado": r["estado"], "claims": len(r["claims"]),
                      "criterios": {k: v["ok"] for k, v in r["criterios"].items()}}, ensure_ascii=False))
