"""31-oct-2026 (auditoría P1): dos PROCESOS a la vez no deben subir dos veces la misma pieza.

Antes: con la misma aprobación vigente, dos procesos simultáneos llamaban 2 veces al canal (ledger con 2 ids,
BD con 1: un video huérfano). Ahora la fila se reserva con BEGIN IMMEDIATE antes de llamar al canal.
Base y ledger temporales, publicadores simulados que cuentan llamadas. Nada de red ni de content.db.
"""
from __future__ import annotations

import multiprocessing as mp
import sqlite3
import time
from pathlib import Path


TEXTO = "caption aprobado"
ESPERA = 1.5   # la subida real tarda 20-30 s; con 1.5 s basta para que se encimen


def _preparar(base: Path):
    from content_factory import db
    from tests.ayuda_aprobacion import aprobar_como_telegram
    ruta_db = base / "prueba.db"
    c = db.conectar(ruta_db)
    db.crear_esquema(c)
    tema_id = db.upsert_tema(c, {
        "hash_titulo": "ht-conc", "titulo": "CONC", "pilar": "P4", "intent": "educativo", "creado_en": "2026-10-31",
        "estado": "SELECCIONADA", "metodo_clasificacion": "legado", "evidencia": [{"legacy_id": "e1"}]})
    pid, _ = db.registrar_pieza(c, tema_id=tema_id, formato="corto", angulo="tutorial", angulo_metodo="legado",
                                pilar_decision="P4", estado="PRODUCIDA", creada_en="2026-10-31",
                                detalle_nacimiento="prueba de concurrencia")
    mp4 = base / "pieza.mp4"
    mp4.write_bytes(b"mp4 simulado")
    aprobar_como_telegram(c, pid, ruta_mp4=mp4, texto=TEXTO, canales=["facebook"])
    c.close()
    return ruta_db, pid, mp4, base / "ledger.jsonl"


def _trabajador(nombre, ruta_db, pid, mp4, ruta_ledger, contador, barrera, salida, rechaza):
    from content_factory import db, distribucion as dist

    def publicador():
        with contador.get_lock():
            contador.value += 1
        time.sleep(ESPERA)
        if rechaza:
            raise dist.RechazoDelCanal("simulado: el canal rechazó")
        return {"video_id": "VID_%s" % nombre}
    publicador.ruta_mp4, publicador.texto, publicador.programada_para = str(mp4), TEXTO, None
    conn = db.conectar(ruta_db)
    try:
        barrera.wait()
        r = dist.publicar_pieza(conn, pid, publicador=publicador, ledger=dist.Ledger(ruta_ledger),
                                fecha="2026-10-31T19:30:00-06:00")
        salida.put((nombre, r["resultado"]))
    except Exception as e:  # noqa: BLE001
        salida.put((nombre, "EXCEPCION %s: %s" % (type(e).__name__, e)))
    finally:
        conn.close()


def _correr(tmp_path, rechaza=False):
    ruta_db, pid, mp4, ledger = _preparar(tmp_path)
    ctx = mp.get_context("spawn")
    contador, salida, barrera = ctx.Value("i", 0), ctx.Queue(), ctx.Barrier(2)
    procs = [ctx.Process(target=_trabajador, args=(n, ruta_db, pid, mp4, ledger, contador, barrera, salida, rechaza))
             for n in ("P1", "P2")]
    [p.start() for p in procs]
    [p.join(60) for p in procs]
    res = dict(salida.get(timeout=10) for _ in procs)
    filas = sqlite3.connect(str(ruta_db)).execute("SELECT estado, identificador_remoto FROM publicaciones").fetchall()
    lineas = ledger.read_text(encoding="utf-8").splitlines() if ledger.exists() else []
    return contador.value, res, filas, lineas


def test_dos_procesos_a_la_vez_suben_una_sola_vez(tmp_path):
    llamadas, res, filas, lineas = _correr(tmp_path)
    assert llamadas == 1, res
    assert sorted(res.values()) == ["intento_sin_confirmar", "publicada"], res
    assert len(lineas) == 1
    assert filas == [("CONFIRMADA", lineas and __import__("json").loads(lineas[0])["identificador_remoto"])]


def test_rechazo_seguro_suelta_la_reserva_y_permite_reintentar(tmp_path):
    from content_factory import db, distribucion as dist
    llamadas, res, filas, lineas = _correr(tmp_path, rechaza=True)
    assert llamadas == 1                       # el segundo no llamó mientras el primero tenía la reserva
    assert filas == [] and lineas == []        # rechazo seguro: no queda reserva colgada
    # reintento después del rechazo: vuelve a llamar al canal y publica
    ruta_db = tmp_path / "prueba.db"
    conn = db.conectar(ruta_db)
    pid = conn.execute("SELECT id FROM piezas").fetchone()[0]

    def publicador():
        return {"video_id": "VID_REINTENTO"}
    publicador.ruta_mp4, publicador.texto, publicador.programada_para = str(tmp_path / "pieza.mp4"), TEXTO, None
    r = dist.publicar_pieza(conn, pid, publicador=publicador, ledger=dist.Ledger(tmp_path / "ledger.jsonl"),
                            fecha="2026-10-31T19:40:00-06:00")
    assert r["resultado"] == "publicada"
    conn.close()
