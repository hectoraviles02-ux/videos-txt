# -*- coding: utf-8 -*-
"""
Contrato PREVISUALIZACION vs ENTREGABLE (content-addressed).
Verifica: NO APTO no deja constancia; APTO deja constancia con sha coincidente;
+1 byte invalida; F5 APTO sin editorial NO es ENTREGABLE; con editorial si.
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))
import verificar_entregable as VE  # noqa: E402


def _redir(monkeypatch, tmp_path):
    monkeypatch.setattr(VE, "REG_F5", tmp_path / "registro_f5")
    monkeypatch.setattr(VE, "REG_EDIT", tmp_path / "registro_editorial")
    (tmp_path / "registro_editorial").mkdir()


def _video(tmp_path, data=b"VIDEOBYTES-DOCUMENTAL"):
    v = tmp_path / "v.mp4"
    v.write_bytes(data)
    return v


def test_no_apto_no_deja_constancia(monkeypatch, tmp_path):
    _redir(monkeypatch, tmp_path)
    v = _video(tmp_path)
    out = VE.emitir_constancia_f5(str(v), {"apto": False, "puntaje_gate": 20}, "documental")
    assert out is None
    assert not (tmp_path / "registro_f5").exists() or not list((tmp_path / "registro_f5").glob("*.json"))
    assert VE.verificar(str(v), requerir_editorial=False)[0] == "PREVISUALIZACION"


def test_apto_constancia_sha_coincide(monkeypatch, tmp_path):
    _redir(monkeypatch, tmp_path)
    v = _video(tmp_path)
    out = VE.emitir_constancia_f5(str(v), {"apto": True, "puntaje_gate": 90}, "documental", encargo_sha="ENC123")
    rec = json.loads(Path(out).read_text(encoding="utf-8"))
    assert rec["sha256"] == VE.sha256_file(str(v)) and rec["veredicto"] == "APTO"
    assert rec["encargo_sha256"] == "ENC123"
    assert VE.verificar(str(v), requerir_editorial=False)[0] == "APTO_TECNICO"


def test_certificacion_sin_encargo_es_previsualizacion(monkeypatch, tmp_path):
    # cierra el bypass: una constancia documental SIN encargo (render directo / --certificar)
    # NO es certificable aunque F5 sea APTO.
    _redir(monkeypatch, tmp_path)
    v = _video(tmp_path)
    VE.emitir_constancia_f5(str(v), {"apto": True, "puntaje_gate": 90}, "documental")  # sin encargo_sha
    estado, det = VE.verificar(str(v), requerir_editorial=False)
    assert estado == "PREVISUALIZACION"
    assert "encargo" in det["motivo"]


def test_byte_extra_invalida(monkeypatch, tmp_path):
    _redir(monkeypatch, tmp_path)
    v = _video(tmp_path)
    VE.emitir_constancia_f5(str(v), {"apto": True, "puntaje_gate": 90}, "documental", encargo_sha="ENC123")
    v.write_bytes(v.read_bytes() + b"\x00")  # +1 byte -> otro sha
    assert VE.verificar(str(v), requerir_editorial=False)[0] == "PREVISUALIZACION"


def test_apto_sin_editorial_no_entregable(monkeypatch, tmp_path):
    _redir(monkeypatch, tmp_path)
    v = _video(tmp_path)
    VE.emitir_constancia_f5(str(v), {"apto": True, "puntaje_gate": 90}, "documental", encargo_sha="ENC123")
    assert VE.verificar(str(v), requerir_editorial=True)[0] == "PREVISUALIZACION"
    # aprobacion editorial humana (registro aparte)
    sha = VE.sha256_file(str(v))
    (tmp_path / "registro_editorial" / f"{sha}.json").write_text(
        json.dumps({"aprobado": True, "por": "humano"}), encoding="utf-8")
    assert VE.verificar(str(v), requerir_editorial=True)[0] == "ENTREGABLE"
