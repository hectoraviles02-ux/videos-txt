# -*- coding: utf-8 -*-
"""Juez NLI local (mDeBERTa-v3 multilingüe, XNLI) para el gate de fidelidad.

Elegido el 2026-10-13 tras la comparacion aislada (docs/operations/MOTOR_DOCUMENTAL_PERIODISTICO/
GATE_FIDELIDAD_20261013/evaluacion/COMPARACION_VERIFICADORES.md). Misma interfaz que
synthesis.gate_fidelidad.juez_ollama:
juez(fuente, afirmaciones) -> [{"clasificacion", "fragmento", "motivo", "puntajes"}].

Como funciona: cada afirmacion (hipotesis) se compara contra cada oracion de la fuente y contra
cada par de oraciones vecinas (premisas). El modelo da P(implica), P(neutral) y P(contradice).
- El fragmento es SIEMPRE una oracion literal de la fuente: este juez no puede inventar evidencia.
- RESPALDADA  si la mejor P(implica) >= UMBRAL_IMPLICA y supera a la mejor P(contradice)
- CONTRADICHA si la mejor P(contradice) >= UMBRAL_CONTRADICE (con su premisa literal)
- si no: SIN_EVIDENCIA
Modelo: MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7 (MIT), cache local de HF.
"""
from __future__ import annotations

from synthesis import gate_fidelidad as G

MODELO = "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
UMBRAL_IMPLICA = 0.50
UMBRAL_CONTRADICE = 0.50


_CACHE: dict = {}


def juez_nli(dispositivo: str | None = None, lote: int = 32):
    """El modelo se carga UNA vez por proceso (el bot lo reutiliza entre pedidos). Si no esta en
    la cache local de Hugging Face falla (local_files_only): el gate lo trata como verificador
    no disponible y NO produce."""
    clave = (dispositivo, lote)
    if clave in _CACHE:
        return _CACHE[clave]
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(MODELO, local_files_only=True)
    mod = AutoModelForSequenceClassification.from_pretrained(MODELO, local_files_only=True)
    dev = dispositivo or ("cuda" if torch.cuda.is_available() else "cpu")
    mod.to(dev).eval()
    etiquetas = {v.lower(): k for k, v in mod.config.id2label.items()}  # entailment/neutral/contradiction
    i_imp, i_con = etiquetas["entailment"], etiquetas["contradiction"]

    def _puntajes(premisas, hipotesis):
        out = []
        for i in range(0, len(premisas), lote):
            p = premisas[i:i + lote]
            enc = tok(p, [hipotesis] * len(p), truncation=True, max_length=512,
                      padding=True, return_tensors="pt").to(dev)
            with torch.no_grad():
                pr = torch.softmax(mod(**enc).logits, dim=-1).cpu().tolist()
            out += [(x[i_imp], x[i_con]) for x in pr]
        return out

    def _juez(fuente: str, afirmaciones: list[str]) -> list[dict]:
        if not G._oraciones(fuente):
            raise G.JuezNoDisponible("la fuente no tiene oraciones que comparar")
        res = []
        for h in afirmaciones:
            # 23-oct-2026: solo contra las ventanas de la fuente que hablan de lo mismo (antes: contra TODAS,
            # y cualquier oracion de otro tema podia «contradecir»). Al modelo se le pasa la premisa con la
            # ortografia de los nombres reparada (Next.hot.se -> Next.js); el fragmento que se reporta es el literal.
            premisas = G.alinear(h, fuente)
            try:
                pts = _puntajes([G.reparar_premisa(p, h) for p in premisas], h)
            except Exception as e:  # p. ej. memoria de la tarjeta agotada: sin verificar no se produce
                raise G.JuezNoDisponible(f"NLI: {type(e).__name__}: {str(e)[:160]}") from e
            k_imp = max(range(len(pts)), key=lambda k: pts[k][0])
            k_con = max(range(len(pts)), key=lambda k: pts[k][1])
            imp, con = pts[k_imp][0], pts[k_con][1]
            if imp >= UMBRAL_IMPLICA and imp >= con:
                r = {"clasificacion": "RESPALDADA", "fragmento": premisas[k_imp],
                     "motivo": f"NLI implica {imp:.2f}"}
            elif con >= UMBRAL_CONTRADICE:
                r = {"clasificacion": "CONTRADICHA", "fragmento": premisas[k_con],
                     "motivo": f"NLI contradice {con:.2f}"}
            else:
                r = {"clasificacion": "SIN_EVIDENCIA", "fragmento": "",
                     "motivo": f"NLI máx implica {imp:.2f}, máx contradice {con:.2f}"}
            r["puntajes"] = {"implica": round(imp, 3), "contradice": round(con, 3)}
            res.append(r)
        return res
    _juez.dispositivo = dev
    _juez.nombre = f"NLI {MODELO.split('/')[-1]} ({dev})"
    _CACHE[clave] = _juez
    return _juez
