"""StockVideoGenerator: dos escenas con la misma consulta no comparten clip.

El defecto que estos tests fijan: `generate()` cacheaba la RUTA del clip por
consulta y devolvía la misma para la segunda escena, esquivando la guarda
`_usados` que existe justo para impedir eso. El caché ahora guarda el
resultado de BÚSQUEDA — ahorra la llamada a la API sin repetir el metraje.
"""

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pipeline.providers.stock_video_generator import StockVideoGenerator


def _hit(id_, tags="puppy rescue dog"):
    return {
        "id": id_,
        "tags": tags,
        "videos": {"large": {"url": f"https://ejemplo/{id_}.mp4", "width": 1920}},
    }


def _escena(id_, keywords, narracion=""):
    return SimpleNamespace(
        id=id_,
        visual={"search_keywords": keywords},
        narration=narracion,
        title="",
    )


class _RespuestaFalsa:
    """Descarga simulada: 200 KB, por encima del mínimo que exige el generador."""

    status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, _n):
        yield b"x" * 200_000


@pytest.fixture
def gen():
    return StockVideoGenerator()


@pytest.fixture
def storyboard_consulta_repetida():
    """Dos escenas distintas cuyas keywords normalizan a la MISMA consulta."""
    return SimpleNamespace(scenes=[
        _escena("s1", "puppy rescue hands warmth"),
        _escena("s2", "puppy rescue hands warmth"),
    ])


@patch.dict(os.environ, {"PIXABAY_API_KEY": "clave-falsa", "PEXELS_API_KEY": ""})
def test_dos_escenas_con_igual_consulta_reciben_clips_distintos(
    gen, storyboard_consulta_repetida, tmp_path
):
    """Recorre el código REAL de _descargar_primero: solo se simula la red."""
    hits = [_hit(101), _hit(202)]

    with patch.object(gen, "_buscar", return_value=hits) as mock_buscar, \
         patch.object(gen, "_elegir_con_modelo", side_effect=lambda c, *_: c), \
         patch("pipeline.providers.stock_video_generator.requests.get",
               return_value=_RespuestaFalsa()):
        resultados = gen.generate(storyboard_consulta_repetida, str(tmp_path))

    assert len(resultados) == 2
    # Lo que fallaba: ambas escenas apuntaban al MISMO archivo.
    assert resultados["s1"] != resultados["s2"]
    # La guarda _usados vuelve a operar: cada escena consumió un clip distinto.
    assert gen._usados == {101, 202}
    assert mock_buscar.call_count == 1, "la busqueda se cachea, no se repite"


@patch.dict(os.environ, {"PIXABAY_API_KEY": "clave-falsa", "PEXELS_API_KEY": ""})
def test_la_busqueda_se_cachea_por_consulta(gen):
    with patch.object(gen, "_buscar", return_value=[_hit(1), _hit(2)]) as mock_buscar, \
         patch.object(gen, "_elegir_con_modelo", side_effect=lambda c, *_: c):
        gen._candidatos("puppy rescue", "clave-falsa")
        gen._candidatos("puppy rescue", "clave-falsa")
        gen._candidatos("otra cosa distinta", "clave-falsa")

    assert mock_buscar.call_count == 2, "una llamada por consulta distinta"


@patch.dict(os.environ, {"PIXABAY_API_KEY": "clave-falsa", "PEXELS_API_KEY": ""})
def test_el_cache_no_deja_que_el_llamador_mute_lo_guardado(gen):
    """Se devuelve una copia: mutar el resultado no corrompe el caché."""
    with patch.object(gen, "_buscar", return_value=[_hit(1), _hit(2)]), \
         patch.object(gen, "_elegir_con_modelo", side_effect=lambda c, *_: c):
        primeros = gen._candidatos("puppy rescue", "clave-falsa")
        primeros.clear()
        segundos = gen._candidatos("puppy rescue", "clave-falsa")

    assert len(segundos) == 2


@patch.dict(os.environ, {"PIXABAY_API_KEY": "clave-falsa", "PEXELS_API_KEY": ""})
def test_sin_clip_disponible_la_escena_va_al_fallback(gen, tmp_path):
    """Agotados los candidatos, la escena cae al generador de imágenes."""
    class _Fallback:
        def generate(self, sb, out):
            return {s.id: f"{out}/{s.id}.png" for s in sb.scenes}

    gen._fallback = _Fallback()
    sb = SimpleNamespace(scenes=[_escena("s1", "puppy rescue hands")])

    with patch.object(gen, "_descargar_primero", return_value=None):
        resultados = gen.generate(sb, str(tmp_path))

    assert resultados["s1"].endswith("s1.png")


class _Ollama0:
    """Ollama simulado: el modelo se ABSTIENE (responde 0 = ninguno sirve)."""

    status_code = 200

    def json(self):
        return {"response": "0"}


@patch.dict(os.environ, {"PIXABAY_API_KEY": "clave-falsa", "PEXELS_API_KEY": ""})
def test_si_el_modelo_se_abstiene_la_escena_no_baja_un_clip_al_azar(gen, tmp_path):
    """Discriminante del defecto observado en #5/#11/#14/#15 (2026-09-22).

    Cuando el selector LLM responde 0 ("ninguno de estos clips sirve"), la
    escena NO debe quedarse con el primer candidato: debe caer al fallback de
    imágenes. El defecto: `_elegir_con_modelo` devolvía la lista intacta ante
    la abstención (idx = -1, misma rama que un fallo de red) y
    `_descargar_primero` bajaba el primer clip —el metraje ajeno (gimnasia para
    'excel')— sin que nadie verificara nada.
    """
    class _Fallback:
        def generate(self, sb, out):
            return {s.id: f"{out}/{s.id}.png" for s in sb.scenes}

    gen._fallback = _Fallback()
    sb = SimpleNamespace(scenes=[
        _escena("s1", "excel sheet colored cells", "datos duplicados en la hoja")])
    # Dos candidatos cuyos tags NO coinciden con la consulta: el modelo, bien
    # informado, respondería 0. El primero imita el clip de gimnasia real.
    hits = [_hit(44098, "health fitness sports gymnastics balls"),
            _hit(1234, "office desk laptop")]

    with patch.object(gen, "_buscar", return_value=hits), \
         patch("pipeline.providers.stock_video_generator.requests.post",
               return_value=_Ollama0()), \
         patch("pipeline.providers.stock_video_generator.requests.get",
               return_value=_RespuestaFalsa()):
        resultados = gen.generate(sb, str(tmp_path))

    assert resultados["s1"].endswith("s1.png"), (
        "el selector se abstuvo (0) pero la escena se quedó con un clip de "
        "video en vez de caer al fallback")
    assert gen._usados == set(), "no se debió consumir ningún clip"


def test_sin_api_key_todo_va_al_fallback(gen, tmp_path):
    class _Fallback:
        def generate(self, sb, out):
            return {s.id: f"{out}/{s.id}.png" for s in sb.scenes}

    gen._fallback = _Fallback()
    sb = SimpleNamespace(scenes=[_escena("s1", "puppy rescue hands")])

    with patch.dict(os.environ, {"PIXABAY_API_KEY": ""}):
        resultados = gen.generate(sb, str(tmp_path))

    assert resultados["s1"].endswith("s1.png")
