# -*- coding: utf-8 -*-
"""Pruebas del armado de podcast.

Lo que se prueba es lo que puede fallar en silencio: la direccion de las
correcciones declaradas por la fuente, el troceado de la transcripcion, y
la deteccion de defectos del dialogo. La sintesis y el ensamblado hablan
con edge-tts y ffmpeg, y esos se prueban por separado.
"""
from __future__ import annotations

import synthesis.podcast as pod


# ── correcciones declaradas por la fuente ────────────────────────────────

AVISO_ASR = ("El ASR transcribe 'Claude Code' como 'Quadcode'/'Quad'/'Cloud'. "
             "No se corrigio a mano.")


def test_pc_01a_corrige_en_la_direccion_correcta():
    """Lo que va ANTES de 'como' es lo correcto; lo de despues, el error.

    Regresion: la primera version tomaba la ULTIMA cita como la buena y
    reemplazaba al reves, dejando 'Cloud' donde debia decir 'Claude Code'.
    """
    reglas = pod._correcciones([AVISO_ASR])
    salida = pod._aplicar("Quadcode y Quad son lo mismo que Cloud", reglas)
    assert salida == "Claude Code y Claude Code son lo mismo que Claude Code"


def test_pc_01b_ignora_avisos_que_no_declaran_correccion():
    """Un aviso de confianza o de idioma no debe generar reglas."""
    avisos = ["110 de 678 segmentos tienen confianza < 0.6.",
              "El audio es un doblaje al espanol hasta [15:29]."]
    assert pod._correcciones(avisos) == []


def test_pc_01c_sin_direccion_no_inventa_regla():
    """Si el aviso no dice 'como', no se sabe cual cita es la buena."""
    assert pod._correcciones(["El ASR confunde 'foo' y 'bar'."]) == []


def test_pc_01d_no_toca_lo_que_no_declara_la_fuente():
    reglas = pod._correcciones([AVISO_ASR])
    texto = "Esto habla de Docker y de Kubernetes"
    assert pod._aplicar(texto, reglas) == texto


# ── timestamps ───────────────────────────────────────────────────────────

def test_pc_02a_mmss():
    assert pod._mmss(0) == "0:00"
    assert pod._mmss(61) == "1:01"
    assert pod._mmss(931) == "15:31"


# ── defectos observables del dialogo ─────────────────────────────────────

def _turno(quien: str, palabras: int) -> dict:
    return {"quien": quien, "texto": " ".join(["palabra"] * palabras)}


def test_pc_03a_detecta_turnos_seguidos_del_mismo():
    turnos = [_turno("JORGE", 20), _turno("JORGE", 20), _turno("DALIA", 20)]
    defectos = pod.revisar_dialogo(turnos)
    assert any("seguidos" in d for d in defectos)


def test_pc_03b_detecta_titulares():
    """Un turno corto suena a viñeta leida, no a habla. Medido el 2026-08-21."""
    turnos = [_turno("JORGE", 5), _turno("DALIA", 20)]
    defectos = pod.revisar_dialogo(turnos)
    assert any("titular" in d for d in defectos)


def test_pc_03c_detecta_turnos_largos():
    turnos = [_turno("JORGE", 20), _turno("DALIA", 90)]
    defectos = pod.revisar_dialogo(turnos)
    assert any("encima" in d for d in defectos)


def test_pc_03d_dialogo_sano_no_reporta_nada():
    turnos = [_turno("JORGE", 25), _turno("DALIA", 30), _turno("JORGE", 22)]
    assert pod.revisar_dialogo(turnos) == []


# ── troceado de la transcripcion ─────────────────────────────────────────

def test_pc_04a_sin_segmentos_no_hay_puntos():
    """No debe llamar a Ollama si no hay nada que leer."""
    assert pod.puntos_desde_transcripcion({"segmentos": []}) == []
    assert pod.puntos_desde_transcripcion({}) == []


def test_pc_04b_trocea_por_palabras_y_conserva_timestamp(monkeypatch):
    """Cada tramo debe llegar al modelo con los timestamps de sus segmentos."""
    segmentos = [{"inicio_seg": i * 5, "fin_seg": i * 5 + 5,
                  "texto": " ".join(["palabra"] * 100)} for i in range(20)]
    vistos: list[str] = []

    def falso_pedir(prompt, **kw):
        vistos.append(prompt)
        return {"puntos": [{"t": "0:05", "punto": "algo concreto"}]}

    monkeypatch.setattr(pod, "_pedir", falso_pedir)
    puntos = pod.puntos_desde_transcripcion({"segmentos": segmentos})

    # 2000 palabras / 900 por tramo -> 3 tramos
    assert len(vistos) == 3
    assert len(puntos) == 3
    assert "[0:00]" in vistos[0]
    assert all(p["t"] for p in puntos)


def test_pc_04c_si_ollama_no_responde_sigue_con_los_demas(monkeypatch):
    """Un tramo caido no debe tumbar el episodio entero."""
    segmentos = [{"inicio_seg": i * 5, "texto": " ".join(["p"] * 100)}
                 for i in range(20)]
    llamadas = {"n": 0}

    def falso_pedir(prompt, **kw):
        llamadas["n"] += 1
        if llamadas["n"] == 2:
            raise pod.OllamaNoResponde("caido")
        return {"puntos": [{"t": "0:05", "punto": "algo"}]}

    monkeypatch.setattr(pod, "_pedir", falso_pedir)
    puntos = pod.puntos_desde_transcripcion({"segmentos": segmentos})
    assert len(puntos) == 2  # el tramo caido se salta, los otros dos entran


# ── dialogo ──────────────────────────────────────────────────────────────

def test_pc_05a_sin_puntos_no_hay_dialogo():
    assert pod.dialogo_desde_puntos([]) == []


def test_pc_05b_normaliza_el_hablante(monkeypatch):
    """El modelo escribe el nombre como quiere; solo hay dos anfitriones."""
    def falso_pedir(prompt, **kw):
        return {"turnos": [{"quien": "dalia", "texto": "uno"},
                           {"quien": "Locutor", "texto": "dos"}]}

    monkeypatch.setattr(pod, "_pedir", falso_pedir)
    puntos = [{"t": "0:01", "punto": "p%d" % i} for i in range(9)]
    turnos = pod.dialogo_desde_puntos(puntos)
    assert {t["quien"] for t in turnos} <= {"JORGE", "DALIA"}
    assert turnos[0]["quien"] == "DALIA"
    assert turnos[1]["quien"] == "JORGE"


def test_pc_05c_los_puntos_van_en_el_prompt(monkeypatch):
    """La unica fuente permitida debe llegarle al modelo."""
    prompts: list[str] = []

    def falso_pedir(prompt, **kw):
        prompts.append(prompt)
        return {"turnos": [{"quien": "JORGE", "texto": "x"}]}

    monkeypatch.setattr(pod, "_pedir", falso_pedir)
    pod.dialogo_desde_puntos([{"t": "2:10", "punto": "dato irrepetible"}] * 6)
    assert any("dato irrepetible" in p for p in prompts)
    assert any("[2:10]" in p for p in prompts)
