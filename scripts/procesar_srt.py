#!/usr/bin/env python3
"""
Procesa un archivo SRT a JSON y MD
Uso: python procesar_srt.py <archivo.srt> <url_hash>
"""
import sys
import re
import json
from datetime import datetime
from pathlib import Path


def parse_srt(srt_path: str) -> list:
    """Parsea un archivo SRT a lista de segmentos"""
    with open(srt_path, 'r', encoding='utf-8') as f:
        contenido = f.read()
    
    # Patrón SRT: número, timestamp, texto
    patron = r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\n(.*?)(?=\n\n|\Z)'
    matches = re.findall(patron, contenido, re.DOTALL)
    
    segmentos = []
    for num, inicio, fin, texto in matches:
        # Convertir timestamp a segundos
        ini_seg = timestamp_a_segundos(inicio)
        fin_seg = timestamp_a_segundos(fin)
        texto_limpio = texto.strip()
        
        if texto_limpio and texto_limpio != '[música]':
            segmentos.append({
                "inicio_seg": round(ini_seg, 2),
                "fin_seg": round(fin_seg, 2),
                "texto": texto_limpio,
                "confianza": 0.95  # Auto-generado, confianza alta
            })
    
    return segmentos


def timestamp_a_segundos(ts: str) -> float:
    """Convierte HH:MM:SS,mmm a segundos"""
    horas, minutos, resto = ts.split(':')
    segundos, milisegundos = resto.split(',')
    return int(horas) * 3600 + int(minutos) * 60 + int(segundos) + int(milisegundos) / 1000


def segundos_a_timestamp(seg: float) -> str:
    """Convierte segundos a HH:MM:SS"""
    horas = int(seg // 3600)
    minutos = int((seg % 3600) // 60)
    segundos = int(seg % 60)
    return f"{horas:02d}:{minutos:02d}:{segundos:02d}"


def generar_json(segmentos: list, url_hash: str, titulo: str, url: str) -> dict:
    """Genera el JSON de salida"""
    return {
        "source_url": url,
        "url_hash": url_hash,
        "tipo": "video",
        "titulo": titulo,
        "idioma": "es",
        "fecha_extraccion": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "metodo": "subtitulos_oficiales",
        "duracion_seg": segmentos[-1]["fin_seg"] if segmentos else 0,
        "verificada": True,
        "segmentos": segmentos,
        "avisos": []
    }


def generar_md(segmentos: list, titulo: str, url: str, url_hash: str) -> str:
    """Genera el Markdown de salida"""
    lineas = [
        f"# {titulo}",
        "",
        f"- **Fuente:** {url}",
        f"- **Hash:** `{url_hash}`",
        f"- **Método:** subtítulos_oficiales",
        f"- **Fecha:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "---",
        "",
    ]
    
    for seg in segmentos:
        ts = segundos_a_timestamp(seg["inicio_seg"])
        lineas.append(f"**[{ts}]** {seg['texto']}")
        lineas.append("")
    
    return "\n".join(lineas)


def main():
    if len(sys.argv) < 3:
        print("Uso: python procesar_srt.py <archivo.srt> <url_hash>")
        sys.exit(1)
    
    srt_path = sys.argv[1]
    url_hash = sys.argv[2]
    
    # Parsear SRT
    print(f"Parseando: {srt_path}")
    segmentos = parse_srt(srt_path)
    print(f"Segmentos extraídos: {len(segmentos)}")
    
    # Cargar datos del video (hardcoded por ahora)
    url = "https://www.youtube.com/live/hxxNxzOuDWM"
    titulo = "Sesión 1 - Módulo de Redes"
    
    # Generar JSON
    data = generar_json(segmentos, url_hash, titulo, url)
    json_path = Path(srt_path).parent / f"{url_hash}.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"JSON: {json_path}")
    
    # Generar MD
    md_content = generar_md(segmentos, titulo, url, url_hash)
    md_path = Path(srt_path).parent / f"{url_hash}.md"
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md_content)
    print(f"MD: {md_path}")
    
    print("\n✓ Extracción completada")


if __name__ == "__main__":
    main()
