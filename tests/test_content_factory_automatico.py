import json
import pytest
from content_factory import automatico as auto, db, distribucion as dist
from tests.ayuda_aprobacion import aprobar_como_telegram


@pytest.fixture
def caso(tmp_path):
    conn = db.conectar(tmp_path / "cola.db")
    db.crear_esquema(conn)
    tid = db.upsert_tema(conn, dict(hash_titulo="tema", titulo="Tema", pilar="P4",
        intent="educativo", creado_en="2026-10-21", estado="SELECCIONADA",
        metodo_clasificacion="prueba", evidencia=[]))
    pid, _ = db.registrar_pieza(conn, tema_id=tid, formato="corto", angulo="t",
        angulo_metodo="prueba", pilar_decision="P4", estado="PRODUCIDA",
        creada_en="2026-10-21", detalle_nacimiento="artefacto simulado")
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video original")
    llamadas = []
    def pub():
        llamadas.append(1)
        return {"video_id": "123"}
    pub.ruta_mp4, pub.texto = str(video), "texto"
    cfg = tmp_path / "politica.json"
    cfg.write_text(json.dumps(dict(activa=True, version="test", palabras_operador="GO",
        apis_pagadas=False, pilares=["P4"], canales=["facebook"])))
    ev = dict(juez={"veredicto":"OK"}, tecnica={"audio": True}, sha256=db.sha256_archivo(video))
    def run():
        return auto.publicar(conn, pid, pub, canal="facebook", fecha="2026-10-21",
            evidencia=ev, ledger=dist.Ledger(tmp_path / "ledger.jsonl"), config=cfg)
    yield conn, pid, video, pub, cfg, ev, llamadas, run
    conn.close()


def test_sin_aprobacion_de_operador_la_ruta_automatica_no_publica(caso):
    # Etapa 1: antes esta prueba demostraba que la frase guardada autorizaba publicar.
    conn, pid, *_, llamadas, run = caso
    with pytest.raises(dist.SinAprobacionValida):
        run()
    assert llamadas == []
    assert conn.execute("SELECT COUNT(*) FROM aprobaciones").fetchone()[0] == 0


def test_con_aprobacion_de_operador_publica_una_vez(caso):
    conn, pid, video, pub, *_, llamadas, run = caso
    aprobar_como_telegram(conn, pid, ruta_mp4=video, texto=pub.texto, canales=["facebook"])
    assert run()["resultado"] == "publicada"
    assert run()["resultado"] == "ya_publicada"
    assert llamadas == [1]


@pytest.mark.parametrize("campo,valor", [("activa",False),("canales",["youtube"]),
    ("pilares",["P1"]),("palabras_operador",""),("apis_pagadas",True)])
def test_politica_rechaza_sin_llamar(caso, campo, valor):
    _,_,_,_,cfg,_,llamadas,run = caso
    p=json.loads(cfg.read_text()); p[campo]=valor; cfg.write_text(json.dumps(p))
    with pytest.raises(ValueError): run()
    assert llamadas == []


@pytest.mark.parametrize("campo,valor", [("juez",{"veredicto":"FALLA"}),
    ("tecnica",{}),("sha256","otro")])
def test_evidencia_rechaza(caso,campo,valor):
    *_,ev,llamadas,run=caso
    ev[campo]=valor
    with pytest.raises(ValueError): run()
    assert llamadas == []


def test_no_resucita_aprobacion_invalidada(caso):
    conn,pid,video,pub,_,_,llamadas,run=caso
    ap=aprobar_como_telegram(conn,pid,ruta_mp4=video,texto=pub.texto,canales=["facebook"])
    db.invalidar_aprobacion(conn,ap,"2026-10-21","video cambiado")
    with pytest.raises(dist.SinAprobacionValida): run()
    assert llamadas == []


@pytest.mark.parametrize("respuesta", ["error", "procesando", "publicado"])
def test_wrapper_reserva_sanitiza_y_confirma(caso, monkeypatch, tmp_path, respuesta):
    from types import SimpleNamespace as NS
    conn,pid,video,_,cfg,ev,_,_=caso
    monkeypatch.setattr(auto,"RAIZ",tmp_path)
    monkeypatch.setenv("FB_PAGE_ID","pagina")
    monkeypatch.setenv("FB_PAGE_TOKEN","SENTINELA_NO_SECRETO")
    subidas=[]
    legado={}
    def subir(*args,**kw):
        assert db.obtener_publicacion(conn,pid,"facebook")["estado"] == db.PUB_PENDIENTE
        subidas.append(1)
        return "987",{}
    fb=NS(cargar_env=lambda:None,publicados=lambda:legado,guardar=lambda d:legado.update(d),
        resolver_token_pagina=lambda p,t:t,publicar_uno=subir,API="https://ejemplo.invalid")
    def get(*args,**kw):
        if respuesta == "error": raise RuntimeError("URL access_token=SENTINELA_NO_SECRETO")
        return NS(status_code=200,json=lambda:{"published":respuesta == "publicado"})
    pub=auto.facebook(conn,pid,ruta=video,texto="texto",eid="e_test",fecha="2026-10-21",
        config=cfg,_fb=fb,_http=NS(get=get))
    aprobar_como_telegram(conn,pid,ruta_mp4=video,texto="texto",canales=["facebook"])
    def run():
        return auto.publicar(conn,pid,pub,canal="facebook",fecha="2026-10-21",
            evidencia=ev,ledger=dist.Ledger(tmp_path/"registro.jsonl"),config=cfg)
    res=run()
    assert res["resultado"] == ("publicada" if respuesta == "publicado" else "resultado_incierto")
    assert "SENTINELA_NO_SECRETO" not in str(res)
    assert "SENTINELA_NO_SECRETO" not in str(conn.execute("SELECT detalle FROM eventos").fetchall())
    assert db.obtener_publicacion(conn,pid,"facebook")["identificador_remoto"] == "987"
    run()
    assert subidas == [1]
    if respuesta == "procesando":
        respuesta="publicado"
        monkeypatch.setattr(auto,"facebook",lambda *a,**kw:pub)
        assert auto.reconciliar_pendientes(conn,"2026-10-21",config=cfg) == 0
        assert subidas == [1]
