# -*- coding: utf-8 -*-
"""Pruebas del ensamblado del episodio.

Se prueba lo que puede fallar en silencio: donde caen las pausas, que la
voz de cada turno sea la que corresponde, y que un fallo de loudnorm
degrade en vez de tumbar el episodio. La sintesis real habla con edge-tts
y no se prueba aqui.
"""
from __future__ import annotations

import os
import subprocess
import types

import synthesis.podcast_audio as pa


def _turnos(n: int) -> list[dict[str, str]]:
    return [{"quien": "JORGE" if i % 2 == 0 else "DALIA", "texto": "hola %d" % i}
            for i in range(n)]


def _parchar(monkeypatch, tmp_path, returncodes=None):
    """Sustituye edge-tts y ffmpeg; devuelve los comandos ejecutados."""
    ejecutados: list[list[str]] = []
    codigos = list(returncodes or [])

    class _Communicate:
        def __init__(self, texto, voice, rate="+0%", pitch="+0Hz"):
            ejecutados.append(["tts", voice, rate, pitch, texto])

        async def save(self, destino):
            with open(destino, "wb") as f:
                f.write(b"x")

    monkeypatch.setattr(pa, "resolve_ffmpeg", lambda p=None: "ffmpeg")
    monkeypatch.setitem(__import__("sys").modules, "edge_tts",
                        types.SimpleNamespace(Communicate=_Communicate))

    def falso_run(cmd, **kw):
        ejecutados.append(list(cmd))
        rc = codigos.pop(0) if codigos else 0
        # el concat final debe dejar el archivo para que exista la salida
        if rc == 0 and cmd[-1].endswith(".mp3"):
            with open(cmd[-1], "wb") as f:
                f.write(b"x")
        return subprocess.CompletedProcess(cmd, rc, "", "error simulado")

    monkeypatch.setattr(pa.subprocess, "run", falso_run)
    monkeypatch.setattr(pa, "duracion_de", lambda *a, **k: 42.0)
    return ejecutados


def test_pa_01a_cada_turno_usa_la_voz_de_su_anfitrion(monkeypatch, tmp_path):
    ejecutados = _parchar(monkeypatch, tmp_path)
    pa.montar_episodio(_turnos(4), str(tmp_path))
    voces = [e[1] for e in ejecutados if e and e[0] == "tts"]
    assert voces == [pa.JORGE.voice, pa.DALIA.voice,
                     pa.JORGE.voice, pa.DALIA.voice]


def test_pa_01b_las_voces_no_suenan_igual():
    """Dos voces distintas no bastan; el ritmo tambien las separa."""
    assert pa.JORGE.voice != pa.DALIA.voice
    assert pa.JORGE.rate != pa.DALIA.rate


def test_pa_02a_la_pausa_larga_cae_en_el_cambio_de_bloque(monkeypatch, tmp_path):
    _parchar(monkeypatch, tmp_path)
    pa.montar_episodio(_turnos(6), str(tmp_path), turnos_por_bloque=3)
    with open(os.path.join(str(tmp_path), "lista.txt"), encoding="utf-8") as f:
        lineas = [ln.strip() for ln in f if ln.strip()]
    pausas = [ln for ln in lineas if "p_turno" in ln or "p_bloque" in ln]
    # 6 turnos -> 5 huecos; el hueco tras el turno 3 es de bloque
    assert len(pausas) == 5
    assert "p_bloque" in pausas[2]
    assert all("p_turno" in p for i, p in enumerate(pausas) if i != 2)


def test_pa_02b_no_hay_pausa_despues_del_ultimo_turno(monkeypatch, tmp_path):
    _parchar(monkeypatch, tmp_path)
    pa.montar_episodio(_turnos(3), str(tmp_path))
    with open(os.path.join(str(tmp_path), "lista.txt"), encoding="utf-8") as f:
        lineas = [ln.strip() for ln in f if ln.strip()]
    assert "t002.mp3" in lineas[-1]


def test_pa_03a_si_loudnorm_falla_el_episodio_sale_igual(monkeypatch, tmp_path):
    """Sin normalizar es peor que normalizado, pero mucho mejor que nada."""
    # dos silencios ok, el concat con loudnorm falla, el segundo pasa
    ejecutados = _parchar(monkeypatch, tmp_path, returncodes=[0, 0, 1, 0])
    ep = pa.montar_episodio(_turnos(2), str(tmp_path))
    assert ep.audio_path
    assert any("loudnorm" in a for a in ep.avisos)
    concats = [e for e in ejecutados if "concat" in e]
    assert any(pa.LOUDNORM in e for e in concats)
    assert any(pa.LOUDNORM not in e for e in concats)


def test_pa_03b_si_el_concat_falla_siempre_se_avisa(monkeypatch, tmp_path):
    _parchar(monkeypatch, tmp_path, returncodes=[0, 0, 1, 1])
    try:
        pa.montar_episodio(_turnos(2), str(tmp_path))
    except RuntimeError as e:
        assert "ensamblar" in str(e)
    else:
        raise AssertionError("deberia haber fallado")


def test_pa_04a_sin_turnos_falla_temprano(tmp_path):
    try:
        pa.montar_episodio([], str(tmp_path))
    except ValueError as e:
        assert "turnos" in str(e)
    else:
        raise AssertionError("deberia haber fallado")


def test_pa_04b_duracion_de_no_revienta_con_archivo_invalido(tmp_path):
    assert pa.duracion_de(str(tmp_path / "no_existe.mp3")) == 0.0
