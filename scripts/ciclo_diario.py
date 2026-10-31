# -*- coding: utf-8 -*-
"""CICLO DIARIO de la fábrica: investiga tendencias → produce reels → revisa narración → Telegram.

Encadena piezas YA PROBADAS (no reimplementa):
  1) content_factory (senales → clasificar → score → seleccionar → informe), base de PRUEBA.
  2) El LLM local convierte cada tendencia en un tema de reto de Python (o la descarta).
  3) telegram_bot.producir_reel.producir(tema): código EJECUTADO (salida real) + voz documental.
  4) scripts/verificar_narracion_reel.verificar(): el juez local revisa la narración contra la salida.
  5) Telegram: resumen de tendencias del día + cada reel con botones [YouTube][Facebook][Ambos][No].

NO PUBLICA SOLO. Calibración del juez (15-oct-2026): 5/8 aciertos, dejó pasar 2 de 4 narraciones
falsas → la publicación la decide el operador con un toque en Telegram. El veredicto viaja como aviso.

Candado `logs/_fabrica.lock`: si el bot u otra corrida está produciendo, este ciclo no arranca
(GPU y Ollama compartidos; el 14b se cae por tiempo cuando compiten).

Uso:  python scripts/ciclo_diario.py [--n 4] [--sin-tendencias]
Registro: docs/operations/evidencia/ciclo_diario_20261015/registro.json · Log: logs/ciclo_diario_AAAAMMDD.log
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
from telegram_bot import candado  # noqa: E402

EVID = REPO / "docs/operations/evidencia/ciclo_diario_20261015"
REGISTRO = EVID / "registro.json"
LOTE_14OCT = REPO / "docs/operations/evidencia/tendencias_20261014/lote_reels_tendencias.json"
LOG = REPO / "logs" / ("ciclo_diario_%s.log" % datetime.now().strftime("%Y%m%d"))
PY = sys.executable


def log(msg: str) -> None:
    linea = "%s %s" % (datetime.now().strftime("%H:%M:%S"), msg)
    print(linea, flush=True)
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(linea + "\n")


def cargar_env() -> None:
    envp = REPO / ".env"
    if envp.is_file():
        for ln in envp.read_text(encoding="utf-8-sig").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def telegram(texto: str) -> None:
    import requests
    tok = os.environ.get("VIDEOS_TXT_TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("VIDEOS_TXT_TELEGRAM_CHAT_ID", "").strip()
    if not tok or not chat:
        log("TELEGRAM: falta token o chat_id; no se envía"); return
    for i in range(0, len(texto), 3900):
        try:
            requests.post("https://api.telegram.org/bot%s/sendMessage" % tok, timeout=60,
                          data={"chat_id": chat, "text": texto[i:i + 3900], "disable_web_page_preview": "true"})
        except Exception as e:
            log("TELEGRAM: error %s" % type(e).__name__)  # nunca se imprime la URL (lleva el token)


# ── 1) tendencias ──────────────────────────────────────────────────────────
def tendencias(fecha: str, produccion=False) -> list[dict]:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    extras = ["--produccion", "--db", str(REPO / "content_factory/content.db")] if produccion else []
    for etapa in ("senales", "clasificar", "score", "seleccionar"):
        r = subprocess.run([PY, "-m", "content_factory.cli", etapa, "--fecha-corte", fecha] + extras,
                           cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8", timeout=3600)
        log("tendencias %s: exit=%d %s" % (etapa, r.returncode, (r.stdout.strip().splitlines() or [""])[-1][:120]))
        if r.returncode != 0 and not (etapa == "seleccionar" and "DecisionYaMaterializada" in r.stderr):
            log("tendencias: %s" % r.stderr.strip()[-300:])
            return []
    r = subprocess.run([PY, "-m", "content_factory.cli", "informe", "--fecha-corte", fecha] + extras,
                       cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8", timeout=600)
    try:
        temas = json.loads(r.stdout).get("temas", [])
    except Exception:
        log("tendencias: informe no es JSON (%s)" % r.stderr.strip()[-200:]); return []
    (EVID / ("top10_%s.json" % fecha)).write_text(r.stdout, encoding="utf-8")
    return temas


# ── 2) tendencia → tema de reto ────────────────────────────────────────────
def a_tema(titulo: str) -> str | None:
    import requests
    from telegram_bot import producir_reel as pr
    # Con «responde NO si no da» el 14b descartó 49 de 49 (15-oct, incluso el ejemplo de TRIM). Con una bandera
    # separada «tecnico» + tema SIEMPRE, clasificó bien 6 de 6 en la prueba del mismo día.
    sis = ("Eres editor de reels educativos de Python. Recibes el titular de una tendencia. Tarea:\n"
           "1) tecnico: true si el titular menciona una herramienta, fórmula, lenguaje, algoritmo o técnica "
           "(Excel, PowerShell, SQL, NumPy, modelos de IA, hardware…); false si es opinión, chisme, queja o publicidad.\n"
           "2) tema: SIEMPRE propone un reto de Python de 3 a 7 líneas (solo cómputo con la biblioteca estándar: "
           "strings, listas, dicts, math, hashlib, bisect, sqlite3 en memoria; imprime un resultado) que enseñe el "
           "CONCEPTO del titular. Ejemplos: 'BUSCARV en Excel' -> 'buscar el precio de un producto en un diccionario "
           "con dict.get'; 'espacio que TRIM no quita' -> 'quitar el espacio duro con str.replace y str.strip'.\n"
           'Responde SOLO JSON: {"tecnico": true/false, "tema": "frase en español"}')
    try:
        r = requests.post(pr.OLLAMA, timeout=300, json={"model": pr.MODELO, "stream": False, "format": "json",
                          "messages": [{"role": "system", "content": sis}, {"role": "user", "content": titulo}]})
        d = json.loads(r.json()["message"]["content"])
        t = str(d.get("tema", "")).strip()
        return t if d.get("tecnico") is True and t else None
    except Exception:
        return None


def candidatos_db(fecha: str, limite: int = 60, produccion=False) -> list[dict]:
    """Más allá del top 10: temas técnicos (P2 IA, P3 investigación, P4 herramientas) del día, por puntaje final.
    Solo lectura de la base de prueba."""
    import sqlite3
    db = REPO / "content_factory" / ("content.db" if produccion else "content.test.db")
    try:
        c = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
        filas = c.execute("SELECT t.titulo, t.pilar, s.final FROM scores s JOIN temas t ON t.id = s.tema_id "
                          "WHERE s.fecha = ? AND t.pilar IN ('P2','P3','P4') ORDER BY s.final DESC LIMIT ?",
                          (fecha, limite)).fetchall()
        c.close()
    except Exception as e:
        log("candidatos_db: %s" % e); return []
    return [{"titulo": t, "pilar": p, "score": round(f, 3)} for t, p, f in filas]


def ya_usadas() -> set:
    usadas = set()
    for p in (REGISTRO, LOTE_14OCT):
        if p.is_file():
            # solo bloquea lo YA PRODUCIDO; descartadas y fallidas se reintentan (las 3 pruebas del 15-oct fallaron
            # por un defecto del productor, no por el tema)
            usadas |= {h.get("senal") for h in json.loads(p.read_text(encoding="utf-8")) if h.get("eid")}
    return usadas


# ── ciclo ──────────────────────────────────────────────────────────────────
def main() -> int:
    global EVID, LOG
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=4, help="reels a producir hoy")
    ap.add_argument("--sin-tendencias", action="store_true", help="usa el top10 ya guardado de hoy")
    ap.add_argument("--automatico", action="store_true", help="usa cola real y autorización delegada configurada")
    ap.add_argument("--solo-producir", action="store_true", help="verifica el recorrido automático sin publicar")
    a = ap.parse_args()
    cargar_env()
    if a.automatico:
        import uuid
        from content_factory import automatico
        politica = automatico.politica()
        a.n = min(a.n, politica["videos_por_dia"])
        EVID = REPO / "runs" / ("ciclo_auto_" + datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8])
        LOG = EVID / "ciclo.log"
    EVID.mkdir(parents=True, exist_ok=True)
    fecha = date.today().isoformat()

    with candado.tomar("ciclo_diario") as ok:
        if not ok:
            log("OCUPADO: %s; no arranco" % candado.quien()); return 2
        log("=== CICLO %s (n=%d) ===" % (fecha, a.n))
        if a.automatico:
            import sqlite3
            from content_factory import db as factory_db
            c = factory_db.conectar(REPO / "content_factory/content.db", produccion=True)
            pendientes = automatico.reconciliar_pendientes(c, datetime.now().astimezone().isoformat())
            c.close()
            if pendientes:
                log("BLOQUEADO: publicación incierta; no se vuelve a subir ni se añade otra")
                return 1
            c = sqlite3.connect("file:%s?mode=ro" % (REPO / "content_factory/content.db").as_posix(), uri=True)
            ocupados = c.execute("SELECT COUNT(DISTINCT pieza_id) FROM publicaciones WHERE substr(publicada_en,1,10)=?", (fecha,)).fetchone()[0]
            c.close()
            a.n = max(0, a.n - ocupados)
            if not a.n:
                log("CUPO diario ya usado o reservado; no producir ni publicar de nuevo")
                return 0
        guardado = EVID / ("top10_%s.json" % fecha)
        temas = (json.loads(guardado.read_text(encoding="utf-8")).get("temas", [])
                 if a.sin_tendencias and guardado.is_file() else tendencias(fecha, produccion=a.automatico))
        resumen = ["🔎 Tendencias del %s (motor de temas, base de prueba)" % fecha]
        for t in temas[:10]:
            url = ((t.get("evidencia") or [{}])[0] or {}).get("url", "")
            resumen.append("%d. [%s %.2f] %s\n   %s" % (t.get("posicion", 0), t.get("pilar", ""), t.get("score", 0),
                                                       t.get("titulo", "")[:110], url))
        if not a.automatico:
            telegram("\n".join(resumen) if temas else "🔎 Tendencias %s: el motor no devolvió temas (ver log)." % fecha)
        elif not temas:
            log("BLOQUEADO: selector sin temas; no se inventan fuentes")
            return 1

        from telegram_bot import producir_reel as pr
        from telegram_bot import reels_review as rr
        from verificar_narracion_reel import verificar
        registro = json.loads(REGISTRO.read_text(encoding="utf-8")) if REGISTRO.is_file() else []
        usadas, hechos = ya_usadas(), 0
        vistos, cola = set(), []
        for t in list(temas) + candidatos_db(fecha, produccion=a.automatico):
            if a.automatico and t.get("pilar") not in politica["pilares"]:
                continue
            if t.get("titulo") and t["titulo"] not in vistos:
                vistos.add(t["titulo"]); cola.append(t)
        for t in cola:
            if hechos >= a.n:
                break
            senal = t.get("titulo", "")
            if senal in usadas:
                continue
            tema = a_tema(senal)
            fila = {"fecha": fecha, "senal": senal, "pilar": t.get("pilar"), "score": t.get("score"), "tema": tema}
            if not tema:
                fila["descartada"] = "no da para reto de Python"; registro.append(fila)
                log("DESCARTO: %s" % senal[:80])
            else:
                t0 = time.time()
                r = pr.producir(tema)
                fila.update(r if isinstance(r, dict) else {"error": "sin respuesta"})
                fila["segundos"] = round(time.time() - t0)
                if r.get("eid"):
                    e = {x["id"]: x for x in json.loads(pr.BANCO.read_text(encoding="utf-8"))}[r["eid"]]
                    fila["juez"] = verificar(e["code"], e["output"], e["narracion"])
                    if a.automatico:
                        carpeta = EVID / r["eid"]
                        carpeta.mkdir()
                        (carpeta / "produccion.json").write_text(json.dumps({"resultado":r,"tema":t,"guion":e,"juez":fila["juez"]},ensure_ascii=False,indent=2),encoding="utf-8")
                        if fila["juez"]["veredicto"] != "OK":
                            fila["publicacion"] = {"resultado":"bloqueada_por_narracion"}
                        elif a.solo_producir:
                            fila["publicacion"] = {"resultado":"solo_producir", "tecnica":automatico.comprobar_video(r["mp4"],politica)}
                        else:
                            fila["publicacion"] = automatico.registrar_y_publicar(r,t,fila["juez"],carpeta,
                                fecha=datetime.now().astimezone().isoformat())
                        log("DISTRIBUCION %s: %s" % (r["eid"],fila["publicacion"]["resultado"]))
                    else:
                        rr.enviar_reel_con_botones(r["eid"])
                        telegram("🧪 %s «%s» — juez: %s (%s)\nTendencia: %s\nNarración: %s\nSalida real: %s\n"
                             "⚠️ Juez medido el 21-oct con solo 8 casos: dejó pasar 1 de 4 narraciones malas. Revisa que la voz diga lo mismo que la salida."
                             % (r["eid"], r.get("titulo", ""), fila["juez"]["veredicto"], fila["juez"]["motivo"],
                                senal[:100], e["narracion"], e["output"][:300]))
                    hechos += 1
                    log("OK %s %s juez=%s (%ds)" % (r["eid"], r.get("titulo"), fila["juez"]["veredicto"], fila["segundos"]))
                else:
                    log("FALLO %s: %s" % (senal[:60], str(r.get("error"))[:120]))
                registro.append(fila)
            REGISTRO.write_text(json.dumps(registro, ensure_ascii=False, indent=1), encoding="utf-8")
        if not a.automatico:
            telegram("🏭 Ciclo %s terminado: %d reels nuevos para revisar. Publica con los botones de cada video." % (fecha, hechos))
        log("=== FIN: %d reels ===" % hechos)
        if a.automatico and not hechos:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
