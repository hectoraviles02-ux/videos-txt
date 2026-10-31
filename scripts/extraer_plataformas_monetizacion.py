# -*- coding: utf-8 -*-
"""Extrae fuentes OFICIALES de monetización de plataformas alternativas (y disponibilidad de YouTube)
a raw/ + procesado/, para docs/operations/ESTRATEGIA_PLATAFORMAS_MONETIZACION.md.

Reutiliza las funciones de scripts/extraer_politicas_monetizacion.py SIN tocar sus 28 fuentes
(ese script reescribe sus procesados al correr `extraer`; este tiene lista y log propios).

  python scripts/extraer_plataformas_monetizacion.py descargar [carpeta...]  -> raw/ (no re-descarga)
  python scripts/extraer_plataformas_monetizacion.py extraer                 -> procesado/ .md + .json
  python scripts/extraer_plataformas_monetizacion.py indice                  -> filas nuevas en INDICE.md

Red: requests por la wifi (source_address 0.0.0.0). Cada fuente se baja estática Y renderizada
con Edge headless (muchas son SPA/Zendesk/Salesforce); la extracción usa la que trae más texto.
Método: extractor_articulo. Cada segmento es el texto literal de un bloque del contenido principal.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comun import utf8_stdout, calcular_hash, escribir_salida  # noqa: E402
from extraer_politicas_monetizacion import (  # noqa: E402
    _sesion, _texto_html, _titulo, _idioma, _dedup_consecutivos, UA)

REPO = Path(__file__).resolve().parent.parent
RAW, PROC = REPO / "raw", REPO / "procesado"
FECHA = "2026-10-17"
LOG = REPO / "logs" / "extraccion_plataformas_20261017.json"

# carpeta (sin fecha), url oficial
FUENTES = [
    ("plataformas-youtube-ypp-paises", "https://support.google.com/youtube/answer/7101720?hl=es-419"),
    ("plataformas-youtube-ypp-ampliado", "https://support.google.com/youtube/answer/13429240?hl=es-419"),
    ("plataformas-youtube-doblaje-automatico", "https://support.google.com/youtube/answer/15569972?hl=es-419"),
    ("plataformas-kwai-ganar-dinero", "https://www.kwai.com/creators/earn"),
    ("plataformas-kwai-programa-creadores", "https://www.kwai.com/creators/agency/creator"),
    ("plataformas-snap-monetizacion-2024", "https://newsroom.snap.com/snapchat-new-creator-monetization?lang=es-MX"),
    ("plataformas-x-original-content-rewards", "https://help.x.com/en/using-x/original-content-rewards"),
    ("plataformas-x-creator-revenue-sharing", "https://help.x.com/en/using-x/creator-revenue-sharing"),
    ("plataformas-instagram-anuncios-reels", "https://help.instagram.com/728034559116476"),
    ("plataformas-twitch-afiliados", "https://help.twitch.tv/s/article/joining-the-affiliate-program?language=es"),
    ("plataformas-udemy-reparto-ingresos", "https://support.udemy.com/hc/en-us/articles/229605008"),
    ("plataformas-udemy-uso-ia", "https://support.udemy.com/hc/en-us/articles/30999984483607"),
    ("plataformas-hotmart-tarifas", "https://help.hotmart.com/es/article/208298448/"),
    ("plataformas-medium-partner-program", "https://help.medium.com/hc/en-us/articles/115011694187"),
    ("plataformas-medium-elegibilidad", "https://help.medium.com/hc/en-us/articles/39121627791639"),
    ("plataformas-vimeo-ott-precios", "https://help.vimeo.com/hc/en-us/articles/12425768622481"),
    ("plataformas-kofi-gold", "https://help.ko-fi.com/hc/en-us/articles/360005506873"),
    ("plataformas-kofi-postura-ia", "https://help.ko-fi.com/hc/en-us/articles/19789627403293-Ko-fi-s-stance-on-AI"),
    ("plataformas-patreon-precios", "https://www.patreon.com/pricing"),
]


# Responden 200 pero NO traen el artículo pedido (revisión humana del texto extraído, 2026-10-17)
FORZAR_PENDIENTE = {
    "plataformas-udemy-reparto-ingresos": "redirige a la portada del Help Center de Udemy (403 estático; render sin el artículo)",
    "plataformas-udemy-uso-ia": "redirige a la portada del Help Center de Udemy (403 estático; render sin el artículo)",
    "plataformas-instagram-anuncios-reels": "entrega 'Formas de anunciarse en Instagram', no el artículo de anuncios en Reels para creadores",
}


def carpeta(nombre: str) -> str:
    return f"{FECHA}_{nombre}"


def descargar(solo: set | None = None):
    s = _sesion()
    lote = []
    for nombre, url in FUENTES:
        if solo and nombre not in solo:
            continue
        d = RAW / carpeta(nombre)
        if d.exists() and any(d.iterdir()):
            print("ya existe (no se re-descarga):", d)
            continue
        d.mkdir(parents=True, exist_ok=True)
        meta = {"source_url": url, "fecha_descarga": FECHA, "red": "wifi 0.0.0.0 (estático)"}
        try:
            r = s.get(url, timeout=40)
            meta.update(status_estatico=r.status_code, url_final_estatico=r.url)
            (d / "pagina.html").write_bytes(r.content)
        except Exception as e:  # noqa: BLE001
            meta.update(status_estatico=0, error_estatico=str(e)[:300])
        lote.append((d, url, meta))
    if not lote:
        return
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        ctx = b.new_context(locale="es-MX", user_agent=UA + " Edg/124.0")
        for d, url, meta in lote:
            pg = ctx.new_page()
            try:
                r = pg.goto(url, timeout=60000, wait_until="domcontentloaded")
                pg.wait_for_timeout(9000)
                meta.update(status_render=r.status if r else 0, url_final_render=pg.url,
                            nota_render="renderizada con Microsoft Edge headless (Playwright)")
                (d / "pagina_renderizada.html").write_text(pg.content(), encoding="utf-8")
            except Exception as e:  # noqa: BLE001
                meta.update(status_render=0, error_render=str(e)[:300])
            pg.close()
            (d / "_descarga.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
            print(meta.get("status_estatico"), meta.get("status_render"), d.name)
        b.close()


def extraer():
    resumen = []
    for nombre, url in FUENTES:
        rd, pd = RAW / carpeta(nombre), PROC / carpeta(nombre)
        if not (rd / "_descarga.json").exists():
            print("sin crudo:", rd)
            continue
        meta = json.loads((rd / "_descarga.json").read_text(encoding="utf-8"))
        avisos = [f"descarga: status_estatico={meta.get('status_estatico')}; status_render={meta.get('status_render')}; "
                  f"url_final={meta.get('url_final_render') or meta.get('url_final_estatico')}"]
        mejor = ([], "(sin contenedor)", "(sin título)", None, None)
        for arch, st in (("pagina.html", meta.get("status_estatico")),
                         ("pagina_renderizada.html", meta.get("status_render"))):
            if not (rd / arch).exists() or st != 200:
                continue
            html = (rd / arch).read_bytes().decode("utf-8", errors="replace")
            bl, via = _texto_html(html)
            bl = _dedup_consecutivos(bl)
            if sum(map(len, bl)) > sum(map(len, mejor[0])):
                mejor = (bl, via, _titulo(html), arch, st)
        bloques, via, titulo, arch, st = mejor
        avisos.append(f"extracción: desde {arch}; bloques de {via}")
        ok = st == 200 and sum(map(len, bloques)) > 300 and nombre not in FORZAR_PENDIENTE
        if nombre in FORZAR_PENDIENTE:
            avisos.append("[PENDIENTE] " + FORZAR_PENDIENTE[nombre])
        elif not ok:
            avisos.append("[PENDIENTE] la página no cargó o no trae el texto (requiere sesión, bloqueo o SPA sin contenido)")
        reg = {"source_url": url, "url_hash": calcular_hash(url), "tipo": "texto", "titulo": titulo,
               "idioma": _idioma(" ".join(bloques)), "duracion_seg": 0, "metodo": "extractor_articulo",
               "verificada": bool(ok),
               "segmentos": [{"inicio_seg": None, "fin_seg": None, "texto": b, "confianza": 1.0}
                             for b in bloques] if ok else [],
               "avisos": avisos}
        pd.mkdir(parents=True, exist_ok=True)
        for viejo in list(pd.glob("*.json")) + list(pd.glob("*.md")):
            viejo.unlink()
        r = escribir_salida(pd, reg)
        resumen.append({"carpeta": pd.name, "estado": r["estado"] if ok else "PENDIENTE", "json": str(r["json"]),
                        "titulo": titulo, "hash": reg["url_hash"], "segmentos": len(reg["segmentos"]),
                        "chars": sum(map(len, bloques)), "source_url": url})
        print("%-10s %4d seg %7d chars  %s" % (resumen[-1]["estado"], resumen[-1]["segmentos"],
                                                resumen[-1]["chars"], pd.name))
    LOG.parent.mkdir(exist_ok=True)
    LOG.write_text(json.dumps(resumen, ensure_ascii=False, indent=1), encoding="utf-8")


def indice():
    res = json.loads(LOG.read_text(encoding="utf-8"))
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
        {"extraer": extraer, "indice": indice}[accion]()
