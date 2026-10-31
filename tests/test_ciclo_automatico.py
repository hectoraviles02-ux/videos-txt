import json
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace as NS

from scripts import ciclo_diario as ciclo
from content_factory import automatico, db


def test_tendencias_real_todas_etapas_y_base_correcta(monkeypatch,tmp_path):
    orden=[]
    def run(cmd,**kw):
        orden.append(cmd)
        return NS(returncode=0,stderr="",stdout=json.dumps({"temas":[{"titulo":"t"}]}))
    monkeypatch.setattr(ciclo.subprocess,"run",run)
    monkeypatch.setattr(ciclo,"EVID",tmp_path)
    monkeypatch.setattr(ciclo,"log",lambda msg:None)
    assert ciclo.tendencias("2026-10-21",produccion=True) == [{"titulo":"t"}]
    assert len(orden)==5
    assert all("--produccion" in cmd and str(ciclo.REPO/"content_factory/content.db") in cmd for cmd in orden)


def test_fallo_ingesta_no_continua_con_datos_viejos(monkeypatch):
    llamadas=[]
    def run(cmd,**kw):
        llamadas.append(cmd)
        return NS(returncode=1,stdout="",stderr="fallo controlado")
    monkeypatch.setattr(ciclo.subprocess,"run",run)
    monkeypatch.setattr(ciclo,"log",lambda msg:None)
    assert ciclo.tendencias("2026-10-21",True)==[]
    assert len(llamadas)==1


def test_cupo_reservado_no_produce_ni_busca(monkeypatch,tmp_path):
    (tmp_path/"content_factory").mkdir()
    path=tmp_path/"content_factory/content.db"
    c=sqlite3.connect(path)
    c.execute("CREATE TABLE publicaciones (pieza_id INTEGER, publicada_en TEXT)")
    c.execute("INSERT INTO publicaciones VALUES (1,?)",(str(ciclo.date.today()),))
    c.commit();c.close()
    @contextmanager
    def lock(*a):yield True
    monkeypatch.setattr(ciclo,"REPO",tmp_path)
    monkeypatch.setattr(ciclo,"cargar_env",lambda:None)
    monkeypatch.setattr(ciclo.candado,"tomar",lock)
    monkeypatch.setattr(ciclo.sys,"argv",["ciclo","--automatico"])
    monkeypatch.setattr(automatico,"politica",lambda:{"videos_por_dia":1})
    monkeypatch.setattr(automatico,"reconciliar_pendientes",lambda *a:0)
    monkeypatch.setattr(db,"conectar",lambda *a,**kw:sqlite3.connect(path))
    def prohibido(*a,**kw):raise AssertionError("cupo excedido")
    monkeypatch.setattr(ciclo,"tendencias",prohibido)
    assert ciclo.main()==0
