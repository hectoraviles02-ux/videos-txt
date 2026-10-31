# -*- coding: utf-8 -*-
"""Pruebas de la musica sintetizada y de la traduccion del guion.

Lo que se prueba es lo que puede fallar en silencio: que la musica no invada
la banda de la voz, que sea reproducible, y que la traduccion no pierda ni
invente cifras. Un numero mal traducido es indistinguible de un dato
inventado, y la regla del proyecto no distingue entre las dos cosas.
"""
from __future__ import annotations

import os

import numpy as np
import pytest

import synthesis.musica as mus
import synthesis.traduccion as trad


# ── musica ───────────────────────────────────────────────────────────────

def test_mu_01a_genera_y_es_reproducible(tmp_path):
    """Misma semilla, mismo archivo: un video se tiene que poder rehacer."""
    a = mus.generar(3.0, str(tmp_path / "a.mp3"), ambiente="sobrio", semilla=7)
    b = mus.generar(3.0, str(tmp_path / "b.mp3"), ambiente="sobrio", semilla=7)
    assert os.path.getsize(a) > 1000
    with open(a, "rb") as fa, open(b, "rb") as fb:
        assert fa.read() == fb.read()


def test_mu_01b_semillas_distintas_dan_audio_distinto(tmp_path):
    a = mus.generar(3.0, str(tmp_path / "a.mp3"), semilla=1)
    b = mus.generar(3.0, str(tmp_path / "b.mp3"), semilla=2)
    with open(a, "rb") as fa, open(b, "rb") as fb:
        assert fa.read() != fb.read()


def test_mu_02a_no_invade_la_banda_de_la_voz(tmp_path):
    """El filtro esta en 800 Hz a proposito: la voz vive arriba de eso.

    Si esta prueba falla, la musica va a tapar la narracion aunque el nivel
    parezca bajo.
    """
    ruta = mus.generar(4.0, str(tmp_path / "m.mp3"), ambiente="asombro")
    wav = str(tmp_path / "m.wav")
    import subprocess

    from adapters.ffmpeg_tools import resolve_ffmpeg
    subprocess.run([resolve_ffmpeg(None), "-y", "-i", ruta, "-ac", "1",
                    "-ar", "44100", wav], capture_output=True, timeout=120)
    import soundfile as sf
    x, sr = sf.read(wav)
    if x.ndim > 1:
        x = x.mean(1)
    X = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    f = np.fft.rfftfreq(len(x), 1 / sr)
    arriba = float(X[f > 800].sum() / X.sum())
    assert arriba < 0.15, "demasiada energia en la banda de la voz: %.1f%%" % (
        100 * arriba)


def test_mu_02b_el_nivel_deja_lugar_a_la_narracion(tmp_path):
    ruta = mus.generar(2.0, str(tmp_path / "m.mp3"))
    wav = str(tmp_path / "m.wav")
    import subprocess

    from adapters.ffmpeg_tools import resolve_ffmpeg
    subprocess.run([resolve_ffmpeg(None), "-y", "-i", ruta, "-ac", "1", wav],
                   capture_output=True, timeout=120)
    import soundfile as sf
    x, _ = sf.read(wav)
    if x.ndim > 1:
        x = x.mean(1)
    assert float(np.max(np.abs(x))) < 0.35


def test_mu_03a_rechaza_duracion_invalida(tmp_path):
    with pytest.raises(ValueError):
        mus.generar(0, str(tmp_path / "x.mp3"))


def test_mu_03b_rechaza_ambiente_desconocido(tmp_path):
    with pytest.raises(ValueError) as e:
        mus.generar(2.0, str(tmp_path / "x.mp3"), ambiente="reggaeton")
    assert "reggaeton" in str(e.value)


def test_mu_04a_elige_el_caracter_segun_el_tema():
    assert mus.ambiente_para("los agujeros negros del cosmos") == "asombro"
    assert mus.ambiente_para("la falla que tumbo el sistema") == "tension"
    assert mus.ambiente_para("como llenar un formato") == mus.AMBIENTE_POR_DEFECTO


# ── traduccion ───────────────────────────────────────────────────────────

def test_tr_01a_detecta_cifra_perdida():
    avisos = trad.revisar_cifras("La masa es de 225 soles", "The mass is huge")
    assert any("perdidas" in a and "225" in a for a in avisos)


def test_tr_01b_detecta_cifra_inventada():
    avisos = trad.revisar_cifras("hay 225 soles", "there are 225 and 390 suns")
    assert any("no estaban" in a and "390" in a for a in avisos)


def test_tr_01c_los_separadores_no_cuentan_como_diferencia():
    """6,500 en espanol y 6.500 en ingles son el mismo numero."""
    assert trad.revisar_cifras("son 6,500 millones", "it is 6.500 billion") == []


def test_tr_01d_traduccion_fiel_no_reporta_nada():
    assert trad.revisar_cifras("En 2019 se publico", "In 2019 it was published") == []


def test_tr_02a_idioma_no_soportado_falla_claro():
    with pytest.raises(trad.TraduccionFallida) as e:
        trad.traducir_escenas([{"narration": "hola"}], "klingon")
    assert "klingon" in str(e.value)


def test_tr_03a_conserva_lo_que_no_es_narracion(monkeypatch):
    """search_keywords NO se traduce: el catalogo de metraje indexa en ingles."""
    monkeypatch.setattr(trad, "_pedir",
                        lambda p, **k: {"frases": ["The first image of 2019"]})
    escenas = [{"id": "scene-001", "narration": "La primera imagen de 2019",
                "search_keywords": "telescope dome night sky"}]
    t = trad.traducir_escenas(escenas, "en")
    assert t.escenas[0]["search_keywords"] == "telescope dome night sky"
    assert t.escenas[0]["narration"] == "The first image of 2019"
    assert t.escenas[0]["id"] == "scene-001"


def test_tr_03b_si_el_bloque_vuelve_incompleto_se_deja_el_original(monkeypatch):
    """Un desfase alinearia cada frase con la imagen equivocada."""
    monkeypatch.setattr(trad, "_pedir", lambda p, **k: {"frases": ["solo una"]})
    escenas = [{"narration": "uno"}, {"narration": "dos"}, {"narration": "tres"}]
    t = trad.traducir_escenas(escenas, "en")
    assert [e["narration"] for e in t.escenas] == ["uno", "dos", "tres"]
    assert any("sin traducir" in a for a in t.avisos)


def test_tr_03c_reporta_la_cifra_perdida_por_escena(monkeypatch):
    monkeypatch.setattr(trad, "_pedir",
                        lambda p, **k: {"frases": ["The mass is very large"]})
    t = trad.traducir_escenas(
        [{"id": "scene-002", "narration": "La masa es de 6,500 millones"}], "en")
    assert any("scene-002" in a and "6500" in a for a in t.avisos)


def test_tr_04a_el_storyboard_queda_marcado_con_su_idioma(monkeypatch):
    monkeypatch.setattr(trad, "_pedir", lambda p, **k: {"frases": ["One", "Two"]})
    sb = {"scenes": [{"narration": "Uno"}, {"narration": "Dos"}],
          "metadata": {"title": "T"}}
    r = trad.traducir_storyboard(sb, "en")
    assert r["metadata"]["idioma"] == "en"
    assert r["metadata"]["title"] == "T"
    assert sb["scenes"][0]["narration"] == "Uno", "no debe mutar el original"


def test_tr_04b_storyboard_vacio_falla_claro():
    with pytest.raises(trad.TraduccionFallida):
        trad.traducir_storyboard({"scenes": []}, "en")
