# -*- coding: utf-8 -*-
"""Imágenes y fragmentos de video REALES con licencia libre, trazables (31-oct-2026).

Busca en Wikimedia Commons, lee la licencia de CADA archivo desde la API (extmetadata), descarta lo que no
sea dominio público, CC0, CC BY o CC BY-SA, y descarta las fotos con aviso de derechos de la personalidad
(retratos de personas vivas con restricción de uso comercial). Lo que pasa:
  1. se baja a raw/AAAA-MM-DD_<caso>_medios/ (read-only después),
  2. se registra en biblioteca_audiovisual/_manifiesto.json con el esquema recurso_av 1.0
     (licencia, autor, texto exacto del crédito en pantalla, sha256),
  3. queda listo para que motor.py lo use por su id.

Uso:
  python scripts/foto_real/medios.py buscar "Día de Muertos Oaxaca" [--n 15]
  python scripts/foto_real/medios.py bajar <caso> "File:Nombre.jpg" ["File:Otro.jpg" ...]
  python scripts/foto_real/medios.py video <caso> <url_mp4> --autor "..." --licencia "dominio público" \
         --fuente "..." --pagina <url de la página oficial> --titulo "..."
     (solo para material de gobierno de EE. UU. u otro PD/CC verificado a mano en la página oficial)
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[2]
MANIFIESTO = REPO / "biblioteca_audiovisual" / "_manifiesto.json"
API = "https://commons.wikimedia.org/w/api.php"
UA = {"User-Agent": "VIDEOS-TXT/1.0 (proyecto personal de divulgacion; contacto@example.com)"}
LIBRES = re.compile(r"^(public domain|pd|cc0|cc[ -]by(-sa)?[ -]\d(\.\d)?|cc[ -]by(-sa)?$)", re.I)
NO_LIBRES = re.compile(r"\b(nc|nd)\b", re.I)


def _destino_publico(url):
    """31-oct-2026 (SSRF): nada de red interna, aunque la URL venga de la API o de la línea de comandos."""
    sys.path.insert(0, str(REPO / "scripts")) if str(REPO / "scripts") not in sys.path else None
    from comun import DestinoNoPermitido, destino_seguro
    ok, motivo = destino_seguro(url)
    if not ok:
        raise DestinoNoPermitido(motivo)


def _get(url, **kw):
    """GET con respeto al límite de Wikimedia: ante 429/503 espera (Retry-After) y reintenta."""
    _destino_publico(url)
    for intento in range(5):
        r = requests.get(url, headers=UA, timeout=kw.pop("timeout", 60), **kw)
        if r.status_code not in (429, 503):
            return r
        espera = int(r.headers.get("Retry-After", "0") or 0) or 20 * (intento + 1)
        print(f"Commons pide esperar ({r.status_code}): {espera}s", file=sys.stderr)
        time.sleep(min(espera, 120))
    return r


def _limpio(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def licencia_libre(corta: str) -> bool:
    c = (corta or "").strip()
    return bool(LIBRES.match(c)) and not NO_LIBRES.search(c)


def buscar(consulta: str, n: int = 15, ancho: int = 1600, tipo: str = "bitmap") -> list[dict]:
    """tipo: bitmap (fotos) o video (webm/ogv/mp4 de Commons)."""
    r = _get(API, params={
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
        "gsrsearch": f"{consulta} filetype:{tipo}", "gsrlimit": min(50, n * 3),
        "prop": "imageinfo", "iiprop": "url|extmetadata|size|mime|sha1", "iiurlwidth": ancho})
    r.raise_for_status()
    out = []
    for p in sorted(r.json().get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0)):
        ii = (p.get("imageinfo") or [{}])[0]
        em = ii.get("extmetadata", {})
        g = lambda k: _limpio(em.get(k, {}).get("value", ""))  # noqa: E731
        c = {"titulo": p["title"], "pagina": ii.get("descriptionurl"), "url": ii.get("thumburl") or ii.get("url"),
             "original": ii.get("url"), "ancho": ii.get("width"), "alto": ii.get("height"), "mime": ii.get("mime"),
             "licencia": g("LicenseShortName"), "licencia_url": g("LicenseUrl"), "autor": g("Artist") or g("Credit"),
             "restricciones": g("Restrictions"), "descripcion": g("ImageDescription")[:300],
             "fecha": g("DateTimeOriginal")[:10]}
        c["libre"] = licencia_libre(c["licencia"])
        c["personalidad"] = "personality" in c["restricciones"].lower()
        c["usable"] = c["libre"] and not c["personalidad"] and (c["ancho"] or 0) >= 900
        out.append(c)
    return out[: n * 3]


def info(titulo: str, ancho: int = 1600) -> dict:
    r = _get(API, params={
        "action": "query", "format": "json", "titles": titulo, "prop": "imageinfo",
        "iiprop": "url|extmetadata|size|mime", "iiurlwidth": ancho})
    r.raise_for_status()
    p = next(iter(r.json()["query"]["pages"].values()))
    if "imageinfo" not in p:
        raise SystemExit(f"no existe en Commons: {titulo}")
    ii = p["imageinfo"][0]
    em = ii.get("extmetadata", {})
    g = lambda k: _limpio(em.get(k, {}).get("value", ""))  # noqa: E731
    c = {"titulo": p["title"], "pagina": ii["descriptionurl"], "url": ii.get("thumburl") or ii["url"],
         "original": ii["url"], "ancho": ii.get("width"), "alto": ii.get("height"), "licencia": g("LicenseShortName"),
         "autor": g("Artist") or g("Credit"), "restricciones": g("Restrictions"),
         "fecha": g("DateTimeOriginal")[:10], "descripcion": g("ImageDescription")[:300]}
    c["libre"] = licencia_libre(c["licencia"])
    c["personalidad"] = "personality" in c["restricciones"].lower()
    return c


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _hash_url(u: str) -> str:
    return hashlib.sha1(u.strip().encode("utf-8")).hexdigest()[:12]


def _cargar():
    return json.loads(MANIFIESTO.read_text(encoding="utf-8")) if MANIFIESTO.is_file() else []


_respaldado = False


def registrar(recursos: list[dict]) -> int:
    """Agrega recursos nuevos al manifiesto autoritativo (idempotente por id). Respaldo una vez por corrida."""
    global _respaldado
    man = _cargar()
    ids = {r["id"] for r in man}
    nuevos = [r for r in recursos if r["id"] not in ids]
    if not nuevos:
        return 0
    try:
        import jsonschema
        esquema = json.loads((REPO / "schemas" / "recurso_av.schema.json").read_text(encoding="utf-8"))
        for r in nuevos:
            jsonschema.validate(r, esquema)
    except ImportError:
        pass
    if not _respaldado and MANIFIESTO.is_file():
        sello = dt.datetime.now().strftime("%Y%m%d_%H%M")
        shutil.copy2(MANIFIESTO, MANIFIESTO.with_name(f"_manifiesto_backup_{sello}.json"))
        _respaldado = True
    MANIFIESTO.write_text(json.dumps(man + nuevos, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(nuevos)


def carpeta_raw(caso: str) -> Path:
    d = REPO / "raw" / f"{dt.date.today():%Y-%m-%d}_{caso}_medios"
    d.mkdir(parents=True, exist_ok=True)
    return d


def id_commons(titulo: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", titulo.lower().replace("file:", ""))[:40].strip("-")
    return f"commons-{base}-{hashlib.sha1(titulo.encode()).hexdigest()[:6]}"


def bajar(caso: str, titulos: list[str], permitir_personalidad=False) -> list[dict]:
    d = carpeta_raw(caso)
    regs = []
    for t in titulos:
        try:
            c = info(t)
        except SystemExit as e:   # un título inexistente no detiene el lote
            print(f"OMITIDA: {e}")
            continue
        if not c["libre"]:
            print(f"RECHAZADA (licencia «{c['licencia']}»): {t}")
            continue
        if c["personalidad"] and not permitir_personalidad:
            print(f"RECHAZADA (derechos de la personalidad): {t}")
            continue
        rid = id_commons(c["titulo"])
        es_video = Path(c["original"].split("?")[0]).suffix.lower() in (".webm", ".ogv", ".mp4")
        url = c["original"] if es_video else c["url"]
        ext = Path(url.split("?")[0]).suffix.lower() or ".jpg"
        arch = d / f"{rid}{ext}"
        if not arch.is_file():
            for intento in range(3):
                r = _get(url, timeout=180)
                if r.status_code == 200:
                    arch.write_bytes(r.content)
                    break
                time.sleep(2 + 3 * intento)
            else:
                print(f"NO SE PUDO BAJAR ({r.status_code}): {t}")
                continue
        autor = re.sub(r"\s+", " ", c["autor"])[:80]
        credito = f"Foto: {autor} · {c['licencia']} · Wikimedia Commons"
        reg = {"id": rid, "esquema_version": "1.0", "caso": caso, "tipo": "foto", "url_origen": c["pagina"],
               "hash_url": _hash_url(c["pagina"]), "hash_url_version": "v1", "superseded_by": None, "derivado_de": None,
               "medio": "Wikimedia Commons", "autor": autor or None, "fecha": c["fecha"] or None,
               "resolucion": None, "duracion_seg": None, "fragmento": None, "archivo_local": str(arch),
               "hash_archivo": sha256(arch), "clase": "CONTEXTO", "derechos": "AUTORIZADO",
               "licencia": c["licencia"], "condiciones": "Atribución en pantalla" + (
                   "; compartir igual (CC BY-SA)" if "sa" in c["licencia"].lower() else ""),
               "atribucion_en_pantalla": credito, "estado_verificacion": "VERIFICADO",
               "verificado_metodo": "API Commons extmetadata (LicenseShortName, Artist, Restrictions)",
               "notas": c["descripcion"][:200] or None}
        if es_video:
            reg.update(tipo="video", clase="EVIDENCIA-REAL-DEL-CASO",
                       atribucion_en_pantalla=f"Video: {autor} · {c['licencia']} · Wikimedia Commons")
            pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                 "stream=width,height:format=duration", "-of", "json", str(arch)],
                                capture_output=True, text=True).stdout
            pj = json.loads(pr or "{}")
            if pj.get("streams"):
                reg["resolucion"] = f"{pj['streams'][0]['width']}x{pj['streams'][0]['height']}"
            if pj.get("format", {}).get("duration"):
                reg["duracion_seg"] = round(float(pj["format"]["duration"]), 2)
        else:
            try:
                from PIL import Image
                with Image.open(arch) as im:
                    reg["resolucion"] = f"{im.width}x{im.height}"
            except Exception:
                pass
        (d / f"{rid}.licencia.json").write_text(json.dumps({**c, "id": rid}, ensure_ascii=False, indent=1),
                                                 encoding="utf-8")
        regs.append(reg)
        print(f"OK {rid}  [{c['licencia']}]  {autor[:50]}")
    print(f"registrados en el manifiesto: {registrar(regs)} nuevos")
    return regs


def video(caso, url, autor, licencia, fuente, pagina, titulo, rid=None):
    d = carpeta_raw(caso)
    rid = rid or "video-" + re.sub(r"[^a-z0-9]+", "-", titulo.lower())[:40].strip("-")
    arch = d / f"{rid}{Path(url.split('?')[0]).suffix or '.mp4'}"
    if not arch.is_file():
        _destino_publico(url)
        r = requests.get(url, headers={**UA, "User-Agent": "Mozilla/5.0 " + UA["User-Agent"]}, timeout=180)
        r.raise_for_status()
        arch.write_bytes(r.content)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(arch)], capture_output=True, text=True).stdout.strip() or 0)
    reg = {"id": rid, "esquema_version": "1.0", "caso": caso, "tipo": "video", "url_origen": pagina,
           "hash_url": _hash_url(pagina), "hash_url_version": "v1", "superseded_by": None, "derivado_de": None,
           "medio": fuente, "autor": autor, "fecha": None, "resolucion": None, "duracion_seg": round(dur, 2),
           "fragmento": None, "archivo_local": str(arch), "hash_archivo": sha256(arch), "clase": "EVIDENCIA-REAL-DEL-CASO",
           "derechos": "AUTORIZADO", "licencia": licencia, "condiciones": "Crédito de la fuente en pantalla",
           "atribucion_en_pantalla": f"Video: {autor}", "estado_verificacion": "VERIFICADO",
           "verificado_metodo": f"descarga directa {url}; licencia leída en {pagina}", "notas": titulo}
    print(f"OK {rid} {dur:.1f}s · registrados: {registrar([reg])}")
    return reg


def recurso(rid: str) -> dict:
    for r in _cargar():
        if r["id"] == rid:
            return r
    raise KeyError(f"{rid} no está en {MANIFIESTO}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("buscar")
    b.add_argument("consulta")
    b.add_argument("--n", type=int, default=15)
    b.add_argument("--todas", action="store_true", help="mostrar también las no usables")
    b.add_argument("--video", action="store_true", help="buscar videos en vez de fotos")
    d = sub.add_parser("bajar")
    d.add_argument("caso")
    d.add_argument("titulos", nargs="+")
    v = sub.add_parser("video")
    v.add_argument("caso")
    v.add_argument("url")
    for k in ("autor", "licencia", "fuente", "pagina", "titulo"):
        v.add_argument("--" + k, required=True)
    v.add_argument("--id")
    a = ap.parse_args()
    if a.cmd == "buscar":
        for c in buscar(a.consulta, a.n, tipo="video" if a.video else "bitmap"):
            if c["usable"] or a.todas:
                marca = "OK " if c["usable"] else ("PER" if c["personalidad"] else "NO ")
                print(f"{marca} {c['ancho']}x{c['alto']}  [{c['licencia']}]  {c['titulo']}  — {c['autor'][:40]}")
    elif a.cmd == "bajar":
        bajar(a.caso, a.titulos)
    else:
        video(a.caso, a.url, a.autor, a.licencia, a.fuente, a.pagina, a.titulo, a.id)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
