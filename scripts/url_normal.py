# -*- coding: utf-8 -*-
"""
Normalizacion de URL VERSIONADA (aditiva) — F1.

NO reemplaza a scripts/comun.py::normalizar_url (v1), que sigue siendo la
funcion que usa el extractor hoy. Esto introduce una v2 mas robusta y, sobre
todo, un RESOLVEDOR que reconoce identificadores historicos: dada una URL,
devuelve los hashes candidatos (v2 y v1) para que un indice/manifiesto pueda
encontrar un registro aunque se haya guardado con el algoritmo viejo.

Reglas (decision F1): el formato del hash se mantiene compatible con el
contrato canonico (sha1[:12]); lo que cambia entre versiones es la
NORMALIZACION previa, no el formato de salida. Asi no hay sustitucion masiva:
los registros existentes conservan su hash v1 y siguen resolviendo.
"""
import hashlib
from urllib.parse import urlparse, urlencode, parse_qsl

VERSION_ACTUAL = "v2"
_TRACKING = {"fbclid", "gclid", "ref", "ref_src", "mc_cid", "mc_eid", "igshid"}


def _sha1_12(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:12]


def normalizar_url_v1(url: str) -> str:
    """Replica EXACTA de scripts/comun.py::normalizar_url (historico)."""
    p = urlparse(url.strip())
    params = parse_qsl(p.query, keep_blank_values=True)
    limpias = [(k, v) for k, v in params
               if not k.startswith("utm_") and k not in ("fbclid", "gclid", "ref", "ref_src")]
    # comun.py usa parse_qs+urlencode(doseq) -> conserva orden de aparicion
    q = urlencode(limpias, doseq=True)
    return f"{p.scheme}://{p.netloc}{p.path}{'?' + q if q else ''}"


def normalizar_url_v2(url: str) -> str:
    """v2: minusculas en scheme/host, quita 'www.', quita '/' final,
    ORDENA los query params (asi ?a&b == ?b&a), amplia la lista de tracking,
    y unifica youtu.be/<id> -> youtube.com/watch?v=<id>."""
    p = urlparse(url.strip())
    scheme = (p.scheme or "https").lower()
    host = (p.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = p.path or ""
    params = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
              if not k.startswith("utm_") and k.lower() not in _TRACKING]
    # youtu.be/<id> -> youtube.com/watch?v=<id>
    if host == "youtu.be" and path.strip("/"):
        vid = path.strip("/").split("/")[0]
        host, path = "youtube.com", "/watch"
        if not any(k == "v" for k, _ in params):
            params.append(("v", vid))
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    params.sort()  # orden canonico
    q = urlencode(params, doseq=True)
    return f"{scheme}://{host}{path}{'?' + q if q else ''}"


def calcular_hash(url: str, version: str = VERSION_ACTUAL) -> str:
    """Hash 12-char (formato compatible con el contrato canonico)."""
    norm = normalizar_url_v2(url) if version == "v2" else normalizar_url_v1(url)
    return _sha1_12(norm)


def hashes_candidatos(url: str) -> list[str]:
    """Todos los hashes con que una URL pudo haberse guardado (v2 primero,
    luego v1 historico). Un resolvedor prueba estos contra el indice."""
    cands = []
    for h in (calcular_hash(url, "v2"), calcular_hash(url, "v1")):
        if h not in cands:
            cands.append(h)
    return cands


def resolver_en_indice(url: str, texto_indice: str) -> str | None:
    """Devuelve el primer hash candidato presente en el texto del indice,
    o None. Reconoce registros historicos (v1) sin reescribirlos."""
    for h in hashes_candidatos(url):
        if h in texto_indice:
            return h
    return None
