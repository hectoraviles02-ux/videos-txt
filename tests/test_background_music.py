"""Música de fondo en compose_video.

Los tests de filterchain son unitarios (rápidos, sin ffmpeg). Los que comprueban
que la música SUENA renderizan de verdad y miden el audio del MP4 producido:
un video con música silenciosa pasaría cualquier prueba con mock.
"""

import os
import subprocess

import pytest

from adapters.ffmpeg_tools import (
    _build_compose_args,
    compose_video,
    resolve_ffmpeg,
)
from tests.fixtures import make_image, make_tone_wav, make_wav

SILENCIO_DB = -80.0  # por debajo de esto no hay señal audible


def _args(bgm_path=None, bgm_volume=0.1, output_duration=6.0):
    return _build_compose_args(
        ffmpeg="ffmpeg",
        scene_paths=["a.png", "b.png"],
        scene_durations=[3.0, 3.0],
        narration_path="narr.wav",
        output_path="out.mp4",
        fps=30,
        bgm_path=bgm_path,
        bgm_volume=bgm_volume,
        output_duration=output_duration,
    )


# --- filterchain -----------------------------------------------------------

def test_sin_musica_conserva_el_mapeo_de_audio_original():
    args = _args()
    assert "-stream_loop" not in args
    assert "amix" not in " ".join(args)
    # La narración es la entrada 2 (tras las 2 escenas) y se mapea directo.
    assert "2:a" in args
    assert "[a]" not in args


def test_con_musica_mapea_la_salida_del_mezclador():
    args = _args(bgm_path="bgm.mp3")
    # Si se mapeara "2:a" en vez de "[a]", el video saldría SIN música pese a
    # que ffmpeg termina en 0: es la falla silenciosa que este test previene.
    assert "[a]" in args
    assert "2:a" not in args


def test_stream_loop_va_pegado_a_su_propia_entrada():
    args = _args(bgm_path="bgm.mp3")
    i = args.index("-stream_loop")
    assert args[i + 1] == "-1"
    assert args[i + 2] == "-i"
    assert args[i + 3] == "bgm.mp3"


def test_la_narracion_conserva_su_indice_con_musica():
    """La música se añade DESPUÉS para no desplazar a la narración."""
    args = _args(bgm_path="bgm.mp3")
    fc = args[args.index("-filter_complex") + 1]
    assert "[2:a][bgm]amix" in fc  # narración=2, música=3
    assert "[3:a]volume=" in fc


def test_amix_no_normaliza():
    """normalize=1 (default de ffmpeg) atenuaría la narración a la mitad."""
    fc = _args(bgm_path="bgm.mp3")[
        _args(bgm_path="bgm.mp3").index("-filter_complex") + 1
    ]
    assert "normalize=0" in fc


def test_el_volumen_se_aplica_a_la_musica():
    fc = _args(bgm_path="bgm.mp3", bgm_volume=0.25)[
        _args(bgm_path="bgm.mp3", bgm_volume=0.25).index("-filter_complex") + 1
    ]
    assert "volume=0.250" in fc


def test_el_fade_se_ancla_a_la_duracion_de_salida():
    """Con narración más corta que las escenas, el fade debe caer dentro del
    video; anclado a la suma de escenas no se dispararía nunca."""
    args = _args(bgm_path="bgm.mp3", output_duration=4.0)
    fc = args[args.index("-filter_complex") + 1]
    assert "afade=t=out:st=1.000:d=3" in fc


def test_el_fade_no_arranca_en_negativo_con_videos_cortos():
    fc = _args(bgm_path="bgm.mp3", output_duration=1.0)[
        _args(bgm_path="bgm.mp3", output_duration=1.0).index("-filter_complex") + 1
    ]
    assert "afade=t=out:st=0.000" in fc


# --- render real -----------------------------------------------------------

def _ffmpeg_disponible():
    try:
        resolve_ffmpeg()
        return True
    except RuntimeError:
        return False


requiere_ffmpeg = pytest.mark.skipif(
    not _ffmpeg_disponible(), reason="ffmpeg no disponible"
)


def _volumen_medio(video_path, inicio=None, dur=None):
    args = [resolve_ffmpeg(), "-hide_banner"]
    if inicio is not None:
        args += ["-ss", str(inicio)]
    if dur is not None:
        args += ["-t", str(dur)]
    args += ["-i", video_path, "-af", "volumedetect", "-f", "null", "-"]
    r = subprocess.run(args, capture_output=True, text=True)
    for linea in r.stderr.splitlines():
        if "mean_volume:" in linea:
            return float(linea.split("mean_volume:")[1].strip().split()[0])
    return None


@pytest.fixture
def lab(tmp_path):
    """Narración de 6s en SILENCIO + música de 2s con tono.

    Cualquier energía de audio en la salida solo puede venir de la música, y
    cubrir 6s con 2s de música obliga a que el loop funcione.
    """
    escenas = [
        make_image(str(tmp_path / "e1.png"), 160, 120, (200, 0, 0)),
        make_image(str(tmp_path / "e2.png"), 160, 120, (0, 0, 200)),
    ]
    return {
        "escenas": escenas,
        "duraciones": [3.0, 3.0],
        "narracion": make_wav(str(tmp_path / "narr.wav"), 6.0),
        "musica": make_tone_wav(str(tmp_path / "bgm.wav"), 2.0),
        "dir": tmp_path,
    }


def _render(lab, nombre, **kwargs):
    salida = str(lab["dir"] / nombre)
    return compose_video(
        scene_paths=lab["escenas"],
        scene_durations=lab["duraciones"],
        narration_path=lab["narracion"],
        output_path=salida,
        resolution=(160, 120),
        fps=10,
        **kwargs,
    ), salida


@requiere_ffmpeg
def test_sin_musica_el_audio_queda_en_silencio(lab):
    res, salida = _render(lab, "sin.mp4")
    assert res["success"], res.get("error")
    assert res["bgm_applied"] is False
    assert res["warnings"] == []
    assert _volumen_medio(salida) < SILENCIO_DB


@requiere_ffmpeg
def test_con_musica_el_audio_es_audible(lab):
    res, salida = _render(
        lab, "con.mp4", background_music_path=lab["musica"],
        background_music_volume=0.5,
    )
    assert res["success"], res.get("error")
    assert res["bgm_applied"] is True
    assert _volumen_medio(salida) > -60


@requiere_ffmpeg
def test_la_musica_se_repite_hasta_cubrir_el_video(lab):
    """Música de 2s sobre video de 6s: debe sonar también en el 2do ciclo."""
    _, salida = _render(
        lab, "loop.mp4", background_music_path=lab["musica"],
        background_music_volume=0.5,
    )
    assert _volumen_medio(salida, 2.2, 1.5) > -60


@requiere_ffmpeg
def test_el_cierre_hace_fade(lab):
    _, salida = _render(
        lab, "fade.mp4", background_music_path=lab["musica"],
        background_music_volume=0.5,
    )
    inicio = _volumen_medio(salida, 0.2, 1.5)
    final = _volumen_medio(salida, 5.7, 0.3)
    assert final < inicio - 6


@requiere_ffmpeg
def test_musica_inexistente_no_aborta_el_video(lab):
    res, salida = _render(
        lab, "faltante.mp4",
        background_music_path=str(lab["dir"] / "no_existe.mp3"),
        background_music_volume=0.5,
    )
    assert res["success"]
    assert res["bgm_applied"] is False
    assert any("SIN MUSICA" in w for w in res["warnings"])
    assert os.path.exists(salida)


@requiere_ffmpeg
def test_musica_corrupta_degrada_y_avisa(lab):
    """Un archivo que existe pero no es audio: ffmpeg falla, se descarta el
    parcial y se reintenta sin música. El aviso lo deja registrado."""
    basura = lab["dir"] / "basura.txt"
    basura.write_text("esto no es audio")
    res, salida = _render(
        lab, "corrupta.mp4", background_music_path=str(basura),
        background_music_volume=0.5,
    )
    assert res["success"]
    assert res["bgm_applied"] is False
    assert any("SIN MUSICA" in w for w in res["warnings"])
    assert os.path.exists(salida)
    assert _volumen_medio(salida) < SILENCIO_DB


@requiere_ffmpeg
def test_volumen_cero_no_intenta_mezclar(lab):
    res, _ = _render(
        lab, "vol0.mp4", background_music_path=lab["musica"],
        background_music_volume=0.0,
    )
    assert res["success"]
    assert res["bgm_applied"] is False
    assert any("volumen" in w for w in res["warnings"])
