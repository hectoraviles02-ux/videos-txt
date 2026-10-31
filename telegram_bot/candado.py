# -*- coding: utf-8 -*-
"""Candado de la fábrica: una sola producción a la vez (GPU + Ollama compartidos).

Archivo `logs/_fabrica.lock` con {quien, pid, desde, inicio_proceso}. Se considera abandonado SOLO si el proceso que lo
tomó ya no existe (o su número de proceso lo reusa otro programa: distinto `inicio_proceso`). La edad ya no basta: un render
largo puede durar más de 6 h (hallazgo de Codex, 19-oct-2026). Sin psutil NO se puede verificar al dueño: el candado se
considera ocupado (falla cerrada; liberarlo es manual) en vez de suponer que fue abandonado.
"""
from __future__ import annotations
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

LOCK = Path(__file__).resolve().parent.parent / "logs" / "_fabrica.lock"


def _inicio(pid: int):
    try:
        import psutil
        return round(psutil.Process(pid).create_time(), 3)
    except Exception:
        return None


def _vivo(d: dict) -> bool | None:
    """True/False si se puede saber; None si no (sin psutil, o inicio ilegible): quien llama lo trata como ocupado."""
    try:
        import psutil
    except ImportError:
        return None
    pid = int(d.get("pid", 0))
    if not psutil.pid_exists(pid):
        return False
    esperado = d.get("inicio_proceso")
    if esperado is None:
        return True
    actual = _inicio(pid)
    if actual is None:
        return None  # el proceso existe pero no se pudo leer su inicio: estado desconocido → ocupado (Codex, 19-oct)
    return actual == esperado  # leída y distinta: mismo número pero otro programa → el dueño anterior desapareció


def quien() -> str:
    try:
        d = json.loads(LOCK.read_text(encoding="utf-8"))
        return "%s desde %s" % (d.get("quien"), time.strftime("%H:%M", time.localtime(d.get("desde", 0))))
    except Exception:
        return "nadie"


GRACIA_ESCRITURA_SEG = 10  # un candado recién creado puede leerse vacío un instante: no es «abandonado»


def ocupado() -> bool:
    try:
        d = json.loads(LOCK.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except Exception:
        try:  # ilegible (se está escribiendo): ocupado mientras sea reciente
            return time.time() - LOCK.stat().st_mtime < GRACIA_ESCRITURA_SEG
        except OSError:
            return False
    vivo = _vivo(d)
    if vivo is None:
        return True  # sin psutil no se puede verificar al dueño: falla cerrada (Codex, 19-oct)
    return vivo


def _crear(quien_soy: str) -> bool:
    """Crea el candado en UN paso atómico (O_CREAT|O_EXCL): si ya existe, falla. Antes era «revisar y luego escribir»,
    dos pasos con carrera (dos procesos entraban a la vez; hallazgo de Codex, 19-oct-2026)."""
    try:
        fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps({"quien": quien_soy, "pid": os.getpid(), "desde": time.time(), "inicio_proceso": _inicio(os.getpid())}))
    return True


def _recuperar_abandonado(quien_soy: str) -> bool:
    """Solo UN proceso a la vez puede recuperar un candado abandonado: el que crea atómicamente `<lock>.recuperando`.
    Mientras el candado abandonado exista nadie más puede crear uno nuevo (O_EXCL), así que quien recupera puede
    borrarlo sin riesgo de borrar uno vivo. Si el de recuperación quedó huérfano (> 60 s), se descarta."""
    rec = LOCK.with_name(LOCK.name + ".recuperando")
    try:
        if time.time() - rec.stat().st_mtime > 60:
            rec.unlink()
    except OSError:
        pass
    try:
        fd = os.open(str(rec), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False  # otro proceso está recuperando
    os.close(fd)
    try:
        if LOCK.exists() and not ocupado():
            LOCK.unlink()
        return _crear(quien_soy)
    except OSError:
        return False
    finally:
        try:
            rec.unlink()
        except OSError:
            pass


@contextmanager
def tomar(quien_soy: str):
    """with tomar("bot") as ok: … — ok=False si otro proceso produce."""
    LOCK.parent.mkdir(exist_ok=True)
    ok = _crear(quien_soy)
    if not ok and not ocupado():
        ok = _recuperar_abandonado(quien_soy)
    if not ok:
        yield False
        return
    try:
        yield True
    finally:
        try:
            if json.loads(LOCK.read_text(encoding="utf-8")).get("pid") == os.getpid():
                LOCK.unlink()
        except Exception:
            pass
