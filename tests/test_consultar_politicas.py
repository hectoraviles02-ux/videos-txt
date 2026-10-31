import importlib.util
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("watch", Path(__file__).resolve().parents[1] / "scripts/consultar_politicas.py")
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


class PolicyTests(unittest.TestCase):
    def test_estados_y_contenido_invalido(self):
        fuente = {"anclas": ["policy"]}
        texto = "Markdown Content: policy original"
        base = watch.evaluar(texto, fuente, 5)
        self.assertEqual(base["estado"], "BASE_PENDIENTE_REVISION")
        self.assertEqual(watch.evaluar(texto, fuente, 5, base)["estado"], "SIN_CAMBIO_TEXTUAL")
        self.assertTrue(watch.evaluar(texto, fuente, 5, base)["revision_pendiente"])
        self.assertFalse(watch.evaluar(texto, dict(fuente, sha256_revisado=base["sha256"]), 5, base)["revision_pendiente"])
        self.assertEqual(watch.evaluar(texto + " cambio", fuente, 5, base)["estado"], "CAMBIO_REQUIERE_REVISION")
        for invalido in ("", "Markdown Content: login", "Markdown Content: policy"):
            with self.assertRaises(ValueError):
                watch.evaluar(invalido, fuente, 20)

    def test_historial_fallos_y_vencimiento(self):
        with tempfile.TemporaryDirectory(dir=watch.ROOT / "runs") as tmp:
            runs = Path(tmp)
            config = {"intervalo_horas": 168, "timeout_segundos": 1, "minimo_caracteres": 5,
                      "fuentes": [{"id": "test", "url": "https://example.org", "anclas": ["policy"]}]}
            now = datetime(2026, 10, 17, tzinfo=timezone.utc)
            fetch = lambda *_: "Markdown Content: policy original"
            a, r = watch.ejecutar(config, runs, fetch=fetch, now=now)
            original = (a / "report.json").read_bytes()
            b, r = watch.ejecutar(config, runs, fetch=lambda *_: self.fail("no consultar"), now=now)
            self.assertEqual(r["fuentes"][0]["estado"], "NO_VENCIDA")
            self.assertTrue(r["requiere_revision"])
            def fallo(*_):
                raise TimeoutError("red caída")
            c, r = watch.ejecutar(config, runs, fetch=fallo, now=now + timedelta(days=8))
            self.assertEqual(r["fuentes"][0]["estado"], "CONSULTA_FALLIDA")
            self.assertEqual((a / "report.json").read_bytes(), original)
            self.assertNotEqual(a, b)
            self.assertTrue((c / "report.json").exists())
            d, r = watch.ejecutar(config, runs, force=True, fetch=fetch, now=now + timedelta(days=9))
            watch.ejecutar(config, runs, force=True, fetch=fallo, now=now + timedelta(days=9, hours=1))
            calls = []
            watch.ejecutar(config, runs, fetch=lambda *args: calls.append(args) or fetch(), now=now + timedelta(days=9, hours=2))
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
