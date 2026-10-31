# -*- coding: utf-8 -*-
"""Extrae las políticas OFICIALES de monetización (YouTube, TikTok, Meta) a raw/ + procesado/.

Fases separadas (raw/ es de solo lectura):
  python scripts/extraer_politicas_monetizacion.py descargar   -> baja cada fuente UNA vez a raw/<carpeta>/
                                                                   (si ya existe el crudo, NO lo vuelve a bajar)
  python scripts/extraer_politicas_monetizacion.py extraer     -> lee el crudo de disco y escribe .md + .json
  python scripts/extraer_politicas_monetizacion.py indice      -> agrega filas a INDICE.md (sin duplicar hash)

Red: Google y la API de TikTok con requests por la wifi (source_address 0.0.0.0); las páginas de Meta y
TikTok que se arman con JavaScript se renderizan con Edge headless (Playwright). Método: extractor_articulo.
El texto de cada segmento es el texto literal de un bloque (p, li, h*, td) del contenido principal.
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comun import utf8_stdout, calcular_hash, escribir_salida  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
RAW, PROC = REPO / "raw", REPO / "procesado"
FECHA = "2026-10-17"
WIFI = ("0.0.0.0", 0)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
TT_API = ("https://www.tiktok.com/feedback/1/faq_detail_by_id/?faq_id={id}&country=MX&lang=en"
          "&entrance=general&app_id=1284&app_key=tiktok-web&fetch_faq_version=2&aid=1284")

# carpeta, url pública, modo (estatico|render|tiktok_api), extra
FUENTES = [
    ("politicas-youtube-monetizacion-canal", "https://support.google.com/youtube/answer/1311392?hl=es-419", "estatico", None),
    ("politicas-youtube-ypp-elegibilidad", "https://support.google.com/youtube/answer/72851?hl=es-419", "estatico", None),
    ("politicas-youtube-shorts-monetizacion", "https://support.google.com/youtube/answer/12504220?hl=es-419", "estatico", None),
    ("politicas-youtube-anunciantes", "https://support.google.com/youtube/answer/6162278?hl=es-419", "estatico", None),
    ("politicas-youtube-contenido-alterado-ia", "https://support.google.com/youtube/answer/14328491?hl=es-419", "estatico", None),
    ("politicas-youtube-content-id", "https://support.google.com/youtube/answer/2797370?hl=es-419", "estatico", None),
    ("politicas-youtube-biblioteca-audio", "https://support.google.com/youtube/answer/3376882?hl=es-419", "estatico", None),
    ("politicas-tiktok-creator-rewards", "https://support.tiktok.com/en/business-and-creator/creator-rewards-program/creator-rewards-program", "tiktok_api", "7581821550694013452"),
    ("politicas-tiktok-creator-rewards-terminos", "https://support.tiktok.com/en/business-and-creator/creator-rewards-program/creator-rewards-program-terms", "render", "shell"),
    ("politicas-tiktok-contenido-ia", "https://support.tiktok.com/en/using-tiktok/creating-videos/ai-generated-content", "tiktok_api", "7636670084747893268"),
    ("politicas-tiktok-normas-integridad", "https://www.tiktok.com/community-guidelines/es-419/integrity-authenticity", "render", None),
    ("politicas-tiktok-fyf", "https://www.tiktok.com/community-guidelines/es-419/fyf-standards", "render", None),
    ("politicas-tiktok-contenido-marca", "https://www.tiktok.com/legal/page/global/bc-policy/es", "render", None),
    ("politicas-tiktok-derechos-autor", "https://www.tiktok.com/legal/page/global/copyright-policy/es", "render", None),
    ("politicas-tiktok-musica-comercial", "https://support.tiktok.com/en/business-and-creator/creator-and-business-accounts/commercial-music-library", "render", "shell"),
    ("politicas-tiktok-uso-comercial-musica", "https://support.tiktok.com/en/business-and-creator/creator-and-business-accounts/commercial-use-of-music-on-tiktok", "tiktok_api", "7636670088170773013"),
    ("politicas-tiktok-creator-academy-rewards", "https://www.tiktok.com/creator-academy/en/articles/monetize/creator-rewards", "render", None),
    ("politicas-meta-monetizacion-socios", "https://es-la.facebook.com/business/help/169845596919485?locale=es_LA", "render", None),
    ("politicas-meta-monetizacion-contenido", "https://es-la.facebook.com/business/help/1348682518563619?locale=es_LA", "render", None),
    ("politicas-meta-elegibilidad", "https://es-la.facebook.com/business/help/321041698514182?locale=es_LA", "render", None),
    ("politicas-meta-contenido-original", "https://es-la.facebook.com/business/help/262834734651607?locale=es_LA", "render", None),
    ("politicas-meta-reglas-monetizacion", "https://es-la.facebook.com/business/help/185404538833362?locale=es_LA", "render", None),
    ("politicas-meta-originales-2026", "https://about.fb.com/news/2026/03/rewarding-original-creators-on-facebook/", "render", None),
    ("politicas-meta-etiqueta-ia", "https://www.meta.com/help/artificial-intelligence/1783222608822690/", "render", None),
    ("politicas-meta-etiquetado-ia-2024", "https://about.fb.com/news/2024/04/metas-approach-to-labeling-ai-generated-content-and-manipulated-media/", "render", None),
    ("politicas-meta-sound-collection", "https://www.facebook.com/sound/collection/", "render", "corto"),
    ("politicas-meta-sound-collection-intro", "https://www.facebook.com/creators/discover/introducing-sound-collection", "render", "shell"),
    ("politicas-meta-sound-collection-terminos", "https://www.facebook.com/legal/sound_collection_terms", "render", None),
]


def carpeta(nombre: str) -> str:
    return f"{FECHA}_{nombre}"


def _sesion():
    import requests
    from requests.adapters import HTTPAdapter

    class _Wifi(HTTPAdapter):
        def init_poolmanager(self, *a, **kw):
            kw["source_address"] = WIFI
            super().init_poolmanager(*a, **kw)
    s = requests.Session()
    s.mount("http://", _Wifi())
    s.mount("https://", _Wifi())
    s.headers.update({"User-Agent": UA, "Accept-Language": "es-419,es;q=0.9,en;q=0.8"})
    return s


def descargar(solo: set | None = None):
    s = _sesion()
    pendientes_render = []
    for nombre, url, modo, extra in FUENTES:
        if solo and nombre not in solo:
            continue
        d = RAW / carpeta(nombre)
        if d.exists() and any(d.iterdir()):
            print("ya existe (no se re-descarga):", d)
            continue
        d.mkdir(parents=True, exist_ok=True)
        meta = {"source_url": url, "modo": modo}
        try:
            if modo == "estatico":
                r = s.get(url, timeout=40)
                meta.update(status=r.status_code, url_final=r.url)
                (d / "pagina.html").write_bytes(r.content)
            elif modo == "tiktok_api":
                api = TT_API.format(id=extra)
                r = s.get(api, timeout=40)
                meta.update(status=r.status_code, url_final=api,
                            nota="la página pública es una SPA; el texto se obtiene del endpoint JSON oficial "
                                 "que la propia página consulta. Con lang=es el endpoint regresa vacío; se usa lang=en.")
                (d / "api.json").write_bytes(r.content)
            else:
                pendientes_render.append((d, url, meta))
                continue
        except Exception as e:  # noqa: BLE001
            meta.update(status=0, error=str(e)[:300])
        (d / "_descarga.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        print(meta.get("status"), d.name)
    if pendientes_render:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(channel="msedge", headless=True)
            ctx = b.new_context(locale="es-MX", user_agent=UA + " Edg/124.0")
            for d, url, meta in pendientes_render:
                pg = ctx.new_page()
                try:
                    r = pg.goto(url, timeout=60000, wait_until="domcontentloaded")
                    pg.wait_for_timeout(8000)
                    meta.update(status=r.status if r else 0, url_final=pg.url,
                                nota="renderizada con Microsoft Edge headless (Playwright)")
                    (d / "pagina_renderizada.html").write_text(pg.content(), encoding="utf-8")
                except Exception as e:  # noqa: BLE001
                    meta.update(status=0, error=str(e)[:300])
                pg.close()
                (d / "_descarga.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
                print(meta.get("status"), d.name)
            b.close()


EXPANDIR = {"politicas-meta-monetizacion-socios", "politicas-meta-monetizacion-contenido",
            "politicas-meta-contenido-original"}


def expandir():
    """Meta oculta el texto de sus políticas en acordeones. Se vuelve a renderizar la página, se abren
    todos los acordeones y se guarda como archivo NUEVO (pagina_expandida.html); el crudo previo no se toca."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        ctx = b.new_context(locale="es-MX", user_agent=UA + " Edg/124.0")
        for nombre, url, modo, extra in FUENTES:
            if nombre not in EXPANDIR:
                continue
            d = RAW / carpeta(nombre)
            if (d / "pagina_expandida_v2.html").exists():
                print("ya existe:", d / "pagina_expandida_v2.html")
                continue
            pg = ctx.new_page()
            pg.goto(url, timeout=60000, wait_until="domcontentloaded")
            pg.wait_for_timeout(7000)
            n = 0
            for _ in range(1):  # v2: Meta abre cada sección al hacer clic en su encabezado h2/h3/h4
                for el in pg.query_selector_all("[aria-expanded='false'], h2, h3, h4"):
                    try:
                        el.click(timeout=2000)
                        n += 1
                        pg.wait_for_timeout(300)
                    except Exception:  # noqa: BLE001
                        pass
            pg.wait_for_timeout(2000)
            (d / "pagina_expandida_v2.html").write_text(pg.content(), encoding="utf-8")
            print("acordeones abiertos:", n, d.name)
            pg.close()
        b.close()


# ---------------- extracción ----------------
BLOQUES = {"p", "li", "h1", "h2", "h3", "h4", "h5", "td", "th", "dt", "dd", "blockquote"}
CONTENEDORES = [  # xpath del contenido principal, en orden de preferencia
    "//div[contains(@class,'article-content-container')]",
    "//section[contains(@class,'article')]",
    "//main",
    "//article",
    "//div[@role='main']",
    "//body",
]


def _es_bloque(el) -> bool:
    if not isinstance(el.tag, str):
        return False
    return el.tag in BLOQUES or (el.tag == "div" and el.get("data-line") == "true")


def _bloques(raiz) -> list[str]:
    out = []
    for el in raiz.iter():
        if not _es_bloque(el):
            continue
        if any(_es_bloque(h) for h in el.iterdescendants()):
            txt = (el.text or "").strip()  # bloque con bloques dentro: solo su texto propio inicial
            if txt:
                out.append(re.sub(r"\s+", " ", txt))
            continue
        txt = re.sub(r"\s+", " ", el.text_content()).strip()
        if txt:
            out.append(txt)
    return out


def _limpiar(tree):
    for bad in tree.xpath("//script|//style|//noscript|//nav|//header|//footer|//svg|//button"):
        bad.drop_tree()
    return tree


def _texto_html(html: str, xpaths=CONTENEDORES) -> tuple[list[str], str]:
    from lxml import html as LH
    tree = _limpiar(LH.fromstring(html))
    for xp in xpaths:
        nodos = tree.xpath(xp)
        if nodos:
            bl = []
            for n in nodos:
                bl.extend(_bloques(n))
            if sum(len(b) for b in bl) > 300:
                return bl, xp
    # respaldo: páginas sin p/li (p. ej. Creator Academy) -> fragmentos de texto literales >= 40 caracteres
    body = tree.xpath("//body")
    if body:
        bl = [re.sub(r"\s+", " ", t).strip() for t in body[0].itertext() if len(t.strip()) >= 40]
        if sum(len(b) for b in bl) > 300:
            return bl, "//body itertext (respaldo, fragmentos >= 40 car.)"
    return [], "(sin contenedor)"


def _titulo(html: str) -> str:
    import html as H
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return H.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else "(sin título)"


def _idioma(txt: str) -> str:
    t = f" {txt.lower()} "
    es = sum(t.count(f" {w} ") for w in ("que", "de", "la", "el", "los", "para", "con", "una"))
    en = sum(t.count(f" {w} ") for w in ("the", "of", "and", "to", "for", "with", "that", "is"))
    return "es" if es >= en else "en"


def _dedup_consecutivos(bl):
    out = []
    for b in bl:
        if not out or out[-1] != b:
            out.append(b)
    return out


def extraer():
    resumen = []
    for nombre, url, modo, extra in FUENTES:
        rd = RAW / carpeta(nombre)
        pd = PROC / carpeta(nombre)
        meta = json.loads((rd / "_descarga.json").read_text(encoding="utf-8")) if (rd / "_descarga.json").exists() else {}
        avisos = [f"descarga: modo={modo}; status={meta.get('status')}; url_final={meta.get('url_final')}"]
        if meta.get("nota"):
            avisos.append("descarga: " + meta["nota"])
        bloques, titulo, via = [], "(sin título)", ""
        try:
            if modo == "tiktok_api":
                j = json.loads((rd / "api.json").read_text(encoding="utf-8"))
                f = j["faq_list"][0]
                titulo = f.get("Name") or titulo
                from lxml import html as LH
                bloques = _bloques(LH.fromstring("<div>%s</div>" % f.get("Value", "")))
                via = "faq_list[0].Value"
            else:
                arch = rd / ("pagina.html" if modo == "estatico" else "pagina_renderizada.html")
                for exp in ("pagina_expandida_v2.html", "pagina_expandida.html"):
                    if (rd / exp).exists():
                        arch = rd / exp
                        avisos.append(f"extracción: desde {exp} (acordeones abiertos con Edge headless)")
                        break
                html = arch.read_bytes().decode("utf-8", errors="replace")
                titulo = _titulo(html)
                bloques, via = _texto_html(html)
                if extra == "corto" and not bloques:  # página de aterrizaje breve: fragmentos >= 15 car.
                    from lxml import html as LH
                    body = _limpiar(LH.fromstring(html)).xpath("//body")[0]
                    bloques = [re.sub(r"\s+", " ", t).strip() for t in body.itertext() if len(t.strip()) >= 15]
                    via = "//body itertext (página breve, fragmentos >= 15 car.)"
        except Exception as e:  # noqa: BLE001
            avisos.append(f"[PENDIENTE] no se pudo extraer: {str(e)[:200]}")
        bloques = _dedup_consecutivos(bloques)
        avisos.append(f"extracción: bloques de {via}")
        minimo = 100 if extra == "corto" else 300
        ok = meta.get("status") == 200 and sum(len(b) for b in bloques) > minimo and extra != "shell"
        if extra == "shell":
            avisos.append("[PENDIENTE] la URL resuelve (200) pero solo entrega el menú/portada "
                          "(SPA o redirección sin el artículo); el texto de la política no se pudo obtener")
        elif not ok:
            avisos.append("[PENDIENTE] la página no cargó o no trae el texto de la política (requiere sesión o no existe)")
        reg = {"source_url": url, "url_hash": calcular_hash(url), "tipo": "texto",
               "titulo": titulo, "idioma": _idioma(" ".join(bloques)), "duracion_seg": 0,
               "metodo": "extractor_articulo", "verificada": bool(ok),
               "segmentos": [{"inicio_seg": None, "fin_seg": None, "texto": b, "confianza": 1.0}
                             for b in bloques] if ok else [],
               "avisos": avisos}
        pd.mkdir(parents=True, exist_ok=True)
        for viejo in list(pd.glob("*.json")) + list(pd.glob("*.md")):
            viejo.unlink()
        r = escribir_salida(pd, reg)
        resumen.append({"carpeta": pd.name, "estado": r["estado"] if ok else "PENDIENTE", "json": str(r["json"]),
                        "titulo": titulo, "hash": reg["url_hash"], "segmentos": len(reg["segmentos"]),
                        "chars": sum(len(b) for b in bloques), "source_url": url})
        print("%-10s %4d seg %7d chars  %s" % (resumen[-1]["estado"], len(reg["segmentos"]),
                                                resumen[-1]["chars"], pd.name))
    (REPO / "logs").mkdir(exist_ok=True)
    (REPO / "logs" / "extraccion_politicas_20261017.json").write_text(
        json.dumps(resumen, ensure_ascii=False, indent=1), encoding="utf-8")


def indice():
    res = json.loads((REPO / "logs" / "extraccion_politicas_20261017.json").read_text(encoding="utf-8"))
    ind = REPO / "INDICE.md"
    txt = ind.read_text(encoding="utf-8")
    nuevas = []
    for r in res:
        if r["hash"] in txt:
            continue
        rel = Path(r["json"]).relative_to(PROC).as_posix()
        tit = r["titulo"].replace("|", "/")[:60]
        nuevas.append(f"| {FECHA} | {tit} | texto | extractor_articulo | {r['estado']} | `{r['hash']}` | {rel} |")
    if nuevas:
        if not txt.endswith("\n"):
            txt += "\n"
        ind.write_text(txt + "\n".join(nuevas) + "\n", encoding="utf-8")
    print("filas agregadas:", len(nuevas))


if __name__ == "__main__":
    utf8_stdout()
    accion = sys.argv[1]
    if accion == "descargar":
        descargar(set(sys.argv[2:]) or None)
    else:
        {"extraer": extraer, "indice": indice, "expandir": expandir}[accion]()
