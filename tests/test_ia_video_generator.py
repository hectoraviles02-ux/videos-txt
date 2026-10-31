# -*- coding: utf-8 -*-
"""El modo rapido es opt-in: lo que se prueba aqui es que NO se activa solo.

No hay generacion real: eso necesita GPU y tarda minutos. Lo que se cubre es
la logica de seleccion, que es donde un descuido convierte produccion en otra
cosa sin que nadie se entere.
"""
import pytest

import pipeline.providers.ia_video_generator as M


def test_default_no_toca_produccion():
    g = M.IAVideoGenerator()
    assert g._rapido is False
    assert g._pasos == M.PASOS == 20
    assert g._guia == M.GUIA == 7.5
    assert g.name == "ia-animatediff"


def test_rapido_usa_los_valores_medidos():
    g = M.IAVideoGenerator(rapido=True)
    assert g._pasos == M.PASOS_LCM == 8
    assert g._guia == M.GUIA_LCM == 2.0
    assert g.name == "ia-animatelcm"


def test_pasos_explicito_manda_sobre_el_modo():
    assert M.IAVideoGenerator(rapido=True, pasos=8)._pasos == 8
    assert M.IAVideoGenerator(pasos=30)._pasos == 30


def test_guia_rapida_no_es_1_punto_0():
    """Bajarla a 1.0 ahorra la mitad del computo y lava la imagen un 72% de
    contraste (medido 2026-08-27). Si alguien la 'optimiza', esto lo frena."""
    assert M.GUIA_LCM > 1.0


def test_si_el_modo_rapido_no_carga_se_degrada_avisando(monkeypatch):
    """Degradar esta bien; degradar en silencio no: `name` tiene que delatar
    que el video salio en DDIM y no en LCM.

    Se simula que AnimateLCM no carga (falta `peft`, o los pesos) y que la
    carga de DDIM tampoco llega a completarse. Lo que importa es el estado en
    que queda el objeto DESPUES de la rama rapida, no que el pipeline exista.
    """
    torch = pytest.importorskip("torch")
    if not torch.cuda.is_available():
        pytest.skip("la rama rapida solo se recorre con GPU")
    import diffusers

    g = M.IAVideoGenerator(rapido=True)
    monkeypatch.setattr(g, "_cargar_rapido", lambda _t: None)

    def _no_carga(*a, **k):
        raise RuntimeError("stub: no se cargan pesos en un test")

    monkeypatch.setattr(diffusers.MotionAdapter, "from_pretrained", _no_carga)

    with pytest.raises(Exception):
        g._cargar()

    # El estado quedo corregido ANTES de intentar DDIM: eso es lo que hace
    # que el artefacto no mienta sobre como se genero.
    assert g._rapido is False
    assert g.name == "ia-animatediff"
    assert g._pasos == M.PASOS
    assert g._guia == M.GUIA


# ── El cableado: del yaml al generador ───────────────────────────────────
#
# Estos no prueban el generador sino el CAMINO. Un flag que existe pero no
# llega es peor que no tenerlo: el yaml dice una cosa y el video sale de otra.


def _config_con(video_rapido: str, tmp_path):
    """El yaml real del repo, con dos valores cambiados."""
    from telegram_bot.config import load_config
    import re
    base = open("config/telegram_bot.yaml", encoding="utf-8").read()
    p = tmp_path / "tb.yaml"
    # Se impone el valor sea cual sea el del repo: el yaml real cambia
    # segun lo que este probando el usuario, y el test no puede depender
    # de eso.
    txt = re.sub(r"video_rapido:\s*(true|false)",
                 "video_rapido: %s" % video_rapido, base)
    txt = re.sub(r'adapter:\s*"[a-z]+"', 'adapter: "ia"', txt, count=1)
    p.write_text(txt, encoding="utf-8")
    return load_config(str(p))


def _generador_interno(envoltorio):
    """`_select_adapter` devuelve un SDImageAdapter que ENVUELVE al generador."""
    g = getattr(envoltorio, "_image_generator", None)
    assert type(g).__name__ == "IAVideoGenerator", type(envoltorio).__name__
    return g


def test_el_yaml_del_repo_declara_el_modo_explicitamente():
    """El valor se lee y es booleano, no queda al azar.

    Este test comprobaba que viniera en False. El 2026-08-29 el usuario lo
    encendio a proposito para probar AnimateLCM, asi que fijar el valor
    aqui convertiria una decision suya en un test roto. Lo que si tiene
    que cumplirse es que el flag EXISTA y se lea como booleano: un yaml
    con la clave mal escrita caeria al default sin avisar.
    """
    from telegram_bot.config import load_config
    assert isinstance(load_config("config/telegram_bot.yaml").video_rapido,
                      bool)


@pytest.mark.parametrize("valor,esperado,pasos", [("true", True, 8),
                                                  ("false", False, 20)])
def test_el_flag_llega_del_yaml_al_generador(valor, esperado, pasos, tmp_path):
    import telegram_bot.handlers as H
    g = _generador_interno(H._select_adapter(_config_con(valor, tmp_path)))
    assert g._rapido is esperado
    assert g._pasos == pasos


@pytest.mark.parametrize("valor", ["true", "false"])
def test_el_respaldo_a_banco_sobrevive_al_flag(valor, tmp_path):
    """Sin `fallback_generator` una escena que falla se pierde en vez de caer
    a metraje de banco. El flag no puede costar la red de seguridad."""
    import telegram_bot.handlers as H
    g = _generador_interno(H._select_adapter(_config_con(valor, tmp_path)))
    assert g._fallback is not None
    assert type(g._fallback).__name__ == "StockVideoGenerator"
