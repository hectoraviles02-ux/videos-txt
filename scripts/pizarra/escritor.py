# -*- coding: utf-8 -*-
"""Escritor de guiones de pizarra (tipo garrafón) — el operador, 24-oct-2026: «no estamos solo en Python, también los tipo
garrafón». Precedente: docs/operations/PARRILLA_LINEAS_CALIDAD_DISTRIBUCION_20261023.md, paso 1 («llenar la cola de
pizarra con un escritor automático»).

Llena la cola que la fábrica ya lee (guiones/pizarra/<AAAA-MM-DD_slug>/guion.json). No produce ni publica nada.
  1. escoge un tema de TEMAS que no se haya escrito;
  2. Claude (CLI `claude -p`) escribe el guion.json siguiendo CONTRATO.md, el motor y el guion del garrafón;
  3. compuerta ANTES de entrar a la cola (un guion malo quema 7 min de GPU):
     - las mismas compuertas del motor (producir_pizarra.compuertas: claves, sin cifras en la voz, cuentas);
     - tipos de escena/centro que el motor sabe dibujar; título corto; leyenda «ILUSTRATIVO»;
     - largo de la voz calibrado con el garrafón (184 palabras = 62 s; el motor exige 61–90 s);
     - cada número NARRADO debe salir de una verificación (resultado o término de la cuenta);
     - prueba en seco: el motor dibuja cuadros de muestra con tiempos simulados (anclas, fórmulas y escena reales);
  4. si falla, le devuelve los errores a Claude (máx. 2 correcciones); si sigue fallando, no entra a la cola.
Alcance: matemática cotidiana con valores SUPUESTOS (leyenda «EJEMPLO ILUSTRATIVO»). Nada de datos del mundo real
(constantes, estadísticas, precios reales): esos exigen fuente en procesado/*.json (CLAUDE.md).
Uso: python scripts/pizarra/escritor.py [n]   (n guiones, por defecto 1)
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from datetime import date, datetime
from pathlib import Path

PIZ = Path(__file__).resolve().parent
RAIZ = PIZ.parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(PIZ))
from content_factory.verificacion_numerica import numeros_narrados  # noqa: E402

COLA = RAIZ / "guiones" / "pizarra"
EJEMPLO = COLA / "2026-10-19_garrafon-dias" / "guion.json"
STAGING = RAIZ / "laboratorio" / "escritor_pizarra_20261024"
LOG = RAIZ / "logs" / "escritor_pizarra.log"
PALABRAS = (195, 265)          # garrafón: 184 palabras → 62 s; el motor acepta 61–90 s
TITULO_MAX = 30
LEYENDA_MAX = 40               # medido 24-oct: «EJEMPLO ILUSTRATIVO · tamaño del tinaco y caudal supuestos» se corta
HUECO_MAX_S = 3.2              # con tiempos SIMULADOS: el garrafón aprobado da 3.1 (real 2.8); el café rechazado da 7.5 (real 5.9)
ESCENAS, CENTROS = {"precio", "taza", "tanque"}, {"grafica", "barras"}

TEMAS = [
    # 24-oct, más simples y de bolsillo (el operador: «más simples para la raza»); van primero
    ("cargador-conectado", "¿Cuánto te cuesta dejar el cargador conectado todo el día? (escena precio; potencia y tarifa SUPUESTAS)"),
    ("garrafon-o-filtro", "¿Te conviene comprar garrafones o un filtro? (escena precio; barras; precios SUPUESTOS)"),
    ("tanda-o-ahorro", "¿Tanda o ahorro? Las cuentas de juntar dinero (escena precio; barras; montos SUPUESTOS)"),
    ("boiler-bano", "¿Cuánto gas gasta un baño largo? (escena tanque; consumo SUPUESTO por minuto)"),
    ("refresco-al-ano", "¿Cuánto gastas al año en refresco? (escena precio; crecimiento lineal; precio SUPUESTO)"),
    ("tinaco-manguera", "¿Cuánto tarda en llenarse un tinaco con la manguera? (escena tanque; la pendiente es el caudal)"),
    ("celular-bateria", "¿Cuántas horas te dura la batería del celular según cuánto lo usas? (escena tanque; descarga lineal)"),
    ("cafe-enfria", "Por qué el café se enfría rápido al principio y luego lento (escena taza; enfriamiento exponencial "
                    "con constante SUPUESTA)"),
    ("tarjeta-intereses", "Cómo crece una deuda de tarjeta si solo pagas el mínimo (escena precio; interés compuesto con "
                          "tasa SUPUESTA)"),
    ("descuento-doble", "Un treinta por ciento más un veinte por ciento de descuento NO es cincuenta (escena precio; barras)"),
    ("ahorro-diario", "Ahorrar diez pesos diarios: cuánto juntas en un año (escena precio; crecimiento lineal)"),
    ("gasolina-tanque", "¿Cuántos kilómetros recorres con un tanque de gasolina? (escena tanque; rendimiento SUPUESTO)"),
    ("propina", "Calcular la propina de memoria: el diez por ciento y la mitad (escena precio; barras)"),
    ("meses-sin-intereses", "Contado con descuento contra meses sin intereses (escena precio; barras)"),
    ("agua-hervir", "Por qué el agua tarda en hervir y luego ya no sube de cien grados (escena taza; gráfica que se "
                    "aplana; potencia SUPUESTA)"),
    ("garrafon-fuga", "Un garrafón con una fuga: cuánto se pierde al día (escena tanque; la derivada como fuga)"),
    ("velocidad-media", "Velocidad media en un viaje con una parada (escena precio con contador de kilómetros; gráfica "
                        "por tramos; distancias SUPUESTAS)"),
]

REGLAS = """No uses herramientas: responde directamente con el JSON.

Eres el guionista de la serie «pizarra» de AVIL·ES (reels verticales de matemáticas cotidianas, voz en
español de México). Escribe UN guion.json para el motor descrito abajo, con la calidad del ejemplo del garrafón.

TEMA: {tema}

REGLAS DURAS (si no se cumplen, el guion se rechaza):
1. Devuelve SOLO el JSON del guion, sin explicación ni bloques de código.
2. "motor": "pizarra"; "id": "{id_}"; "voz": exactamente {voz}.
3. "titulo" en MAYÚSCULAS, pregunta corta, máximo {tmax} caracteres.
4. "leyenda" empieza con "EJEMPLO ILUSTRATIVO · " y dice en DOS o TRES palabras qué se supone (p. ej.
   «consumo supuesto»); máximo {lmax} caracteres en total.
5. Todos los valores son SUPUESTOS para hacer la cuenta y la voz lo dice («supongamos», «como ejemplo»). No
   afirmes datos del mundo real (precios reales, estadísticas, constantes físicas exactas, marcas).
6. "tramos": de 8 a 10, ids t1..tN, entre {pmin} y {pmax} palabras EN TOTAL. Texto SIN cifras: todo número va en
   palabras («veinte», «tres punto tres»). Frases naturales, de tú, gancho en t1, remate en el último.
7. Cada número que la voz diga debe estar en "verificaciones": como "esperado" o como término de su "expr"
   (expresión Python con + - * / ** round abs min max). Calcula con cuidado: se recalcula con código.
8. Anclas: "tN", "tN:fin", "tN/palabra", "tN/palabra#2", con desfase "+0.4". La palabra debe existir TAL CUAL en
   ese tramo (sin acentos y minúsculas da igual). Las pistas van en orden de tiempo.
9. "escena.tipo" ∈ precio | taza | tanque; "centro.tipo" ∈ grafica | barras. Usa SOLO campos que el código del
   motor de abajo lee. Expresiones de variables/gráfica con las pistas, las variables y x.
10. Colores válidos: ambar, blanco, tenue, rosa, cian, lima, violeta, agua, vapor.
11. RITMO: algo debe moverse o latir en pantalla al menos cada dos segundos y medio de voz (pulsos, notas, cambios de
    pista). En la pizarra nunca debe haber más de dos segundos y medio quieta.
12. TONO «para la raza»: habla como en la calle, sin tecnicismos al inicio. t1 abre con la pregunta concreta o el
    resultado sorprendente (nunca «hoy veremos» ni «calculemos la pendiente»). La palabra técnica (pendiente, derivada,
    interés compuesto) aparece hasta el final como dato curioso. El último tramo cierra invitando a mandarlo a alguien
    («mándaselo a quien…»).
13. Incluye "pulsos" (cuando la voz nombra algo en pantalla, late), "formulas" (LaTeX simple en tokens), "notas" en
    el centro y un "remate" corto en MAYÚSCULAS. Sin "musica".

CONTRATO DEL MOTOR:
{contrato}

CÓDIGO DEL MOTOR (lo que lee de cada campo):
{codigo}

EJEMPLO APROBADO (garrafón):
{ejemplo}
"""

CORREGIR = """El guion que escribiste NO pasó la revisión. Errores:
{errores}

Corrígelo y devuelve SOLO el guion.json completo corregido. Guion anterior:
{guion}"""


def log(m: str) -> None:
    linea = f"{datetime.now():%Y-%m-%d %H:%M:%S} {m}"
    print(linea, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(linea + "\n")


def _norm(s: str) -> str:   # la misma normalización que motor.Tema._norm
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if c.isalnum())


def usados() -> set[str]:
    return {p.name.split("_", 1)[-1] for p in COLA.glob("*_*") if p.is_dir()} | \
           {p.name.split("_", 1)[-1] for p in STAGING.glob("*_*") if (p / "RECHAZADO").exists()}


def _claude(prompt: str, timeout: int = 900) -> str:
    claude = shutil.which("claude")
    if not claude:
        raise RuntimeError("no encuentro el CLI «claude»")
    # fuera del repo: si corre dentro, el CLAUDE.md del proyecto lo manda a usar herramientas y agota los turnos
    r = subprocess.run([claude, "-p", "--max-turns", "3", "--output-format", "text", "--disallowedTools",
                        "Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch,Agent,Skill"], input=prompt, text=True,
                       encoding="utf-8", errors="replace", capture_output=True, timeout=timeout, cwd=tempfile.gettempdir())
    if r.returncode:
        raise RuntimeError("claude falló: " + (r.stderr or r.stdout)[-300:])
    return r.stdout


def _json(texto: str) -> dict:
    a, b = texto.find("{"), texto.rfind("}")
    if a < 0 or b < a:
        raise ValueError("la respuesta no trae JSON")
    return json.loads(texto[a:b + 1])


def _anclas(g: dict) -> list[str]:
    """Todas las anclas de texto del guion (para comprobar que la palabra existe en su tramo)."""
    out = []

    def rec(x, clave=None):
        if isinstance(x, dict):
            for k, v in x.items():
                rec(v, k)
        elif isinstance(x, list):
            if clave in (None,) or not x:
                pass
            for v in x:
                rec(v, clave)
        elif isinstance(x, str) and clave in ("desde", "hasta", "en") and re.fullmatch(r"t\d+.*", x.strip()):
            out.append(x.strip())
    rec({k: v for k, v in g.items() if k not in ("tramos", "verificaciones", "voz")})
    for pts in g.get("pistas", {}).values():
        out += [a for a, _ in pts if isinstance(a, str)]
    return out


def revisar(g: dict, ejemplo: dict) -> list[str]:
    import producir_pizarra
    err = []
    try:
        producir_pizarra.compuertas(g)
    except SystemExit as e:
        err.append(str(e))
    except Exception as e:  # noqa: BLE001 - p. ej. una expr que no evalúa
        err.append(f"compuertas: {type(e).__name__}: {e}")
    if g.get("voz") != ejemplo["voz"]:
        err.append("«voz» debe ser exactamente la del ejemplo")
    if len(g.get("titulo", "")) > TITULO_MAX:
        err.append(f"título de {len(g['titulo'])} caracteres (máximo {TITULO_MAX})")
    if not str(g.get("leyenda", "")).startswith("EJEMPLO ILUSTRATIVO"):
        err.append("la leyenda debe empezar con «EJEMPLO ILUSTRATIVO»")
    if len(g.get("leyenda", "")) > LEYENDA_MAX:
        err.append(f"leyenda de {len(g['leyenda'])} caracteres (máximo {LEYENDA_MAX}: se corta en pantalla)")
    if g.get("escena", {}).get("tipo") not in ESCENAS:
        err.append(f"escena.tipo debe ser uno de {sorted(ESCENAS)}")
    if g.get("centro", {}).get("tipo") not in CENTROS:
        err.append(f"centro.tipo debe ser uno de {sorted(CENTROS)}")
    if "musica" in g:
        err.append("sin «musica» (licencia no verificada)")
    tramos = {t["id"]: t["texto"] for t in g.get("tramos", [])}
    n = sum(len(t.split()) for t in tramos.values())
    if not PALABRAS[0] <= n <= PALABRAS[1]:
        err.append(f"la voz tiene {n} palabras; deben ser entre {PALABRAS[0]} y {PALABRAS[1]} (61–90 s)")
    # anclas: la palabra existe en su tramo
    for a in _anclas(g):
        m = re.fullmatch(r"(t\d+)(?::fin|/([^#+\-]+)(?:#(\d+))?)?([+-]\d+(?:\.\d+)?)?", a)
        if not m:
            err.append(f"ancla mal escrita: «{a}»")
            continue
        tid, pal, k = m.group(1), m.group(2), int(m.group(3) or 1)
        if tid not in tramos:
            err.append(f"ancla «{a}»: no existe el tramo {tid}")
        elif pal and sum(_norm(w) == _norm(pal) for w in tramos[tid].split()) < k:
            err.append(f"ancla «{a}»: la palabra «{pal}» (vez {k}) no está en {tid}")
    # números narrados ⊆ números de las verificaciones
    permitidos = set()
    for vf in g.get("verificaciones", []):
        permitidos.add(round(abs(float(vf["esperado"])), 6))
        permitidos |= {round(float(x), 6) for x in re.findall(r"\d+(?:\.\d+)?", str(vf.get("expr", "")))}
    for tid, txt in tramos.items():
        for nn in numeros_narrados(txt):
            v = round(nn["valor"], 6)
            if v not in permitidos and v > 1:   # «uno/una» suelen ser artículo
                err.append(f"{tid}: la voz dice «{nn['texto']}» ({v:g}) y ninguna verificación lo calcula")
    return err


def hueco_maximo(d: Path) -> tuple[float, float, float, str]:
    """Hueco más largo entre dos eventos en pantalla (anclas de pistas, notas, fórmulas, pulsos). Medido 24-oct: el garrafón
    aprobado tiene 2.8 s como máximo; el café (rechazado por «movimiento_pizarra», tramo quieto 4.3 s) tenía 5.9 s."""
    sys.path.insert(0, str(PIZ))
    import motor
    tema = motor.Tema(d)
    ts = sorted({tema.ancla(a) for a in _anclas(tema.g)} | {t for _, t in tema.pulsos} | {0.0, tema.dur})
    gap, a, b = max((b - a, a, b) for a, b in zip(ts, ts[1:]))
    tr = next((x["id"] for x in tema.tiempos["tramos"] if x["ini"] - 0.5 <= a <= x["fin"] + 0.5), "?")
    return gap, a, b, tr


def prueba_seca(g: dict, d: Path) -> list[str]:
    """El motor dibuja cuadros de muestra con tiempos simulados (0.34 s por palabra, como el garrafón)."""
    d.mkdir(parents=True, exist_ok=True)
    (d / "guion.json").write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
    t, tramos = 0.5, []
    for tr in g["tramos"]:
        pal = []
        for w in tr["texto"].split():
            limpio = re.sub(r"^[^\wÁÉÍÓÚÜÑáéíóúüñ]+|[^\wÁÉÍÓÚÜÑáéíóúüñ]+$", "", w)
            pal.append({"w": limpio or w, "ini": round(t, 3), "fin": round(t + 0.3, 3)})
            t += 0.34
        tramos.append({"id": tr["id"], "texto": tr["texto"], "ini": pal[0]["ini"], "fin": pal[-1]["fin"],
                       "modo": "guiado", "palabras": pal})
        t += 0.4
    (d / "tiempos.json").write_text(json.dumps({"duracion": round(t + 0.6, 2), "tramos": tramos, "cobertura": {}},
                                               ensure_ascii=False), encoding="utf-8")
    hueco = hueco_maximo(d)
    if hueco[0] > HUECO_MAX_S:
        return [f"pizarra quieta {hueco[0]:.1f} s entre {hueco[1]:.1f} s y {hueco[2]:.1f} s (máximo {HUECO_MAX_S} s): agrega "
                f"pulsos, notas o cambios de pista con anclas en ese tramo ({hueco[3]})"]
    muestras = [str(round(x, 1)) for x in (1, t * 0.25, t * 0.5, t * 0.75, t - 0.5)]
    r = subprocess.run([sys.executable, str(PIZ / "motor.py"), str(d), "--muestras", *muestras], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=600)
    if r.returncode or len(list(d.glob("cuadro_*.png"))) < len(muestras):
        return ["el motor no pudo dibujar el guion: " + (r.stderr or r.stdout).strip()[-600:]]
    return []


def escribir(slug: str, tema: str) -> Path | None:
    ejemplo = json.loads(EJEMPLO.read_text(encoding="utf-8"))
    motor_src = (PIZ / "motor.py").read_text(encoding="utf-8").splitlines()
    prompt = REGLAS.format(tema=tema, id_=slug.replace("-", "_"), voz=json.dumps(ejemplo["voz"], ensure_ascii=False),
                           tmax=TITULO_MAX, lmax=LEYENDA_MAX, pmin=PALABRAS[0] + 10, pmax=PALABRAS[1] - 15,
                           contrato=(PIZ / "CONTRATO.md").read_text(encoding="utf-8"),
                           codigo="\n".join(motor_src[110:420]), ejemplo=json.dumps(ejemplo, ensure_ascii=False, indent=1))
    hoy = date.today().isoformat()
    d = STAGING / f"{hoy}_{slug}"
    texto, g, err = _claude(prompt), None, []
    for ronda in range(3):
        try:
            g = _json(texto)
            err = revisar(g, ejemplo) or prueba_seca(g, d)
        except Exception as e:  # noqa: BLE001 - JSON roto, etc.: se le devuelve a Claude
            err = [f"{type(e).__name__}: {e}"]
        log(f"{slug}: ronda {ronda + 1}: " + ("OK" if not err else f"{len(err)} errores: " + " | ".join(err)[:600]))
        if not err:
            break
        if ronda < 2:
            texto = _claude(CORREGIR.format(errores="\n".join("- " + e for e in err),
                                            guion=json.dumps(g, ensure_ascii=False, indent=1) if g else texto))
    if err:
        d.mkdir(parents=True, exist_ok=True)
        (d / "RECHAZADO").write_text("\n".join(err), encoding="utf-8")
        log(f"{slug}: RECHAZADO; no entra a la cola ({d})")
        return None
    destino = COLA / f"{hoy}_{slug}"
    destino.mkdir(parents=True, exist_ok=False)
    (destino / "guion.json").write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"{slug}: EN COLA {destino} · «{g['titulo']}» · muestras en {d}")
    return destino


def corregir_cola() -> None:
    """Revisa los guiones ya en cola con la compuerta actual; si fallan, Claude los corrige (máx. 2 rondas). Respalda el
    guion anterior; una pieza con NO_PRODUCIR/MANIFIESTO de un intento fallido se reactiva moviendo esos archivos a
    historial/ (no se borra nada)."""
    ejemplo = json.loads(EJEMPLO.read_text(encoding="utf-8"))
    for d in sorted(p for p in COLA.glob("*_*") if p.is_dir() and p.name != EJEMPLO.parent.name):
        if any((d / "entrega").glob("*/*.mp4")):
            continue
        g = json.loads((d / "guion.json").read_text(encoding="utf-8"))
        prueba = STAGING / f"cola_{d.name}"
        err = revisar(g, ejemplo) or prueba_seca(g, prueba)
        ronda = 0
        while err and ronda < 2:
            ronda += 1
            try:
                g = _json(_claude(CORREGIR.format(errores="\n".join("- " + e for e in err),
                                                  guion=json.dumps(g, ensure_ascii=False, indent=1))))
                err = revisar(g, ejemplo) or prueba_seca(g, prueba)
            except Exception as e:  # noqa: BLE001
                err = [f"{type(e).__name__}: {e}"]
        if err:
            log(f"{d.name}: sigue fallando tras corregir: {err[0][:200]}")
            continue
        if ronda:
            marca = datetime.now().strftime("%Y%m%d_%H%M")
            shutil.copy2(d / "guion.json", d / f"guion_backup_{marca}.json")
            (d / "guion.json").write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
            hist = d / "historial"
            for f in [d / "NO_PRODUCIR", *d.glob("MANIFIESTO_NO_APROBADO_*.json")]:
                if f.exists():
                    hist.mkdir(exist_ok=True)
                    shutil.move(str(f), str(hist / f.name))
        log(f"{d.name}: {'CORREGIDO' if ronda else 'ya pasaba'} (hueco máx. {hueco_maximo(prueba)[0]:.1f} s simulado)")


def main(n: int = 1) -> int:
    hechos, listos = usados(), 0
    for slug, tema in TEMAS:
        if listos >= n:
            break
        if slug in hechos:
            continue
        if escribir(slug, tema):
            listos += 1
    log(f"escritor: {listos}/{n} guiones nuevos en la cola")
    return 0 if listos else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) > 1 and sys.argv[1] == "--corregir-cola":
        corregir_cola()
        sys.exit(0)
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 1))
