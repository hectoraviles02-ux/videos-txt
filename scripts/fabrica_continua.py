# -*- coding: utf-8 -*-
"""Fábrica continua de VIDEOS-TXT — pedido de operador (22-oct-2026): «la máquina de videos nunca debe estar
quieta y eso debe vivir en código»; las URL que él manda por Telegram tienen PRIORIDAD; se intercalan
Python, matemáticas e investigación.

No es un motor nuevo: solo decide QUÉ producir y llama a lo que ya existe.
  python        -> telegram_bot.producir_reel.producir (código ejecutado, salida real) + juez de narración
  matematicas   -> scripts/producir_pieza.py sobre un guion en cola (guiones/pizarra/<pieza>/guion.json)
  investigacion -> scripts/producir_pieza.py sobre un expediente en cola (guiones/expedientes/<pieza>/ con APROBADO)
Todo termina en Telegram para revisión. NADA se publica solo (R27: solo el botón APROBAR publica).

Prioridad de operador: mientras el bot tenga una URL en curso (bitácora con PENDIENTE sin cierre) o la fábrica
esté ocupada, esta fábrica espera. Entre pieza y pieza suelta el candado.
Rotación: ROTACION abajo. Si no hay guion en cola para matemáticas o investigación, hace Python: nunca queda quieta.
Detener: crear logs/DETENER_FABRICA (termina al acabar la pieza en curso) o cerrar el proceso.
Arranque: scripts/arrancar_fabrica.cmd (no es tarea programada; si la PC se reinicia, se vuelve a arrancar).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from review_channels.telegram_channel import _load_env  # noqa: E402

_load_env()
from telegram_bot import candado  # noqa: E402

LOG = REPO / "logs" / "fabrica_continua.log"
ESTADO = REPO / "logs" / "fabrica_continua_estado.json"
DETENER = REPO / "logs" / "DETENER_FABRICA"
BITACORA = REPO / "logs" / "bitacora.jsonl"
PAUSA_ENTRE_PIEZAS_S = 45
# operador, 24-oct: «no estamos solo en Python» → matemáticas (pizarra) cada tercer turno; la cola la llena
# scripts/pizarra/escritor.py. Si no hay guion en cola, hace Python (nunca queda quieta).
ROTACION = ["python", "python", "matematicas", "python", "python", "python", "python", "matematicas", "python",
            "python"]   # 80 % Python (60 principiante + 20 real) · 20 % matemáticas; expedientes en pausa (disco C:)
# Un expediente 3D tiene la fábrica HORAS (render Blender). Solo de madrugada, para no bloquear las URL de operador.
VENTANA_RENDER_LARGO = (0, 7)      # de 00:00 a 06:59
HORARIO_FABRICA = (19, 7)          # operador, 23-oct: la fábrica produce de 19:00 a 06:59; de día espera


def en_horario(h: int) -> bool:
    ini, fin = HORARIO_FABRICA
    return ini <= h or h < fin if ini > fin else ini <= h < fin

# operador, 24-oct: «menos matemáticos, más simples para la raza». Medido en TikTok Studio (24-oct): retos básicos del
# 18–21 oct (for, if, tabla de multiplicar) mediana 192 vistas; avanzados del 22–24 oct (coseno, SHA-256) mediana 13.
# Estos van PRIMERO, en nivel principiante y con ejemplo de la vida diaria.
# Experimento de 14 días (operador, 24-oct): 60 % Python principiante · 20 % Python de problemas reales · 20 % matemáticas
# de bolsillo (pizarra). Cada pieza se mide a las 72 h (retención, compartidos, seguidores). Python: 3 principiante : 1 real.
PRINCIPIANTE = [
    "adivina el número: el programa piensa un número y da pistas",
    "calificación aprobada o reprobada con if",
    "tabla de multiplicar de cualquier número con for",
    "por qué 0.1 + 0.2 no da 0.3 en Python",
    "contar cuántas veces aparece tu nombre en un texto",
    "piedra, papel o tijera contra la computadora",
    "contraseña segura: revisar largo, números y mayúsculas",
    "cuántos segundos llevas vivo a partir de tu edad",
    "quién paga la cuenta: sorteo con random.choice entre amigos",
]
PROBLEMAS_REALES = [
    "repartir la cuenta del restaurante entre varias personas con propina",
    "convertir pesos a dólares con un tipo de cambio de ejemplo",
    "lista del súper: agregar, quitar y ver el total a pagar",
    "cuánto ahorras al año guardando un poco cada día",
    "cuántos días faltan para tu cumpleaños con datetime",
    "calcular tu IMC con peso y estatura de ejemplo",
]
TEMAS_SENCILLOS = []
for _k in range(max(len(PRINCIPIANTE), 3 * len(PROBLEMAS_REALES))):   # intercala 3 de principiante por 1 real
    if _k < len(PRINCIPIANTE):
        TEMAS_SENCILLOS.append(PRINCIPIANTE[_k])
    if _k % 3 == 2 and _k // 3 < len(PROBLEMAS_REALES):
        TEMAS_SENCILLOS.append(PROBLEMAS_REALES[_k // 3])
TEMAS_SENCILLOS += [t for t in PROBLEMAS_REALES if t not in TEMAS_SENCILLOS]
TEMAS_PYTHON = TEMAS_SENCILLOS + [
    "generadores con yield para producir una secuencia bajo demanda",
    "decorador simple que cuenta cuántas veces se llama una función",
    "closures: una función que recuerda un contador",
    "*args y **kwargs para una función flexible",
    "sorted con key y lambda para ordenar diccionarios por dos criterios",
    "collections.Counter para contar las palabras más frecuentes",
    "collections.defaultdict para agrupar elementos por categoría",
    "namedtuple para representar puntos con nombre",
    "dataclasses para una clase Producto con valores por defecto",
    "itertools.groupby para agrupar elementos consecutivos",
    "itertools.combinations para todas las parejas posibles",
    "functools.lru_cache para acelerar fibonacci recursivo",
    "desempaquetado extendido con asterisco en listas",
    "operador morsa := dentro de una comprehension",
    "comprehension de diccionario invirtiendo llaves y valores",
    "comprehension anidada para aplanar una matriz",
    "slicing con paso negativo para invertir y saltar elementos",
    "any y all para validar una lista de condiciones",
    "try except else finally y en qué orden se ejecutan",
    "context manager con una clase y __enter__ __exit__",
    "__repr__ y __str__ en una clase propia",
    "property para validar un atributo de una clase",
    "herencia y super() para extender un método",
    "classmethod como constructor alternativo",
    "iterador propio con __iter__ y __next__",
    "dict.get y setdefault para evitar KeyError",
    "unir diccionarios con el operador |",
    "match case para clasificar comandos",
    "bisect para insertar en una lista ordenada",
    "heapq para obtener los tres valores más grandes",
    "copy frente a deepcopy con listas anidadas",
    "is frente a == con listas iguales",
    "el error del argumento por defecto mutable",
    "expresión generadora frente a lista en sum",
    "enumerate con start y zip para recorrer dos listas",
    "nonlocal para modificar una variable de la función externa",
    "str.join frente a concatenar en un ciclo",
    "f-strings con ancho, decimales y separador de miles",
    "map y filter comparados con una comprehension",
    "sets: intersección, unión y diferencia entre dos listas",
    "zip con * para transponer una matriz",
    "sorted con reverse y key=len",
    "recursión para sumar los dígitos de un número",
    "diccionario de funciones como alternativa a muchos if",
    "staticmethod frente a classmethod",
    "__eq__ y __lt__ para comparar objetos propios",
    "functools.reduce para multiplicar una lista",
    "itertools.accumulate para sumas acumuladas",
    "itertools.chain para unir varias listas",
    "collections.deque como cola con rotate",
    "enumerate para encontrar todas las posiciones de un valor",
    "frozenset como llave de diccionario",
    "generador infinito con itertools.count e islice",
    "lambda dentro de max con key",
    "validar un correo con str.partition y comprobaciones",
    "contar vocales con sum y una expresión generadora",
    "matriz identidad con comprehension anidada",
    "invertir palabras de una frase con split y join",
    "palíndromo ignorando espacios y mayúsculas",
    "búsqueda binaria escrita a mano",
]


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    linea = "%s %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(linea + "\n")
    print(linea, flush=True)


def telegram(texto: str, archivo: Path | None = None) -> None:
    from telegram_bot import aprobacion_botones as ab
    chat = os.environ.get("VIDEOS_TXT_TELEGRAM_CHAT_ID", "").strip()
    try:
        if archivo:
            ab._telegram("sendDocument", {"chat_id": chat, "caption": texto[:1000]}, archivo)
        else:
            ab._telegram("sendMessage", {"chat_id": chat, "text": texto[:4000], "disable_web_page_preview": "true"})
    except Exception as e:  # noqa: BLE001 - un aviso fallido no detiene la fábrica
        log("aviso Telegram falló: %s" % e)


def _estado() -> dict:
    try:
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"turno": 0, "temas_hechos": [], "piezas_hechas": [], "conteo": {}}


def _guardar(est: dict) -> None:
    tmp = ESTADO.with_suffix(".tmp")
    tmp.write_text(json.dumps(est, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(ESTADO)


# ── prioridad de operador ───────────────────────────────────────────────────
def url_en_curso(ahora: datetime | None = None, ventana_min: int = 45) -> str | None:
    """URL que el bot recibió y todavía no cierra (PENDIENTE sin OK/ERROR después), dentro de la ventana."""
    if not BITACORA.is_file():
        return None
    ahora = ahora or datetime.now(timezone.utc)
    abiertas: dict[str, datetime] = {}
    for linea in BITACORA.read_text(encoding="utf-8").splitlines()[-200:]:
        try:
            r = json.loads(linea)
        except Exception:  # noqa: BLE001
            continue
        if r.get("tipo") != "url":
            continue
        ts = datetime.fromisoformat(r["ts"])
        if r.get("resultado") == "PENDIENTE":
            abiertas[r["entrada"]] = ts
        else:
            abiertas.pop(r.get("entrada"), None)
    for url, ts in abiertas.items():
        if ahora - ts < timedelta(minutes=ventana_min):
            return url
    return None


def esperar_turno() -> bool:
    """Espera mientras operador tenga una URL en curso o la fábrica esté ocupada. False si piden detener."""
    avisado = False
    while True:
        if DETENER.exists():
            return False
        if not en_horario(datetime.now().hour):
            if avisado != "horario":
                log("ESPERO: fuera de horario (produce de %02d:00 a %02d:00)" % HORARIO_FABRICA)
                avisado = "horario"
            time.sleep(60)
            continue
        u = url_en_curso()
        if not u and not candado.ocupado():
            return True
        if avisado is not True:
            log("ESPERO: %s" % ("URL de operador en curso: " + u[:80] if u else "fábrica ocupada: " + candado.quien()))
            avisado = True
        time.sleep(20)


# ── cola de guiones (matemáticas e investigación) ─────────────────────────
def en_cola(serie: str) -> Path | None:
    """Primera pieza con guion.json listo y sin entrega. Expedientes exigen APROBADO (operador aprobó el look)."""
    base = REPO / "guiones" / serie
    for d in sorted(p for p in base.glob("*") if p.is_dir()):
        if not (d / "guion.json").is_file() or (d / "NO_PRODUCIR").exists():
            continue
        if serie == "pizarra" and not any((d / "entrega").glob("*/*.mp4")) \
                and not list(d.glob("MANIFIESTO_NO_APROBADO_*.json")):
            return d
        if serie == "expedientes" and (d / "APROBADO").is_file() and not list(d.glob("*_largo_*.mp4")) \
                and not (d / "FALLO_FABRICA").exists():
            return d
    return None


# ── trabajos ──────────────────────────────────────────────────────────────
def hacer_python(est: dict) -> str:
    from telegram_bot import producir_reel as pr
    from telegram_bot import reels_review as rr
    from verificar_narracion_reel import verificar
    pendientes = [t for t in TEMAS_PYTHON if t not in est["temas_hechos"]]
    if not pendientes:                         # se acabó la lista: otra vuelta pidiendo un ejemplo distinto
        est["temas_hechos"] = []
        est["vuelta"] = est.get("vuelta", 1) + 1
        pendientes = list(TEMAS_PYTHON)
    tema = pendientes[0]
    nivel = "nivel principiante, con un ejemplo de la vida diaria en México" if tema in TEMAS_SENCILLOS else "nivel intermedio"
    sufijo = " (%s%s)" % (nivel, "" if est.get("vuelta", 1) == 1 else ", con un ejemplo distinto al habitual")
    t0 = time.time()
    with candado.tomar("fabrica-python") as libre:
        if not libre:
            return "ocupado"
        r = pr.producir(tema + sufijo)
        if not r.get("eid"):
            est["temas_hechos"].append(tema)
            log("FALLO python «%s»: %s" % (tema, str(r.get("error"))[:150]))
            return "fallo"
        e = {x["id"]: x for x in json.loads(pr.BANCO.read_text(encoding="utf-8"))}[r["eid"]]
        juez = verificar(e["code"], e["output"], e["narracion"])
    est["temas_hechos"].append(tema)
    seg = round(time.time() - t0)
    log("OK python %s «%s» juez=%s (%ds)" % (r["eid"], r["titulo"], juez["veredicto"], seg))
    if juez["veredicto"] == "FALLA":
        telegram("⛔ Python %s «%s»: NO va con botón, la narración no coincide con el código.\nMotivo: %s\n"
                 "Narración: %s\nSalida real: %s" % (r["eid"], r["titulo"], juez["motivo"], e["narracion"],
                                                     e["output"][:300]))
        return "frenado"
    rr.enviar_reel_con_botones(r["eid"])
    subprocess.run([sys.executable, str(REPO / "scripts" / "enviar_tiktok_pendientes.py"), r["eid"]],
                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                   env={**os.environ, "PYTHONIOENCODING": "utf-8"})   # además, botón para TikTok
    telegram("🐍 Python %s «%s» — juez: %s · %ds\nNarración: %s\nSalida real: %s"
             % (r["eid"], r["titulo"], juez["veredicto"], seg, e["narracion"], e["output"][:300]))
    return "ok"


def liberar_gpu_ollama() -> None:
    """24-oct: el juez de Python deja el modelo de Ollama 5 min en la GPU (6.4 GB); la voz de la pizarra pide 6 GB
    libres y el turno se perdía como OCUPADO. Se descarga (keep_alive=0); el siguiente reel de Python lo recarga."""
    import urllib.request
    # 24-oct 23:15: la voz de los reels de Python (voz_documental_servidor.py, Chatterbox) se queda cargada como hijo de
    # esta fábrica y ocupa ~4 GB; la pizarra pedía 6 GB y no cabía. Se cierra; el siguiente reel de Python lo reabre.
    vd = sys.modules.get("voz_documental")
    if vd is not None and getattr(vd, "_srv", None) is not None and vd._srv.poll() is None:
        vd._srv.terminate()
        try:
            vd._srv.wait(timeout=30)
        except Exception:  # noqa: BLE001
            vd._srv.kill()
        vd._srv = None
        log("GPU: cerré el servidor de voz de Python para la voz de la pizarra")
    try:
        with urllib.request.urlopen("http://localhost:11434/api/ps", timeout=5) as r:
            modelos = [m["name"] for m in json.loads(r.read()).get("models", [])]
        for m in modelos:
            q = urllib.request.Request("http://localhost:11434/api/generate", method="POST",
                                       data=json.dumps({"model": m, "keep_alive": 0}).encode(),
                                       headers={"Content-Type": "application/json"})
            urllib.request.urlopen(q, timeout=30).read()
            log("GPU: descargué %s de Ollama para la voz" % m)
        if modelos:
            time.sleep(5)
    except Exception as e:  # noqa: BLE001 - si Ollama no responde, la pieza decide con su propia compuerta de GPU
        log("GPU: no pude consultar Ollama (%s)" % str(e)[:100])


def hacer_pieza(serie: str, pieza: Path) -> str:
    t0 = time.time()
    liberar_gpu_ollama()
    log("INICIO %s %s" % (serie, pieza.name))
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "producir_pieza.py"), str(pieza)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    seg = round(time.time() - t0)
    if r.returncode == 2:
        log("OCUPADO %s %s" % (serie, pieza.name))
        return "ocupado"
    if r.returncode != 0:
        (pieza / ("FALLO_FABRICA" if serie == "expedientes" else "NO_PRODUCIR")).write_text(
            (r.stdout + r.stderr)[-3000:], encoding="utf-8")
        log("FALLO %s %s (%ds): %s" % (serie, pieza.name, seg, (r.stdout + r.stderr).strip()[-200:]))
        telegram("❌ %s «%s» no pasó sus revisiones (%d s). Queda marcada para revisión, no se reintenta sola."
                 % (serie, pieza.name, seg))
        return "fallo"
    mp4s = sorted(pieza.glob("entrega/*/*.mp4")) or sorted(pieza.glob("*_largo_FB.mp4"))
    log("OK %s %s (%ds) %s" % (serie, pieza.name, seg, mp4s[-1] if mp4s else "sin mp4"))
    if mp4s:
        telegram("🎬 %s «%s» lista (%d s). Revísala; para publicar se manda con botón APROBAR."
                 % (serie, pieza.name, seg), mp4s[-1] if mp4s[-1].stat().st_size < 49_000_000 else None)
    return "ok"


def main() -> int:
    est = _estado()
    log("=== FÁBRICA CONTINUA arranca (pid %d, turno %d)" % (os.getpid(), est["turno"]))
    telegram("🏭 Fábrica continua encendida. Rotación: %s. Tus URL por Telegram tienen prioridad: mientras el bot "
             "procese una, la fábrica espera. Nada se publica solo." % " → ".join(ROTACION))
    while True:
        if not esperar_turno():
            log("DETENIDA por %s" % DETENER)
            telegram("🛑 Fábrica continua detenida. Conteo: %s" % est.get("conteo"))
            return 0
        tipo = ROTACION[est["turno"] % len(ROTACION)]
        est["turno"] += 1
        resultado = "sin_cola"
        try:
            if tipo == "matematicas":
                p = en_cola("pizarra")
                if p is None:   # 24-oct: la cola de matemáticas se rellena sola (scripts/pizarra/escritor.py, con su compuerta)
                    log("cola de matemáticas vacía: escribo un guion nuevo")
                    subprocess.run([sys.executable, str(REPO / "scripts" / "pizarra" / "escritor.py"), "1"],
                                   capture_output=True, timeout=2400, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                    p = en_cola("pizarra")
                resultado = hacer_pieza("matematicas", p) if p else "sin_cola"
            elif tipo == "investigacion":
                h = datetime.now().hour
                p = en_cola("expedientes") if VENTANA_RENDER_LARGO[0] <= h < VENTANA_RENDER_LARGO[1] else None
                resultado = hacer_pieza("investigacion", p) if p else "sin_cola"
            if tipo == "python" or resultado == "sin_cola":
                if resultado == "sin_cola" and tipo != "python":
                    log("sin guion en cola de %s: hago Python para no quedar quieta" % tipo)
                tipo = "python"
                resultado = hacer_python(est)
        except Exception as e:  # noqa: BLE001 - la fábrica registra y sigue
            resultado = "error"
            log("ERROR %s: %s: %s" % (tipo, type(e).__name__, str(e)[:200]))
        est["conteo"][f"{tipo}:{resultado}"] = est["conteo"].get(f"{tipo}:{resultado}", 0) + 1
        _guardar(est)
        time.sleep(PAUSA_ENTRE_PIEZAS_S)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
