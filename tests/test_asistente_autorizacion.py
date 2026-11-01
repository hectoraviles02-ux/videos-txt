"""31-oct-2026 (auditoría de seguridad): el bot solo obedece al operador, en un chat permitido.

Antes, router_texto (grupo -1) no revisaba quién escribía: cualquier usuario de Telegram podía disparar
investigar_web, producir_reel (ejecuta código generado por IA) y el ciclo diario. Y una lista vacía
dejaba pasar a todos. Ahora: denegación por defecto (config.autorizar), revisando chat.id Y user.id.
Ninguna prueba toca Telegram, Ollama, la red ni publica nada: todo está simulado.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

asistente = pytest.importorskip("telegram_bot.asistente")
from telegram.ext import ApplicationHandlerStop  # noqa: E402

from telegram_bot import config as cfgmod, handlers, reels_review  # noqa: E402

OPERADOR, GRUPO, EXTRANO = 111, -222, 999


@pytest.fixture(autouse=True)
def operador(monkeypatch):
    monkeypatch.setenv("VIDEOS_TXT_TELEGRAM_CHAT_ID", str(OPERADOR))


class _Mensaje:
    def __init__(self, texto):
        self.text = texto
        self.respuestas = []

    async def reply_text(self, t, **kw):
        self.respuestas.append(t)


def _update(chat_id, user_id, texto="hazme un reel de listas"):
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), effective_user=SimpleNamespace(id=user_id),
                           message=_Mensaje(texto), callback_query=None)


def _context(permitidos=(OPERADOR, GRUPO)):
    cfg = SimpleNamespace(allowed_chat_ids=list(permitidos))
    return SimpleNamespace(bot_data={"config": cfg}, chat_data={},
                           application=SimpleNamespace(bot_data={"config": cfg}))


@pytest.fixture
def espias(monkeypatch):
    """Sustituye lo peligroso por espías: si algo se llama, queda anotado (y nada real ocurre)."""
    llamadas = []
    monkeypatch.setattr(asistente.pr, "producir", lambda t: llamadas.append(("producir", t)) or {"error": "simulado"})
    monkeypatch.setattr(asistente.iw, "investigar", lambda t: llamadas.append(("investigar", t)) or {"fuentes": []})
    monkeypatch.setattr(asistente.iw, "formato_telegram", lambda r: "ok")
    monkeypatch.setattr(asistente.candado, "ocupado", lambda: False)
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: llamadas.append(("subproceso", a[0][-1])))
    return llamadas


ORDENES = {"investigar": "investiga la ameba de Mérida",
           "producir_reel": "hazme un reel de listas en python",
           "ciclo_diario": "ciclo"}


def _mandar(update, context):
    try:
        asyncio.run(asistente.router_texto(update, context))
    except ApplicationHandlerStop:
        pass


@pytest.mark.parametrize("orden", list(ORDENES))
def test_desconocido_en_chat_privado_no_ejecuta_nada(espias, orden):
    u = _update(EXTRANO, EXTRANO, ORDENES[orden])
    _mandar(u, _context())
    assert espias == [] and u.message.respuestas == []


@pytest.mark.parametrize("orden", list(ORDENES))
def test_desconocido_dentro_de_un_grupo_permitido_tampoco(espias, orden):
    """chat.id permitido no basta: quien escribe (user.id) tiene que ser el operador."""
    u = _update(GRUPO, EXTRANO, ORDENES[orden])
    _mandar(u, _context())
    assert espias == [] and u.message.respuestas == []


@pytest.mark.parametrize("orden", list(ORDENES))
def test_lista_vacia_bloquea_todo_incluso_al_operador(espias, orden):
    _mandar(_update(OPERADOR, OPERADOR, ORDENES[orden]), _context(permitidos=()))
    assert espias == []


def test_sin_operador_configurado_nadie_pasa(espias, monkeypatch):
    monkeypatch.delenv("VIDEOS_TXT_TELEGRAM_CHAT_ID")
    _mandar(_update(OPERADOR, OPERADOR, ORDENES["producir_reel"]), _context())
    assert espias == []


@pytest.mark.parametrize("orden,esperado", [("investigar", "investigar"), ("producir_reel", "producir"),
                                            ("ciclo_diario", "subproceso")])
def test_el_operador_conserva_sus_funciones(espias, orden, esperado):
    _mandar(_update(OPERADOR, OPERADOR, ORDENES[orden]), _context())
    assert [c[0] for c in espias] == [esperado]


def test_start_de_desconocido_no_muestra_menu():
    u = _update(EXTRANO, EXTRANO, "/start")
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(asistente.start(u, _context()))
    assert u.message.respuestas == []


def test_authorization_filter_y_require_auth_niegan_por_defecto():
    assert handlers.authorization_filter(_update(OPERADOR, OPERADOR), SimpleNamespace(allowed_chat_ids=[])) is False
    assert handlers.authorization_filter(_update(GRUPO, EXTRANO), SimpleNamespace(allowed_chat_ids=[GRUPO])) is False
    assert handlers.authorization_filter(_update(OPERADOR, OPERADOR),
                                         SimpleNamespace(allowed_chat_ids=[OPERADOR])) is True
    corrio = []

    @handlers.require_auth
    async def protegido(update, context):
        corrio.append(1)
    sin_config = SimpleNamespace(application=SimpleNamespace(bot_data={}))
    asyncio.run(protegido(_update(OPERADOR, OPERADOR), sin_config))
    assert corrio == []


def test_botones_de_revision_rechazan_a_desconocidos():
    respuestas = []

    class _Q:
        data = "rv|pub|e1"

        async def answer(self, texto=None, **kw):
            respuestas.append(texto)
    u = _update(GRUPO, EXTRANO)
    u.callback_query = _Q()
    asyncio.run(reels_review.on_callback(u, _context()))
    assert respuestas == ["No autorizado"]


def test_el_motivo_del_rechazo_no_expone_identificadores(caplog):
    with caplog.at_level("WARNING"):
        assert cfgmod.autorizado(_update(GRUPO, EXTRANO), SimpleNamespace(allowed_chat_ids=[GRUPO])) is False
    assert "usuario no es el operador" in caplog.text
    assert str(EXTRANO) not in caplog.text and str(OPERADOR) not in caplog.text
