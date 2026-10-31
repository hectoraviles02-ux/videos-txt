# -*- coding: utf-8 -*-
"""Compuertas del motor expediente3d (22-oct-2026). Cada prueba intenta ROMPER una compuerta:
voz equivocada con igual número de palabras, números equivalentes, voz rechazada que debe detener,
guion cambiado con audio viejo, escena que pide algo que el motor no sabe dibujar.
Sin GPU: la prueba de gen_voz usa un TTS y un whisper falsos (stubs) que solo devuelven el texto indicado."""
import importlib.util
import json
import os
import subprocess
import sys
import wave
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
MOTOR = RAIZ / "scripts" / "expediente3d"
sys.path.insert(0, str(MOTOR))
from verificar_voz import verificar  # noqa: E402

# ── 1. verificador de voz ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("guion,oido", [
    ("la nave llegó a Marte sin problemas", "el gato duerme bajo una mesa verde"),   # mismo número de palabras
    ("La nave llegó a Marte sin problemas", "La nave llegó a Venus sin problemas"),  # una palabra clave distinta
    ("pasó a solo unos cincuenta y siete kilómetros", "pasó a solo unos 75 kilómetros"),
    ("tardó cuarenta y tres segundos", "tardó cuarenta y tres minutos"),
])
def test_voz_equivocada_se_rechaza(guion, oido):
    assert not verificar(guion, oido)["ok"]


@pytest.mark.parametrize("guion,oido", [
    ("pasó a solo unos cincuenta y siete kilómetros", "pasó a solo unos 57 kilómetros"),
    ("son cuatro punto cuatro cinco newtons", "son 4.45 newtons"),
    ("Microsoft estima ocho punto cinco millones de equipos", "Microsoft estima 8 .5 millones de equipos"),
    ("al menos cinco mil proyectos", "al menos 5 .000 proyectos"),
    ("Primero de agosto de dos mil doce.", "1 de agosto de 2012."),
    ("Y ese código escondía algo: Power Peg.", "Y ese código escondía algo, PowerPeg."),
])
def test_voz_equivalente_se_acepta(guion, oido):
    r = verificar(guion, oido)
    assert r["ok"], r["motivo"]


def test_voces_reales_de_los_expedientes_pasan():
    """Calibración: las frases ya producidas (y revisadas a mano el 21-oct) no deben rechazarse."""
    revisadas = 0
    for d in sorted((RAIZ / "guiones" / "expedientes").glob("2026-*")):
        if not (d / "voz" / "_voz.json").is_file():
            continue
        g = json.loads((d / "guion.json").read_text(encoding="utf-8"))
        v = json.loads((d / "voz" / "_voz.json").read_text(encoding="utf-8"))
        for b in g["beats"]:
            if b["id"] in v:
                r = verificar(b["texto"], " ".join(p["w"] for p in v[b["id"]]["palabras"]))
                assert r["ok"], (d.name, b["id"], r["motivo"])
                revisadas += 1
    if revisadas == 0:
        pytest.skip("no hay voces de expedientes en este equipo")


# ── utilidades para piezas de prueba ──────────────────────────────────────────────────────────────────

def _wav(p: Path, seg=1.0):
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
        w.writeframes(b"\0\0" * int(48000 * seg))


def _pieza(tmp: Path, texto="La nave llegó a Marte.", oido=None, estacion="monolito", debe_verse=("pantalla",)):
    tmp.mkdir(parents=True, exist_ok=True)
    g = {"titulo": "prueba", "beats": [{"id": "b1", "estacion": estacion, "texto": texto,
                                        "debe_verse": list(debe_verse), "efectos": [], "tarjeta": {"tipo": "cierre"}}]}
    (tmp / "guion.json").write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
    (tmp / "encargo.json").write_text(json.dumps({"pedido": "prueba", "origen": "prueba",
                                                  "debe_verse": list(debe_verse) or ["pantalla"]}), encoding="utf-8")
    if oido is not None:
        (tmp / "voz").mkdir(exist_ok=True)
        _wav(tmp / "voz" / "b1.wav")
        pal = [{"w": w, "ini": i * 0.1, "fin": i * 0.1 + 0.09} for i, w in enumerate(oido.split())]
        (tmp / "voz" / "_voz.json").write_text(json.dumps(
            {"b1": {"archivo": "b1.wav", "dur": 1.0, "toma": 1, "ok": True, "palabras": pal}}, ensure_ascii=False),
            encoding="utf-8")
    return tmp


# ── 2. la línea de tiempo no acepta una voz que no coincide (aunque diga ok: true) ───────────────────────

def test_linea_tiempo_rechaza_voz_que_no_coincide(tmp_path):
    d = _pieza(tmp_path / "p", texto="La nave llegó a Marte.", oido="El gato duerme bajo.")
    r = subprocess.run([sys.executable, str(MOTOR / "linea_tiempo.py"), str(d)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode != 0
    assert "VOZ NO COINCIDE" in r.stderr
    assert not (d / "linea_tiempo.json").exists()


def test_linea_tiempo_acepta_voz_correcta(tmp_path):
    d = _pieza(tmp_path / "p", texto="La nave llegó a Marte.", oido="La nave llegó a Marte.")
    r = subprocess.run([sys.executable, str(MOTOR / "linea_tiempo.py"), str(d)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    assert (d / "linea_tiempo.json").is_file()


# ── 3. gen_voz: una frase rechazada termina con código 3 (detiene al orquestador) ─────────────────────────

STUBS = {
    "chatterbox/__init__.py": "",
    "chatterbox/mtl_tts.py": (
        "import torch\n"
        "class ChatterboxMultilingualTTS:\n"
        "    sr = 24000\n"
        "    @classmethod\n"
        "    def from_pretrained(cls, device=None): return cls()\n"
        "    def generate(self, *a, **k):\n"
        "        return 0.1 * torch.sin(torch.arange(24000).float().unsqueeze(0) / 5)\n"),
    "faster_whisper/__init__.py": (
        "import os\n"
        "class _W:\n"
        "    def __init__(self, w, s): self.word, self.start, self.end = w, s, s + 0.1\n"
        "class _S:\n"
        "    def __init__(self, t):\n"
        "        self.text = t\n"
        "        self.words = [_W(w, i * 0.1) for i, w in enumerate(t.split())]\n"
        "class WhisperModel:\n"
        "    def __init__(self, *a, **k): pass\n"
        "    def transcribe(self, *a, **k): return [_S(os.environ['OIDO_FALSO'])], None\n"),
}


def _gen_voz(tmp_path, texto, oido):
    stubs = tmp_path / "stubs"
    for rel, cod in STUBS.items():
        (stubs / rel).parent.mkdir(parents=True, exist_ok=True)
        (stubs / rel).write_text(cod, encoding="utf-8")
    d = _pieza(tmp_path / "p", texto=texto)
    env = dict(os.environ, PYTHONPATH=str(stubs), OIDO_FALSO=oido, VOZ_REF="fabrica", ASR_DEVICE="cpu",
               PYTHONIOENCODING="utf-8")
    pytest.importorskip("torch")
    pytest.importorskip("torchaudio")
    return d, subprocess.run([sys.executable, str(MOTOR / "gen_voz.py"), str(d)], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", env=env)


def test_gen_voz_rechazada_detiene(tmp_path):
    d, r = _gen_voz(tmp_path, "La nave llegó a Marte.", "El gato duerme bajo.")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "VOZ RECHAZADA" in r.stdout
    assert json.loads((d / "voz" / "_voz.json").read_text(encoding="utf-8"))["b1"]["ok"] is False


def test_gen_voz_correcta_sigue(tmp_path):
    d, r = _gen_voz(tmp_path, "La nave llegó a Marte.", "La nave llegó a Marte.")
    assert r.returncode == 0, r.stdout + r.stderr


# ── 4 y 5. orquestador: escenas y huellas (se carga el módulo sin correr la producción) ─────────────────

def _orquestador(d: Path):
    argv = sys.argv
    sys.argv = ["producir_expediente.py", str(d)]
    try:
        spec = importlib.util.spec_from_file_location("producir_expediente_prueba", MOTOR / "producir_expediente.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
    finally:
        sys.argv = argv
    return m


def test_escena_que_el_motor_no_sabe_dibujar_detiene_antes_de_la_voz(tmp_path):
    d = _pieza(tmp_path / "p", estacion="globo", debe_verse=("dinosaurio", "nave"))
    m = _orquestador(d)
    fallas = m.revisar_escenas(json.loads((d / "guion.json").read_text(encoding="utf-8")))
    assert any("dinosaurio" in f and "NINGUNA" in f for f in fallas)       # nadie lo sabe dibujar
    assert any("nave" in f and "espacio" in f for f in fallas)             # existe, pero en otra estación
    m.cargar_env = lambda: None
    assert m.main() == 4
    assert not (d / "voz").exists()  # no se gastó GPU


def test_beat_sin_debe_verse_detiene(tmp_path):
    d = _pieza(tmp_path / "p", debe_verse=())
    m = _orquestador(d)
    assert any("falta «debe_verse»" in f for f in m.revisar_escenas(json.loads((d / "guion.json").read_text("utf-8"))))


def test_cambiar_el_guion_aparta_el_audio_viejo(tmp_path):
    d = _pieza(tmp_path / "p", texto="La nave llegó a Marte.", oido="La nave llegó a Marte.")
    (d / "linea_tiempo.json").write_text("{}", encoding="utf-8")
    m = _orquestador(d)
    g = json.loads((d / "guion.json").read_text(encoding="utf-8"))
    H = m.calcular_huellas(g)
    m.HUELLAS.write_text(json.dumps({"voz": H["voz"], "tiempo": H["tiempo"]}), encoding="utf-8")
    # se cambia el texto del guion: la voz y la línea de tiempo viejas ya no le corresponden
    g["beats"][0]["texto"] = "La nave se perdió en Marte."
    (d / "guion.json").write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
    reg = m.revisar_huellas(m.calcular_huellas(g))
    assert not (d / "voz").exists() and not (d / "linea_tiempo.json").exists()
    previo = list((d / "_previo").glob("*_voz"))
    assert previo and (previo[0] / "voz" / "_voz.json").is_file()  # apartado, no borrado
    assert "voz" not in reg and "tiempo" not in reg


def test_sin_cambios_no_se_aparta_nada(tmp_path):
    d = _pieza(tmp_path / "p", texto="La nave llegó a Marte.", oido="La nave llegó a Marte.")
    m = _orquestador(d)
    H = m.calcular_huellas(json.loads((d / "guion.json").read_text(encoding="utf-8")))
    m.HUELLAS.write_text(json.dumps({"voz": H["voz"]}), encoding="utf-8")
    m.revisar_huellas(H)
    assert (d / "voz" / "_voz.json").is_file() and not (d / "_previo").exists()


# ── revisión de Codex (22-oct): negación, unidades exactas, piezas heredadas, huellas de contenido y código ──

@pytest.mark.parametrize("guion,oido", [
    ("La nave no llegó a Marte sin problemas", "La nave llegó a Marte sin problemas"),
    ("La distancia fue de cincuenta y siete metros", "La distancia fue de cincuenta y siete kilómetros"),
])
def test_negacion_y_unidad_cambiadas_se_rechazan(guion, oido):
    assert not verificar(guion, oido)["ok"]


def test_pieza_heredada_con_voz_vieja_se_detiene_aunque_exista_linea_tiempo(tmp_path):
    d = _pieza(tmp_path / "p", texto="La nave se perdió en Marte.", oido="La nave llegó a Marte.")
    (d / "linea_tiempo.json").write_text('{"fps": 30, "frames": 30, "beats": []}', encoding="utf-8")
    m = _orquestador(d)
    m.cargar_env = lambda: None
    assert not m.HUELLAS.exists()  # pieza anterior a las huellas
    assert m.main() == 6           # no se adopta a ciegas (22-oct)
    assert not m.HUELLAS.exists()
    os.environ["ADOPTAR_LEGADO"] = "1"
    try:
        assert m.main() == 5       # adoptada a propósito: igual se comprueba su voz contra el guion actual
    finally:
        del os.environ["ADOPTAR_LEGADO"]
    assert not (d / "prueba").exists()  # no siguió a la muestra


def test_cambiar_contenido_de_musica_o_referencia_con_el_mismo_nombre_invalida(tmp_path, monkeypatch):
    d = _pieza(tmp_path / "p")
    m = _orquestador(d)
    g = json.loads((d / "guion.json").read_text(encoding="utf-8"))
    mus, ref = tmp_path / "musica.mp3", tmp_path / "ref.wav"
    mus.write_bytes(b"pista A"); ref.write_bytes(b"voz A")
    monkeypatch.setattr(m, "MUS_YT", mus)
    monkeypatch.setenv("VOZ_REF", str(ref))
    antes = m.calcular_huellas(g)
    mus.write_bytes(b"pista B"); ref.write_bytes(b"voz B")
    despues = m.calcular_huellas(g)
    assert antes["videos"] != despues["videos"]
    assert antes["voz"] != despues["voz"]


def test_cambiar_el_codigo_del_motor_invalida_los_cuadros(tmp_path, monkeypatch):
    import shutil
    d = _pieza(tmp_path / "p")
    m = _orquestador(d)
    g = json.loads((d / "guion.json").read_text(encoding="utf-8"))
    copia = tmp_path / "motor"
    shutil.copytree(MOTOR, copia, ignore=shutil.ignore_patterns("*_backup_*", "__pycache__"))
    monkeypatch.setattr(m, "AQUI", copia)
    antes = m.calcular_huellas(g)
    with open(copia / "blender_expediente.py", "a", encoding="utf-8") as f:
        f.write("\n# escena nueva\n")
    despues = m.calcular_huellas(g)
    assert antes["voz"] == despues["voz"]        # la voz no depende del render
    assert antes["render"] != despues["render"]  # los cuadros sí


# ── reanudación completa con componentes simulados (sin GPU): compuerta → videos → guion cambiado ──────────

BLENDER_FALSO = '''import json, os, sys
from pathlib import Path
from PIL import Image
D = Path(sys.argv[1])
tl = json.loads((D / "linea_tiempo.json").read_text(encoding="utf-8"))
if os.environ.get("CUADRO"):
    (D / "prueba").mkdir(exist_ok=True)
    for f in os.environ["CUADRO"].split(","):
        Image.new("RGB", (1080, 1920), (20, 20, 40)).save(D / "prueba" / ("cuadro_%04d.png" % int(f)))
else:
    (D / "cuadros3d").mkdir(exist_ok=True)
    for f in range(tl["frames"]):
        Image.new("RGB", (1080, 1920), (20, 20, 40)).save(D / "cuadros3d" / ("f_%04d.png" % f), compress_level=1)
'''


def test_reanudacion_completa_con_simulados(tmp_path, monkeypatch):
    import shutil
    pytest.importorskip("torch")
    pytest.importorskip("torchaudio")
    stubs = tmp_path / "stubs"
    for rel, cod in STUBS.items():
        (stubs / rel).parent.mkdir(parents=True, exist_ok=True)
        (stubs / rel).write_text(cod, encoding="utf-8")
    copia = tmp_path / "motor"
    shutil.copytree(MOTOR, copia, ignore=shutil.ignore_patterns("*_backup_*", "__pycache__"))
    (copia / "blender_expediente.py").write_text(BLENDER_FALSO, encoding="utf-8")
    d = _pieza(tmp_path / "p", texto="La nave llegó a Marte.")
    g = json.loads((d / "guion.json").read_text(encoding="utf-8"))
    g["beats"][0]["corto"] = True
    (d / "guion.json").write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("SIN_TELEGRAM", "1")
    monkeypatch.setenv("VOZ_REF", "fabrica")
    m = _orquestador(d)
    m.cargar_env = lambda: None
    monkeypatch.setattr(m, "AQUI", copia)
    monkeypatch.setattr(m, "PY_TTS", sys.executable)
    monkeypatch.setattr(m, "PY_BL", sys.executable)
    monkeypatch.setattr(m.candado, "LOCK", tmp_path / "_fabrica.lock")
    m.ENV.update(PYTHONPATH=os.pathsep.join([str(stubs), str(RAIZ)]), OIDO_FALSO="La nave llegó a Marte.")

    assert m.main() == 3                                  # se detiene en la compuerta humana
    assert (d / "prueba" / "hoja_muestra.jpg").is_file() and not (d / "cuadros3d").exists()
    (d / "APROBADO").write_text("prueba", encoding="utf-8")
    assert m.main() == 0                                  # con aprobación: render y los 4 videos
    videos = sorted(p.name for p in d.glob("p_*.mp4"))
    assert len(videos) == 4, videos
    assert m.main() == 0                                  # reanudar sin cambios no rehace ni aparta nada
    assert not (d / "_previo").exists()

    g["beats"][0]["texto"] = "La nave se perdió en Marte."   # el guion cambia
    (d / "guion.json").write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
    m.ENV["OIDO_FALSO"] = "La nave se perdió en Marte."
    assert m.main() == 3                                  # voz nueva, muestra nueva, y se pide aprobar otra vez
    previo = [p for p in (d / "_previo").iterdir()]
    apartado = {q.name for p in previo for q in p.iterdir()}
    assert {"voz", "linea_tiempo.json", "prueba", "APROBADO", "cuadros3d"} <= apartado
    assert set(videos) <= apartado                        # los videos viejos se conservan, no se borran
    assert not (d / "APROBADO").exists() and not list(d.glob("p_*.mp4"))
    vz = json.loads((d / "voz" / "_voz.json").read_text(encoding="utf-8"))
    assert vz["b1"]["texto"] == "La nave se perdió en Marte." and vz["b1"]["ok"]


def test_negacion_desplazada_se_rechaza_y_se_listan_diferencias():
    r = verificar("La nave no falló y el motor funcionó", "La nave falló y el motor no funcionó")
    assert not r["ok"] and len(r["diferencias"]) == 2


def test_encargo_no_cubierto_detiene_antes_del_render(tmp_path):
    d = _pieza(tmp_path / "p", estacion="monolito", debe_verse=("pantalla",))
    (d / "encargo.json").write_text(json.dumps({"pedido": "quiero ver Marte, la nave y el descenso",
                                                "origen": "chat", "debe_verse": ["marte", "nave", "descenso"]}),
                                    encoding="utf-8")
    m = _orquestador(d)
    fallas = m.revisar_escenas(json.loads((d / "guion.json").read_text(encoding="utf-8")))
    assert sum("el encargo pide" in f for f in fallas) == 3
    m.cargar_env = lambda: None
    assert m.main() == 4 and not (d / "voz").exists()


def test_sin_encargo_detiene(tmp_path):
    d = _pieza(tmp_path / "p")
    (d / "encargo.json").unlink()
    m = _orquestador(d)
    assert any("falta encargo.json" in f for f in m.revisar_escenas(json.loads((d / "guion.json").read_text("utf-8"))))


def test_palabras_de_mas_al_final_se_rechazan():
    """Mars 3D, 22-oct: el TTS agregó «¿Qué ella?» al final de b5 y pasaba."""
    r = verificar("Lo que navegación creía.", "Lo que navegación creía. ¿Qué ella?")
    assert not r["ok"] and "que ella" in r["motivo"]


def test_defectos_reales_del_tts_quedan_como_evidencia():
    """Casos REALES de Chatterbox (Mars 3D, 22-oct): el de palabras de más se rechaza; los de una palabra de contenido
    distinta pasan el verificador pero quedan listados en «diferencias» para escucha humana."""
    casos = json.loads((RAIZ / "tests" / "fixtures" / "expediente3d" / "voz_mars3d_defectos.json")
                       .read_text(encoding="utf-8"))["casos"]
    for c in casos:
        r = verificar(c["guion"], c["oido"])
        if c["esperado"] == "rechazar":
            assert not r["ok"], c["beat"]
        else:
            assert any(x not in ("y → ∅", "∅ → y") for x in r["diferencias"]), c["beat"]
