"""Tests for synthesis/script_generator.py"""

import json
from unittest.mock import MagicMock, patch

import pytest

from synthesis.script_generator import FuenteInsuficienteError


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def transcript_content():
    return {
        "type": "transcript",
        "segmentos": [
            {"inicio_seg": 0.0, "texto": "Primer segmento del transcript con suficiente texto para pasar la compuerta de fuente minima."},
            {"inicio_seg": 5.0, "texto": "Segundo segmento con información adicional que aporta contenido real al conjunto."},
            {"inicio_seg": 10.0, "texto": "Tercer segmento del contenido con datos y detalles concretos del tema tratado."},
            {"inicio_seg": 15.0, "texto": "Cuarto segmento con más detalles sobre el asunto que se está desarrollando."},
            {"inicio_seg": 20.0, "texto": "Quinto segmento finalizando la exposición con una conclusión clara."},
        ],
    }


@pytest.fixture
def long_transcript():
    words = "palabra " * 150
    return {
        "type": "transcript",
        "segmentos": [
            {"inicio_seg": 0.0, "texto": words},
        ],
    }


@pytest.fixture
def script_generator():
    from synthesis.script_generator import ScriptGenerator

    return ScriptGenerator(ollama_url="http://localhost:11434", ollama_model="qwen2.5:14b")


# ── SY-01a: response JSON válido (5 escenas) ──────────────────────────


@patch("synthesis.script_generator.requests.post")
def test_sy_01a_valid_json(mock_post, script_generator, transcript_content):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "response": json.dumps({
            "title": "Video de prueba",
            "scenes": [
                {"narration": "Texto escena 1.", "visual_description": "Visual 1"},
                {"narration": "Texto escena 2.", "visual_description": "Visual 2"},
                {"narration": "Texto escena 3.", "visual_description": "Visual 3"},
                {"narration": "Texto escena 4.", "visual_description": "Visual 4"},
                {"narration": "Texto escena 5.", "visual_description": "Visual 5"},
            ],
        })
    }
    mock_post.return_value = mock_resp

    result = script_generator.generate(transcript_content)

    assert result["title"] == "Video de prueba"
    assert len(result["scenes"]) == 5
    assert result["scenes"][0]["narration"] == "Texto escena 1."
    assert result["scenes"][0]["visual_description"] == "Visual 1"


# ── SY-01b: short content < 100 palabras → single scene ──────────────


@patch("synthesis.script_generator.requests.post")
def test_sy_01b_short_content_single_scene(mock_post, script_generator):
    short_content = {
        "type": "article",
        "text": "Texto corto de menos de cien palabras para probar.",
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "response": json.dumps({
            "title": "Short Video",
            "scenes": [
                {"narration": "Expanded narration for short content.", "visual_description": "Visual desc"},
            ],
        })
    }
    mock_post.return_value = mock_resp

    # Cambio de contrato (2026-08-20): una fuente de 9 palabras ya no se
    # expande a una escena inventada. Se rechaza. El video fabricado que
    # motivo el cambio decia hablar de un link de Facebook sobre generadores
    # de video y narraba una familia en un parque.
    with pytest.raises(FuenteInsuficienteError) as exc:
        script_generator.generate(short_content)
    assert exc.value.palabras == 9


# ── SY-01b2: fuente larga pero con transcripcion no fiable ────────────


def test_sy_01b2_baja_confianza_se_rechaza(script_generator):
    """El caso real: 53 palabras con confianza 0.319 producian un video."""
    contenido = {
        "tipo": "video",
        "segmentos": [
            {"texto": " ".join(f"palabra{i}" for i in range(10)), "confianza": 0.319}
            for _ in range(6)
        ],
    }
    with pytest.raises(FuenteInsuficienteError) as exc:
        script_generator.generate(contenido)
    assert exc.value.confianza is not None
    assert exc.value.confianza < 0.45


def test_sy_01b3_fuente_suficiente_no_se_rechaza(script_generator):
    """La compuerta no debe estorbar a una fuente normal."""
    contenido = {
        "tipo": "video",
        "segmentos": [
            {"texto": " ".join(f"palabra{i}" for i in range(20)), "confianza": 0.92}
            for _ in range(5)
        ],
    }
    # No lanza: falla despues, al llamar a Ollama (que no esta mockeado aqui).
    try:
        script_generator.generate(contenido)
    except FuenteInsuficienteError:
        raise AssertionError("la compuerta rechazo una fuente valida")
    except Exception:
        pass


# ── SY-01c: connection refused → OllamaConnectionError ────────────────


@patch("synthesis.script_generator.requests.post")
def test_sy_01c_connection_refused(mock_post, script_generator, long_transcript):
    from synthesis.script_generator import OllamaConnectionError

    mock_post.side_effect = __import__("requests").ConnectionError("Connection refused")

    with pytest.raises(OllamaConnectionError, match="Cannot connect to Ollama"):
        script_generator.generate(long_transcript)


@patch("synthesis.script_generator.requests.post")
def test_sy_01d_malformed_json_retry_fails(mock_post, script_generator, long_transcript):
    from synthesis.script_generator import ScriptGenerationError

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"response": "not valid json at all"}
    mock_post.return_value = mock_resp

    with pytest.raises(ScriptGenerationError, match="Retry also failed"):
        script_generator.generate(long_transcript)


# ── SY-02a: prompt contiene format constraints ────────────────────────


def test_sy_02a_prompt_contains_constraints(script_generator):
    content = {
        "type": "article",
        "texto": "Contenido suficientemente largo para estar seguro de que supera el límite de palabras y se usa el prompt completo de varias escenas. " * 20,
    }
    prompt = script_generator._build_prompt(content)

    assert "Output ONLY valid JSON" in prompt
    assert "5 to 10 scenes" in prompt
    assert "narration" in prompt
    assert "visual_description" in prompt
    assert "NO markdown fences" in prompt


# ── SY-06a: generate_script returns JSON string with style ─────────────


@patch("synthesis.script_generator.ScriptGenerator._call_ollama_api")
def test_sy_06a_generate_script_valid(mock_api, script_generator):
    mock_api.return_value = (
        json.dumps({"title": "Test", "scenes": [{"narration": "A", "visual_description": "B"}]}),
        {"total_duration": 1e9, "eval_count": 42},
    )
    result = script_generator.generate_script("Some transcript text.", style="youtube")

    assert isinstance(result, str)
    parsed = json.loads(result)
    assert parsed["title"] == "Test"
    assert len(parsed["scenes"]) == 1
    assert "waiting_in_ms" not in result  # no markdown fences


@patch("synthesis.script_generator.ScriptGenerator._call_ollama_api")
def test_sy_06b_generate_script_all_styles(mock_api, script_generator):
    mock_api.return_value = (
        json.dumps({"title": "T", "scenes": [{"narration": "A", "visual_description": "B"}]}),
        {},
    )
    for style in ("youtube", "tutorial", "narrative"):
        result = script_generator.generate_script("Some text.", style=style)
        parsed = json.loads(result)
        assert parsed["title"] == "T"


@patch("synthesis.script_generator.ScriptGenerator._call_ollama_api")
def test_sy_06c_generate_script_raises_on_empty(mock_api, script_generator):
    from synthesis.script_generator import ScriptGenerationError

    mock_api.return_value = ('{"title":"T","scenes":[]}', {})
    with pytest.raises(ScriptGenerationError):
        script_generator.generate_script("Text.")


# ── SY-06d: generate_storyboard returns Storyboard ─────────────────────


@patch("synthesis.script_generator.ScriptGenerator._call_ollama_api")
def test_sy_06d_generate_storyboard(mock_api, script_generator):
    from adapters.base import Storyboard

    mock_api.return_value = (
        json.dumps({
            "title": "Test Video",
            "scenes": [
                {"narration": "Scene one.", "visual_description": "Visual one"},
                {"narration": "Scene two.", "visual_description": "Visual two"},
            ],
        }),
        {"eval_count": 10},
    )
    sb = script_generator.generate_storyboard("Some transcript text.")

    assert isinstance(sb, Storyboard)
    assert len(sb.scenes) == 2
    assert sb.scenes[0].narration == "Scene one."


# ── SY-06e: build_style_prompt includes style instruction ──────────────


def test_sy_06e_build_style_prompt(script_generator):
    prompt = script_generator._build_style_prompt("Hello", style="tutorial")
    assert "educational tutorial" in prompt or "step by step" in prompt
    assert "Output ONLY valid JSON" in prompt


# ── SY-06f: _call_ollama_api returns metadata ─────────────────────────


@patch("synthesis.script_generator.requests.post")
def test_sy_06f_call_api_metadata(mock_post, script_generator):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "response": json.dumps({"title": "T", "scenes": []}),
        "total_duration": 2500000000,
        "eval_count": 99,
    }
    mock_post.return_value = mock_resp

    raw, meta = script_generator._call_ollama_api("prompt")
    assert meta["total_duration"] == 2500000000
    assert meta["eval_count"] == 99
    assert "title" in raw
