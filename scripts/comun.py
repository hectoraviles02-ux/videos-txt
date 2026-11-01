# -*- coding: utf-8 -*-
"""
Extractor de Contenido — nucleo compartido (UNICA fuente de verdad).

Aqui viven hash, normalizacion, verificacion, rutas, y la escritura de la
salida (.md + .json) con el esquema fijo del CLAUDE.md. Los scripts
procesar_url.py / procesar_video.py / procesar_texto.py importan de aqui;
NO reimplementan nada. Asi el modelo (por debil que sea) no puede desviar
el formato: la logica esta en el codigo, no en su criterio.
"""
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urlencode, parse_qs

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "raw"
PROCESADO_DIR = BASE_DIR / "procesado"
INDICE_FILE = BASE_DIR / "INDICE.md"
MEMORIA_FILE = BASE_DIR / "MEMORIA.md"

METODOS_VALIDOS = ("subtitulos_oficiales", "auto-transcripcion", "extractor_articulo")


def utf8_stdout():
    """Evita el UnicodeEncodeError de la consola de Windows."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def normalizar_url(url: str) -> str:
    """Quita tracking params (utm_*, fbclid, gclid) y fragmentos."""
    p = urlparse(url.strip())
    params = parse_qs(p.query)
    limpias = {k: v for k, v in params.items()
               if not k.startswith("utm_") and k not in ("fbclid", "gclid", "ref", "ref_src")}
    q = urlencode(limpias, doseq=True)
    return f"{p.scheme}://{p.netloc}{p.path}{'?' + q if q else ''}"


def calcular_hash(url: str) -> str:
    """SHA1 (12 chars) de la URL normalizada = llave natural."""
    return hashlib.sha1(normalizar_url(url).encode()).hexdigest()[:12]


def url_ya_procesada(url_hash: str) -> bool:
    if not INDICE_FILE.exists():
        return False
    return url_hash in INDICE_FILE.read_text(encoding="utf-8")


class DestinoNoPermitido(ValueError):
    """La URL apunta a un destino que no se descarga (esquema raro o red interna)."""


def destino_seguro(url: str) -> tuple:
    """31-oct-2026 (auditoría P1-SSRF): solo http/https hacia direcciones PÚBLICAS de internet.

    Resuelve el host y rechaza si CUALQUIERA de sus direcciones no es global (localhost, red privada
    10/172.16/192.168, enlace local 169.254, CGNAT, ::1...). El motivo nunca lleva la IP resuelta.
    """
    import ipaddress
    import socket
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return False, "esquema no permitido: %s" % (p.scheme or "(vacío)")
    host = p.hostname
    if not host:
        return False, "URL sin host"
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as e:
        return False, "el host no resuelve (%s)" % e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            return False, "destino interno no permitido (%s)" % host
    return True, "ok"


def obtener_seguro(url: str, metodo: str = "GET", headers: dict = None, timeout: int = 30,
                   max_saltos: int = 5, **kw):
    """Petición HTTP que revisa el destino ANTES de cada salto: las redirecciones se siguen a mano y
    cada una pasa otra vez por destino_seguro (un 302 hacia la red interna se rechaza).
    Devuelve la respuesta final con el atributo `url_final`. Lanza DestinoNoPermitido."""
    import requests
    from urllib.parse import urljoin
    actual = url
    for _ in range(max_saltos + 1):
        ok, motivo = destino_seguro(actual)
        if not ok:
            raise DestinoNoPermitido(motivo)
        r = requests.request(metodo, actual, headers=headers, timeout=timeout, allow_redirects=False, **kw)
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
            actual = urljoin(actual, r.headers["location"])
            if r.status_code == 303:
                metodo = "GET"
            continue
        r.url_final = actual
        return r
    raise DestinoNoPermitido("demasiadas redirecciones (más de %d)" % max_saltos)


def verificar_url(url: str) -> dict:
    """Compuerta: ¿la URL resuelve? HEAD y si rebota, GET. Solo destinos públicos (ver destino_seguro)."""
    hdr = {"User-Agent": "Mozilla/5.0"}
    try:
        r = obtener_seguro(url, "HEAD", headers=hdr, timeout=15)
        if r.status_code in (403, 405, 501):  # muchos server no aceptan HEAD
            r = obtener_seguro(url, "GET", headers=hdr, timeout=15, stream=True)
            r.close()
            ok = r.status_code < 400
            return {"resuelve": ok, "status": r.status_code, "razon": "OK (GET)" if ok else "HTTP %d" % r.status_code}
        ok = r.status_code < 400
        return {"resuelve": ok, "status": r.status_code, "razon": "OK" if ok else "HTTP %d" % r.status_code}
    except DestinoNoPermitido as e:
        return {"resuelve": False, "status": 0, "razon": "bloqueada: %s" % e}
    except Exception as e:
        return {"resuelve": False, "status": 0, "razon": str(e)}


def slugify(texto: str) -> str:
    """Convierte texto a slug: minusculas, sin acentos, guiones."""
    import re
    import unicodedata
    texto = unicodedata.normalize("NFKD", texto)
    texto = texto.encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^a-zA-Z0-9\s]", "", texto)
    texto = re.sub(r"\s+", "-", texto.lower().strip())
    texto = re.sub(r"-+", "-", texto)
    return texto[:50]


def nombre_archivo(slug: str, url_hash: str) -> str:
    """Genera nombre de archivo: slug_hash"""
    return f"{slug}_{url_hash}"


def crear_directorios(slug: str, url_hash: str) -> tuple:
    hoy = datetime.now().strftime("%Y-%m-%d")
    nombre = nombre_archivo(slug, url_hash)
    raw = RAW_DIR / f"{hoy}_{nombre}"
    proc = PROCESADO_DIR / f"{hoy}_{nombre}"
    raw.mkdir(parents=True, exist_ok=True)
    proc.mkdir(parents=True, exist_ok=True)
    return raw, proc


def _mmss(seg) -> str:
    if seg is None:
        return "--:--"
    seg = int(seg)
    return f"{seg // 60:02d}:{seg % 60:02d}"


def _estado(reg: dict) -> str:
    """EXTRAIDO solo si estan metodo + verificada + segmentos con contenido."""
    ok = (reg.get("metodo") in METODOS_VALIDOS
          and reg.get("verificada") is True
          and isinstance(reg.get("segmentos"), list)
          and len(reg["segmentos"]) > 0)
    return "EXTRAIDO" if ok else "BORRADOR"


def escribir_salida(proc_dir: Path, reg: dict) -> dict:
    """Escribe <slug>.json y <slug>.md con el esquema fijo. Devuelve
    un resumen {estado, json, md} para el indice."""
    reg.setdefault("avisos", [])
    reg.setdefault("idioma", "und")
    reg.setdefault("duracion_seg", 0)
    reg["fecha_extraccion"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    estado = _estado(reg)

    # Usar slug del titulo + hash para nombre descriptivo
    titulo_slug = slugify(reg.get("titulo", "sin-titulo"))
    archivo = nombre_archivo(titulo_slug, reg["url_hash"])
    json_path = proc_dir / f"{archivo}.json"
    md_path = proc_dir / f"{archivo}.md"

    json_path.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- Markdown legible ---
    L = []
    L.append(f"# {reg.get('titulo', '(sin titulo)')}")
    L.append("")
    L.append(f"- **Fuente:** {reg['source_url']}")
    L.append(f"- **Tipo:** {reg['tipo']}  ·  **Idioma:** {reg['idioma']}")
    L.append(f"- **Metodo:** {reg['metodo']}")
    L.append(f"- **Extraccion:** {reg['fecha_extraccion']}  ·  **Estado:** {estado}")
    if reg.get("duracion_seg"):
        L.append(f"- **Duracion:** {_mmss(reg['duracion_seg'])}")
    L.append(f"- **Hash:** `{reg['url_hash']}`")
    L.append("")
    if reg.get("avisos"):
        L.append("> **Avisos:** " + "; ".join(reg["avisos"]))
        L.append("")
    L.append("---")
    L.append("")
    con_tiempo = reg["tipo"] == "video"
    for s in reg.get("segmentos", []):
        txt = (s.get("texto") or "").strip()
        if not txt:
            continue
        if con_tiempo and s.get("inicio_seg") is not None:
            L.append(f"**[{_mmss(s['inicio_seg'])}]** {txt}")
        else:
            L.append(txt)
        L.append("")
    md_path.write_text("\n".join(L), encoding="utf-8")

    return {"estado": estado, "json": json_path, "md": md_path}


def append_indice(titulo: str, tipo: str, metodo: str, estado: str, url_hash: str):
    hoy = datetime.now().strftime("%Y-%m-%d")
    if not INDICE_FILE.exists():
        INDICE_FILE.write_text(
            "# Indice de Extracciones\n\n"
            "| Fecha | Titulo | Tipo | Metodo | Estado | Hash |\n"
            "|-------|--------|------|--------|--------|------|\n", encoding="utf-8")
    titulo = (titulo or "(sin titulo)").replace("|", "/")[:50]
    with INDICE_FILE.open("a", encoding="utf-8") as f:
        f.write(f"| {hoy} | {titulo} | {tipo} | {metodo} | {estado} | `{url_hash}` |\n")


def append_memoria(tipo: str, hecho: str, por_que: str):
    hoy = datetime.now().strftime("%Y-%m-%d")
    with MEMORIA_FILE.open("a", encoding="utf-8") as f:
        f.write(f"- [{hoy}] {tipo}: {hecho} -> {por_que}\n")
