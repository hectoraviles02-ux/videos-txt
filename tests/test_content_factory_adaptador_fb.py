# -*- coding: utf-8 -*-
"""Cableado del publicador REAL de Facebook al flujo de distribucion.

Cambio SDD: content-factory-produccion-y-distribucion (E2).
(a) unit con cliente simulado  ·  (b) integracion estructural del adaptador real
·  (c) prueba REAL pendiente por credencial/token (no se publica en pruebas).
"""
from __future__ import annotations


import pytest

from content_factory import adaptador_fb


def test_b_integracion_estructural_carga_el_publicador_real():
    """El publicador real se carga por ruta y expone `publicar_uno` invocable.
    NO hace ninguna llamada de red (solo importa el modulo)."""
    pu = adaptador_fb.cargar_publicar_uno()
    assert callable(pu)
    assert adaptador_fb.RUTA_PUBLICADOR.name == "publicar_reels_fb.py"
    assert adaptador_fb.RUTA_PUBLICADOR.is_file()


def test_a_construir_publicador_mapea_pieza_a_item_y_no_publica():
    """Con un cliente SIMULADO: el callable mapea la pieza al `item` esperado por el
    publicador existente y devuelve video_id/url, sin tocar la red."""
    capturado = {}

    def publicar_uno_simulado(page_id, token, item):
        capturado["page_id"] = page_id
        capturado["token"] = token
        capturado["item"] = item
        return ("555", {"status": "success"})

    publicar = adaptador_fb.construir_publicador(
        mp4_path="/ruta/e12_intercambiar.mp4", caption="hola #python",
        page_id="PAGE", token="TOK", publicar_uno=publicar_uno_simulado)
    res = publicar()

    assert res["video_id"] == "555"
    assert res["url"] == "/reel/555/"
    assert capturado["page_id"] == "PAGE" and capturado["token"] == "TOK"
    assert capturado["item"]["file"] == "/ruta/e12_intercambiar.mp4"
    assert capturado["item"]["id"] == "e12_intercambiar"
    assert capturado["item"]["caption"] == "hola #python"


def test_a_adaptador_enchufa_en_distribucion_con_mock(tmp_path):
    """El callable del adaptador (con cliente simulado) funciona como `publicador`
    de `distribucion.publicar_pieza` end-to-end, sin red."""
    from content_factory import db, distribucion as dist
    conn = db.conectar(tmp_path / "p.db")
    db.crear_esquema(conn)
    tid = db.upsert_tema(conn, {"hash_titulo": "h", "titulo": "T", "pilar": "P4",
                                "intent": "educativo", "creado_en": "2026-10-05",
                                "estado": "SELECCIONADA", "metodo_clasificacion": "legado",
                                "evidencia": []})
    pid, _ = db.registrar_pieza(conn, tema_id=tid, formato="corto", angulo="t",
                                angulo_metodo="legado", pilar_decision="P4",
                                estado="PRODUCIDA", creada_en="2026-10-05",
                                detalle_nacimiento="x")
    # v5: sin aprobacion vigente el distribuidor rechaza; se aprueba ESTE archivo/texto.
    mp4 = tmp_path / "e12.mp4"
    mp4.write_bytes(b"mp4 simulado")
    from tests.ayuda_aprobacion import aprobar_como_telegram
    aprobar_como_telegram(conn, pid, ruta_mp4=mp4, texto="cap", canales=["facebook"])
    publicar = adaptador_fb.construir_publicador(
        mp4_path=str(mp4), caption="cap", page_id="P", token="T",
        publicar_uno=lambda page, tok, item: ("42", {}))
    res = dist.publicar_pieza(conn, pid, publicador=publicar,
                              ledger=dist.Ledger(tmp_path / "l.jsonl"), fecha="2026-10-05")
    assert res["resultado"] == "publicada"
    assert db.obtener_publicacion(conn, pid, "facebook")["identificador_remoto"] == "42"
    conn.close()


@pytest.mark.skip(reason="prueba REAL pendiente por credencial/token FB; no se publica en pruebas")
def test_c_publicacion_real_pendiente_por_credencial():
    """Placeholder: la publicacion REAL en Facebook requiere FB_PAGE_TOKEN valido y
    red; se ejecuta manualmente fuera de la suite (no publica en CI)."""
    raise NotImplementedError
