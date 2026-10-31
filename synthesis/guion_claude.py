# -*- coding: utf-8 -*-
"""Guion de URL escrito por Claude (CLI `claude -p`), con el modelo local como respaldo — 22-oct-2026.

Pedido de el operador: que el flujo de URL de Telegram tenga guiones de la calidad de los expedientes, y «si tú no
estás, que use el local». Medido el 22-oct: con el modelo local, 3 URL seguidas dieron 0 videos; en dos el gate
de fidelidad encontró 0 frases respaldadas (el guion dramatiza e inventa: «un director técnico notó…»).

Misma interfaz que ScriptGenerator (lo usa storyboard_builder.process): generate(extracted_content) -> dict
{"title", "scenes": [{"narration", "visual_description", "search_keywords"}]}. Si `claude` no existe, no responde,
tarda demasiado o devuelve algo que no es un guion válido, se usa ScriptGenerator (local) SIN cambiar nada más.
El gate de fidelidad del pipeline sigue igual: revisa este guion contra la fuente igual que al local.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess

from synthesis.script_generator import ScriptGenerator

logger = logging.getLogger(__name__)

TIMEOUT_S = 420

REGLAS = """Eres guionista de documentales cortos en español de México. Escribe el guion de un video a partir de la
FUENTE de abajo (una transcripción o artículo).

REGLA PRINCIPAL — FIDELIDAD:
- Cada frase de la narración debe decir SOLO lo que dice la fuente. Puedes resumir, reordenar y explicar con
  palabras claras, pero NO agregues personas, cargos, emociones, escenas, diálogos, motivos ni consecuencias que
  la fuente no diga.
- Nombres, fechas y cifras: exactamente como en la fuente. Si la fuente no da un dato, no lo pongas.
- Prohibido el relleno dramático sin dato: «lo que no sabían era…», «la catástrofe se acercaba», «trabajaron
  incansablemente», «una valiosa lección», «nada volvería a ser igual».
- Si la transcripción trae errores evidentes de oído (p. ej. «Chrome job» por «cron job»), usa el término
  correcto solo si es inequívoco; si dudas, omite esa frase.
- El tono puede ser atractivo: el interés sale de los hechos de la fuente, no de adornos.

FORMATO: responde SOLO un objeto JSON válido, sin markdown ni texto extra:
{"title": "...", "scenes": [{"narration": "...", "visual_description": "...", "search_keywords": "..."}]}
- EXACTAMENTE {n_esc} escenas, unas {por_esc} palabras de narración cada una (mínimo {min_esc}).
- visual_description: lo que se vería, concreto (sujeto, lugar, luz), sin texto en pantalla.
- search_keywords: 4 o 5 palabras en INGLÉS de lo que vería una cámara (personas haciendo algo en un lugar).
  Prohibido: chart, graph, table, statistics, infographic, document, text, screen with text, logo, newspaper,
  poster, sign, information, data, technology, digital, system.

FUENTE:
{texto}
"""


def _json_de(raw: str) -> dict:
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("la respuesta no trae un objeto JSON")
    return json.loads(s[i:j + 1])


def _valido(d: dict, n_esc: int) -> str | None:
    esc = d.get("scenes") if isinstance(d, dict) else None
    if not isinstance(esc, list) or not esc:
        return "sin escenas"
    if any(not str(e.get("narration", "")).strip() for e in esc):
        return "escena sin narración"
    if len(esc) < max(1, n_esc - 1):
        return "pidió %d escenas y llegaron %d" % (n_esc, len(esc))
    return None


class GuionClaude(ScriptGenerator):
    """ScriptGenerator que primero le pide el guion a Claude y, si falla, usa el local."""

    def __init__(self, *a, ejecutar=subprocess.run, claude: str | None = None, **kw):
        super().__init__(*a, **kw)
        self._ejecutar = ejecutar
        self._claude = claude if claude is not None else shutil.which("claude")
        self.ultimo_motor = None

    def generate(self, extracted_content: dict) -> dict:
        try:
            d = self._por_claude(extracted_content)
            self.ultimo_motor = "claude"
            logger.info("guion: Claude (%d escenas)", len(d["scenes"]))
            return d
        except Exception as e:  # noqa: BLE001 - cualquier falla de Claude cae al local, nunca detiene el pipeline
            logger.warning("guion: Claude no disponible (%s: %s); uso el modelo local", type(e).__name__, str(e)[:160])
            self.ultimo_motor = "local"
            return super().generate(extracted_content)

    def _por_claude(self, extracted_content: dict) -> dict:
        if not self._claude:
            raise RuntimeError("no está instalado el comando claude")
        texto = self._get_text(extracted_content)
        palabras = len(texto.split())
        self._compuerta_fuente(texto, palabras, extracted_content)   # misma compuerta que el local
        n_esc, por_esc, _ = self._plan_por_fuente(extracted_content, palabras)
        prompt = (REGLAS.replace("{n_esc}", str(n_esc)).replace("{por_esc}", str(por_esc))
                  .replace("{min_esc}", str(max(40, por_esc - 20))).replace("{texto}", texto[:24000]))
        r = self._ejecutar([self._claude, "-p", "--max-turns", "1", "--output-format", "text"], input=prompt,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT_S)
        if r.returncode != 0:
            raise RuntimeError("claude salió con %s: %s" % (r.returncode, (r.stderr or r.stdout or "")[-160:]))
        d = _json_de(r.stdout)
        motivo = _valido(d, n_esc)
        if motivo:
            raise ValueError(motivo)
        for e in d["scenes"]:
            e.setdefault("visual_description", "")
            e.setdefault("search_keywords", "")
        return d


# ── corrección de frases bloqueadas por el gate (23-oct-2026) ─────────────
# Caso real (runs/fidelidad_20261023T113232): el guion agregó «antes de volver a ponerlas en marcha» a una frase
# fiel. Se reescribe SOLO esa frase contra su evidencia literal y TODO el guion se vuelve a verificar; si Claude no
# responde, la frase se quita (quitar nunca agrega hechos). Una sola ronda: si sigue bloqueado, no se produce.

CORREGIR = """Eres verificador de un guion en español de México. Cada FRASE de abajo fue rechazada porque dice algo que
su EVIDENCIA (texto literal de la fuente) no dice. Reescribe cada frase para que diga SOLO lo que dice su evidencia,
con tono natural de narración, sin agregar nada. Si no se puede, devuelve "" para quitarla.
Responde SOLO JSON: {{"1": "frase corregida", "2": ""}}

{lista}"""


def corregir_frases(malas: list[tuple[str, str]], *, ejecutar=subprocess.run, claude: str | None = None) -> dict:
    """malas = [(frase, evidencia)]. Devuelve {frase: frase_nueva} ("" = quitarla)."""
    claude = claude if claude is not None else shutil.which("claude")
    lista = "\n\n".join('%d. FRASE: %s\n   EVIDENCIA: %s' % (i + 1, f, ev or "(ninguna)") for i, (f, ev) in enumerate(malas))
    try:
        if not claude:
            raise RuntimeError("no está instalado el comando claude")
        r = ejecutar([claude, "-p", "--max-turns", "1", "--output-format", "text"], input=CORREGIR.format(lista=lista),
                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        if r.returncode != 0:
            raise RuntimeError("claude salió con %s" % r.returncode)
        d = _json_de(r.stdout)
        return {f: str(d.get(str(i + 1), "")).strip() for i, (f, _) in enumerate(malas)}
    except Exception as e:  # noqa: BLE001 - sin Claude se quitan las frases: nunca se agrega nada
        logger.warning("corrección: Claude no disponible (%s); quito las frases bloqueadas", str(e)[:120])
        return {f: "" for f, _ in malas}
