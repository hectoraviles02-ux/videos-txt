# -*- coding: utf-8 -*-
"""
Extractor de VIDEO.
Estrategia (NO usa ffmpeg CLI; faster-whisper decodifica via PyAV):
  1. yt-dlp lee metadatos (titulo, duracion, subtitulos disponibles).
  2. Si HAY subtitulos (manuales preferidos; si no, automaticos) en idioma
     preferido -> se bajan y parsean. metodo = subtitulos_oficiales.
  3. Si NO hay -> se baja el mejor stream de audio TAL CUAL (sin reconvertir,
     por eso NO hace falta ffmpeg) y se transcribe con faster-whisper.
     metodo = auto-transcripcion, con confianza por segmento.
  4. Si se pasa --con-video -> tambien baja el video a raw/ (opcional).
Nada se inventa: lo inaudible/faltante se marca [INAUDIBLE], no se rellena.
"""
import math
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comun import (utf8_stdout, crear_directorios, escribir_salida,
                   append_indice, append_memoria, calcular_hash, slugify)

IDIOMAS_PREF = ["es", "es-419", "es-ES", "en"]
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")  # base/small/medium


def _detectar_ffmpeg() -> bool:
    """Detecta si ffmpeg esta instalado y disponible en PATH."""
    return shutil.which("ffmpeg") is not None


def _parse_vtt(texto: str) -> list:
    """WebVTT -> [{inicio_seg, fin_seg, texto, confianza}]. Sin dependencias."""
    import re

    def t2s(t):
        t = t.replace(",", ".")
        parts = [float(x) for x in t.split(":")]
        while len(parts) < 3:
            parts.insert(0, 0.0)
        return parts[-3] * 3600 + parts[-2] * 60 + parts[-1]

    segs, cur = [], None
    for line in texto.splitlines():
        line = line.strip()
        if "-->" in line:
            a, _, b = line.partition("-->")
            ini = re.search(r"[\d:.,]+", a)
            fin = re.search(r"[\d:.,]+", b)
            cur = {"inicio_seg": t2s(ini.group()) if ini else None,
                   "fin_seg": t2s(fin.group()) if fin else None,
                   "texto": "", "confianza": 1.0}
            segs.append(cur)
        elif line and cur is not None and not line.isdigit() and not line.startswith("WEBVTT"):
            cur["texto"] = (cur["texto"] + " " + re.sub(r"<[^>]+>", "", line)).strip()
    out, prev = [], None
    for s in segs:
        if not s["texto"]:
            continue
        txt = s["texto"]
        # Dedup solapamiento: auto-subs repiten finales de linea anterior
        if prev is not None:
            max_check = min(len(txt), len(prev))
            overlap = 0
            for i in range(1, max_check + 1):
                if txt[:i] == prev[-i:]:
                    overlap = i
            if overlap > 0:
                txt = txt[overlap:].lstrip()
                if not txt:
                    continue  # era solo solapamiento
        s["texto"] = txt
        s["inicio_seg"] = round(s["inicio_seg"], 2) if s["inicio_seg"] is not None else None
        s["fin_seg"] = round(s["fin_seg"], 2) if s["fin_seg"] is not None else None
        out.append(s)
        prev = txt
    return out


def _try_subtitulos(url, info, raw_dir):
    import yt_dlp
    subs = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    fuente, lang = None, None
    for L in IDIOMAS_PREF:
        if L in subs:
            fuente, lang = "subtitles", L
            break
    if not fuente:
        for L in IDIOMAS_PREF:
            if L in auto:
                fuente, lang = "automatic_captions", L
                break
    if not fuente:
        return None

    opts = {"skip_download": True,
            "writesubtitles": fuente == "subtitles",
            "writeautomaticsub": fuente == "automatic_captions",
            "subtitleslangs": [lang], "subtitlesformat": "vtt",
            "outtmpl": str(raw_dir / "sub"), "quiet": True,
            "no_warnings": True, "noprogress": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    vtts = list(raw_dir.glob("*.vtt"))
    if not vtts:
        return None
    segs = _parse_vtt(vtts[0].read_text(encoding="utf-8", errors="ignore"))
    if not segs:
        return None
    if fuente == "subtitles":
        return {"segmentos": segs, "metodo": "subtitulos_oficiales", "idioma": lang, "avisos": []}
    # subtitulos AUTOMATICOS no son oficiales: honesto en metodo y aviso
    return {"segmentos": segs, "metodo": "auto-transcripcion", "idioma": lang,
            "avisos": ["Subtitulos AUTOMATICOS de la plataforma (no oficiales)"]}


def _transcribir_audio(url, raw_dir):
    import yt_dlp
    # bestaudio SIN reconvertir -> no requiere ffmpeg CLI
    log_path = raw_dir / "transcripcion_audio.log"
    opts = {"format": "bestaudio/best", "outtmpl": str(raw_dir / "audio.%(ext)s"),
            "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True}
    with open(log_path, "w", encoding="utf-8") as log_f:
        import contextlib
        with contextlib.redirect_stdout(log_f), contextlib.redirect_stderr(log_f):
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
    audios = [p for p in raw_dir.iterdir() if p.stem == "audio"]
    if not audios:
        raise RuntimeError("no se pudo bajar el audio")

    from faster_whisper import WhisperModel
    model_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "modelos", "whisper-tiny"))
    model = WhisperModel(model_path, device="cpu", compute_type="int8", local_files_only=True)
    segments, info = model.transcribe(str(audios[0]), vad_filter=True)
    segs = []
    for s in segments:
        conf = round(min(1.0, math.exp(s.avg_logprob)), 3) if s.avg_logprob is not None else None
        txt = (s.text or "").strip()
        segs.append({"inicio_seg": round(s.start, 2), "fin_seg": round(s.end, 2),
                     "texto": txt if txt else "[INAUDIBLE]", "confianza": conf})
    avisos = [f"[INAUDIBLE] {int(s['inicio_seg'])//60:02d}:{int(s['inicio_seg'])%60:02d}"
              for s in segs if s["texto"] == "[INAUDIBLE]"]
    return {"segmentos": segs, "metodo": "auto-transcripcion", "idioma": info.language, "avisos": avisos}


def descargar_video(url: str, raw_dir: Path) -> dict:
    """Descarga el video completo a raw/. Solo si se pide con --con-video.
    Retorna {exito, ruta, calidad, aviso}."""
    import yt_dlp
    import contextlib
    
    ffmpeg_ok = _detectar_ffmpeg()
    
    if ffmpeg_ok:
        fmt = "bestvideo+bestaudio/best"
        calidad = "full"
        aviso = None
    else:
        fmt = "best"
        calidad = "720p"
        aviso = "[AVISO] sin ffmpeg: video limitado a ~720p"
    
    opts = {
        "format": fmt,
        "outtmpl": str(raw_dir / "video.%(ext)s"),
        "quiet": True, "no_warnings": True, "noprogress": True,
        "noplaylist": True,
        "merge_output_format": "mp4" if ffmpeg_ok else None,
    }
    
    log_path = raw_dir / "descarga_video.log"
    try:
        with open(log_path, "w", encoding="utf-8") as log_f:
            with contextlib.redirect_stdout(log_f), contextlib.redirect_stderr(log_f):
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])
        
        videos = list(raw_dir.glob("video.*"))
        if videos:
            tamano_mb = round(videos[0].stat().st_size / (1024*1024), 1)
            print(f"  Video: {videos[0].name} ({tamano_mb} MB)")
            if aviso:
                print(f"  {aviso}")
            return {"exito": True, "ruta": str(videos[0]), "calidad": calidad, "aviso": aviso}
        else:
            return {"exito": False, "ruta": None, "calidad": None,
                    "aviso": "No se encontro archivo de video"}
    except Exception as e:
        return {"exito": False, "ruta": None, "calidad": None,
                "aviso": f"Error descargando video: {e}"}


def procesar(url: str, raw_dir: Path, proc_dir: Path) -> dict:
    import yt_dlp
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    reg = {"source_url": url, "url_hash": calcular_hash(url), "tipo": "video",
           "titulo": info.get("title") or "(sin titulo)",
           "duracion_seg": int(info.get("duration") or 0), "verificada": True}
    datos = _try_subtitulos(url, info, raw_dir) or _transcribir_audio(url, raw_dir)
    reg.update(datos)
    return escribir_salida(proc_dir, reg)


def main():
    utf8_stdout()
    if len(sys.argv) < 2:
        print("Uso: python procesar_video.py <URL> [--con-video]")
        sys.exit(1)
    url = sys.argv[1]
    con_video = "--con-video" in sys.argv
    url_hash = calcular_hash(url)
    
    # Obtener titulo para slug (silenciado)
    import yt_dlp
    import contextlib
    with open(os.devnull, "w") as devnull:
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
                info = ydl.extract_info(url, download=False)
    titulo = info.get("title") or "sin-titulo"
    slug = slugify(titulo)
    
    print(f"Titulo: {titulo}")
    print(f"Hash: {url_hash}")
    
    raw_dir, proc_dir = crear_directorios(slug, url_hash)
    
    # Descarga de video completo (opcional)
    if con_video:
        ffmpeg_ok = _detectar_ffmpeg()
        if not ffmpeg_ok:
            print("  [AVISO] ffmpeg no detectado: calidad limitada a ~720p")
        print("  Descargando video completo...")
        vinfo = descargar_video(url, raw_dir)
        if not vinfo["exito"]:
            print(f"  [ERROR] {vinfo['aviso']}")
        else:
            print(f"  OK video: {Path(vinfo['ruta']).name}")
    
    try:
        res = procesar(url, raw_dir, proc_dir)
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        append_memoria("error", f"video fallo {url}", f"{type(e).__name__}: {e}")
        sys.exit(2)
    import json
    reg = json.loads(res["json"].read_text(encoding="utf-8"))
    append_indice(reg["titulo"], "video", reg["metodo"], res["estado"], reg["url_hash"])
    print(f"OK [{res['estado']}] metodo={reg['metodo']} segmentos={len(reg['segmentos'])}")
    print(f"  {res['md']}")
    print(f"  {res['json']}")


if __name__ == "__main__":
    main()
