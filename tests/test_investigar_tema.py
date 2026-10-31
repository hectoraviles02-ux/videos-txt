# -*- coding: utf-8 -*-
"""Coordinador de investigacion (22-oct-2026): sin red, modelo y juez simulados."""
import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import investigar_tema as IT  # noqa: E402
from synthesis import gate_fidelidad as G  # noqa: E402

PAG = ("In February 2016, Bangladesh Bank became the victim of a computer intrusion that caused a loss of "
       "approximately $81,000,000. The $20,000,000 sent to Sri Lanka was stopped by the recipient bank. "
       "The majority of the $81,000,000 has not been recovered to date. Unrelated sentence about the weather.")
REG = {"segmentos": [{"texto": "Cover page of the complaint with nothing relevant here at all.", "pagina": 1},
                     {"texto": PAG, "pagina": 60}]}


def juez_literal(fuente, afirmaciones):
    """Juez simulado: respalda solo si la afirmacion aparece casi literal en la fuente."""
    out = []
    for a in afirmaciones:
        ors = G._oraciones(" ".join(fuente.split()))
        hit = [o for o in ors if G._norm(a.rstrip(".")) in G._norm(o)]
        out.append({"clasificacion": "RESPALDADA", "fragmento": hit[0], "motivo": "sim", "puntajes": {}}
                   if hit else {"clasificacion": "SIN_EVIDENCIA", "fragmento": "", "motivo": "sim", "puntajes": {}})
    return out


def test_tipo_de_fuente():
    assert IT.tipo_fuente("https://www.justice.gov/x.pdf") == "primaria"
    assert IT.tipo_fuente("https://www.bbc.com/news/a") == "prensa"
    assert IT.tipo_fuente("https://en.wikipedia.org/wiki/X") == "terciaria"
    assert IT.tipo_fuente("https://thecybersecguru.com/a") == "blog"
    assert "terciaria" not in IT.CUENTAN and "blog" not in IT.CUENTAN


def test_pasajes_exigen_el_caso_y_declaran_lo_no_leido():
    p, cob = IT.pasajes(REG, ["Bangladesh"], {"montos": ["$"], "bloqueo": ["stopped"]}, maximo=1)
    assert len(p) == 1 and p[0]["pagina"] == 60
    assert cob["paginas_total"] == 2 and 1 not in cob["paginas_con_el_caso"]


def test_fidelidad_es_literal_en_su_pagina():
    assert IT.fidelidad("The $20,000,000 sent to Sri Lanka was stopped by the recipient bank", REG, 1)["ok"]
    assert not IT.fidelidad("The $25,000,000 sent to Sri Lanka was stopped by the recipient bank", REG, 1)["ok"]
    assert not IT.fidelidad("The $20,000,000 sent to Sri Lanka was stopped by the recipient bank", REG, 0)["ok"]


def _claim(fiel=True, verdad=G.RESPALDADA, fuentes=None):
    return {"afirmacion": "x", "fidelidad": {"ok": fiel}, "veracidad": {"clasificacion": verdad},
            "fuentes": fuentes or [{"url": "https://www.justice.gov/a", "medio": "justice.gov",
                                    "cita_textual": "x"}]}


def test_certeza_no_sube_con_solo_fidelidad():
    assert IT.certeza(_claim(fiel=True, verdad=G.SIN_EVIDENCIA)) == "NO-CONFIRMADO"
    assert IT.certeza(_claim(fiel=False, verdad=G.RESPALDADA)) == "NO-CONFIRMADO"
    assert IT.certeza(_claim()) == "RESPALDADO"


def test_eco_de_la_acusacion_no_cuenta_como_independiente():
    c = _claim(fuentes=[{"url": "https://www.justice.gov/a", "medio": "justice.gov", "cita_textual": "x"},
                        {"url": "https://www.bbc.com/b", "medio": "bbc.com", "cita_textual": "y",
                         "eco_declaracion": True},
                        {"url": "https://www.philstar.com/c", "medio": "philstar.com", "cita_textual": "z",
                         "eco_declaracion": True}])
    assert IT.E.fuentes_independientes(c) == 2     # la propia + UNA por todos los ecos


def test_contraste_marca_eco_y_no_usa_pistas():
    c = {"afirmacion": "The $20,000,000 sent to Sri Lanka was stopped by the recipient bank",
         "fuentes": [{"url": "https://www.justice.gov/a"}]}
    otras = [{"url": "https://www.bbc.com/b", "medio": "bbc.com", "tipo": "prensa",
              "texto_pasajes": "According to the FBI complaint, the $20,000,000 sent to Sri Lanka was stopped by "
                               "the recipient bank after a check."},
             {"url": "https://en.wikipedia.org/wiki/X", "medio": "wikipedia", "tipo": "terciaria",
              "texto_pasajes": "The $20,000,000 sent to Sri Lanka was stopped by the recipient bank."}]
    IT.contrastar(c, otras, juez_literal)
    assert [f["medio"] for f in c["fuentes"][1:]] == ["bbc.com"]
    assert c["fuentes"][1]["eco_declaracion"] is True


def test_cronologia_solo_con_fecha_literal_en_la_cita():
    ok = {"id": "c1", "afirmacion": "loss in February 2016", "nivel_certeza": "RESPALDADO",
          "fuentes": [{"url": "u", "cita_textual": "In February 2016, Bangladesh Bank became the victim"}]}
    sin = {"id": "c2", "afirmacion": "it happened on February 4, 2016", "nivel_certeza": "RESPALDADO",
           "fuentes": [{"url": "u", "cita_textual": "the transfers were sent overnight"}]}
    no_conf = dict(ok, id="c3", nivel_certeza="NO-CONFIRMADO")
    ev = IT.cronologia([ok, sin, no_conf])
    assert [(e["claim_id"], e["fecha"]) for e in ev] == [("c1", "February 2016")]


def test_mutaciones_son_falsas_y_se_cuentan():
    m = dict(IT.mutaciones("The $20,000,000 sent in 2016 was stopped by the recipient bank"))
    assert set(m) == {"cifra", "año", "negacion"}
    assert "2013" in m["año"] and "was not stopped" in m["negacion"]

    def juez_credulo(fuente, afirmaciones):   # acepta todo con un fragmento literal
        return [{"clasificacion": "RESPALDADA", "fragmento": G._oraciones(fuente)[0], "puntajes": {}}
                for _ in afirmaciones]
    claims = [{"id": "c1", "nivel_certeza": "RESPALDADO",
               "afirmacion": "The $20,000,000 sent to Sri Lanka was stopped by the recipient bank",
               "fuentes": [{"cita_textual": "The $20,000,000 sent to Sri Lanka was stopped by the recipient bank."}]}]
    r = IT.sondas_mutadas(claims, juez_credulo)
    assert r["pruebas"] == 2
    # la cifra cambiada la atrapa la revision determinista de cifras aunque el juez sea credulo
    assert all(p["resultado"] != G.RESPALDADA for p in r["detalle"] if p["tipo"] == "cifra")


def test_pagina_anti_robot_no_es_fuente(tmp_path, monkeypatch):
    reg = {"segmentos": [{"texto": "Please wait bm-verify=AAQ enable javascript " * 40}], "avisos": []}
    j = tmp_path / "x.json"
    j.write_text(json.dumps(reg), encoding="utf-8")
    monkeypatch.setattr(IT, "_json_de", lambda url: j)
    assert IT.obtener("https://www.justice.gov/opa/pr/x")["estado"] == "pagina_no_valida"


def test_corrida_completa_simulada_bloquea_si_falta_evidencia(tmp_path, monkeypatch):
    caso = {"caso": "prueba_sim", "pregunta": "¿Qué pasó?", "palabras_caso": ["Bangladesh"],
            "subpreguntas": {"montos": ["$"], "bloqueo": ["stopped"]},
            "semillas": [{"url": "https://www.justice.gov/a.pdf"}, {"url": "https://en.wikipedia.org/wiki/X"}],
            "consultas": [], "sondas": [{"texto": "Se recuperaron 81 millones", "origen": "agosto"}]}
    cj = tmp_path / "caso.json"
    cj.write_text(json.dumps(caso), encoding="utf-8")
    monkeypatch.setattr(IT.E, "EXPDIR", tmp_path / "exp")
    monkeypatch.setattr(IT, "BASE", tmp_path)

    def extraer(fuente, llm, juez, medio="", max_reintentos=1):
        cita = "The $20,000,000 sent to Sri Lanka was stopped by the recipient bank"
        return {"llamadas": 1, "rechazados": [], "claims": [
            {"afirmacion": cita, "fuentes": [{"cita_textual": cita}]},
            {"afirmacion": "Invented claim", "fuentes": [{"cita_textual": "this quote does not exist anywhere"}]}]}
    exp = IT.correr(cj, llm=object(), juez=juez_literal, buscar=lambda q, n: [],
                    obtener_fn=lambda url: {"estado": "ok", "json": "x", "reg": REG}, extraer=extraer)
    certs = {c["afirmacion"][:10]: c["nivel_certeza"] for c in exp["claims"]}
    assert certs["The $20,00"] == "RESPALDADO" and certs["Invented c"] == "NO-CONFIRMADO"
    assert exp["estado"] != "COMPLETO"                       # 0 eventos de cronologia: A4 falla
    assert not exp["criterios"]["A4"]["ok"]
    assert [f["estado"] for f in exp["fuentes_detalle"]] == ["ok", "pista_no_cuenta"]
    assert (tmp_path / "exp" / "prueba_sim" / "EXPEDIENTE.md").read_text(encoding="utf-8").count("## ") >= 10


def test_sonda_sin_anclas_compartidas_no_se_da_por_contradicha():
    def juez_absurdo(fuente, afirmaciones):   # «contradice» con un fragmento de otro tema (visto en corrida 1)
        return [{"clasificacion": "CONTRADICHA", "fragmento": "The malware evtdiag.exe was configured.",
                 "puntajes": {"contradice": 0.98}} for _ in afirmaciones]
    fuentes = [{"url": "https://www.justice.gov/a", "tipo": "primaria", "texto_pasajes": "x"}]
    r = IT.sondas_agosto([{"texto": "Una impresora empezó a parpadear en Bangladesh", "origen": "agosto"}],
                         fuentes, juez_absurdo)[0]
    assert r["clasificacion"].startswith("SIN_RELACION") and r["veredicto_nli"] == "CONTRADICHA"

    def juez_cifra(fuente, afirmaciones):
        return [{"clasificacion": "CONTRADICHA", "fragmento": "The majority of the $81,000,000 has not been "
                 "recovered to date.", "puntajes": {}} for _ in afirmaciones]
    r2 = IT.sondas_agosto([{"texto": "Se recuperaron 81 millones de dólares", "origen": "agosto"}],
                          fuentes, juez_cifra)[0]
    assert r2["clasificacion"] == "CONTRADICHA" and r2["anclas_compartidas"]
