# -*- coding: utf-8 -*-
"""
Integración LLM Local para sintetizar.py
Requiere: Ollama instalado y corriendo (https://ollama.com)

USO:
1. Instalar Ollama: https://ollama.com
2. Descargar modelo: ollama pull qwen2.5:14b
3. Ejecutar: python sintetizar.py json1.json json2.json --usar-llm

MODELOS RECOMENDADOS:
- qwen2.5:14b (mejor español, 9-10GB RAM)
- llama3:8b (rápido, 5GB RAM)
- mistral:7b (balance, 4GB RAM)

FALLBACK AUTOMÁTICO:
Si Ollama no está corriendo o el modelo falla,
el script vuelve automáticamente a reglas hardcoded.
"""
import json
import urllib.request
import urllib.error
from typing import Optional


class LLMError(Exception):
    """Error en la comunicación con el LLM"""
    pass


def _ollama_request(endpoint: str, data: dict = None, method: str = "GET") -> dict:
    """Realiza petición a la API de Ollama"""
    try:
        url = f"http://localhost:11434/{endpoint}"
        headers = {"Content-Type": "application/json"}
        
        if data and method == "POST":
            req_data = json.dumps(data).encode("utf-8")
        else:
            req_data = None
        
        req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
        
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise LLMError(f"Ollama no está corriendo o no es accesible: {e}")
    except Exception as e:
        raise LLMError(f"Error en petición LLM: {e}")


def inicializar_llm(modelo: Optional[str] = None) -> str:
    """Detecta modelo disponible o usa el especificado"""
    try:
        resp = _ollama_request("api/tags", method="GET")
        modelos_disponibles = [m["name"] for m in resp.get("models", [])]
        
        if not modelos_disponibles:
            raise LLMError("No hay modelos instalados en Ollama")
        
        if modelo:
            # Buscar modelo especificado (parcial match)
            for m in modelos_disponibles:
                if modelo.lower() in m.lower():
                    return m
            raise LLMError(f"Modelo '{modelo}' no encontrado. Disponibles: {modelos_disponibles}")
        
        # Usar primer modelo disponible
        return modelos_disponibles[0]
    
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(f"Error inicializando LLM: {e}")


def _generar_con_llm(prompt: str, modelo: str) -> str:
    """Genera texto usando Ollama"""
    resp = _ollama_request("api/generate", {
        "model": modelo,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.7,
            "top_p": 0.9,
            "num_predict": 500
        }
    }, method="POST")
    return resp.get("response", "").strip()


def generar_titulos_con_llm(puntos: list, angulo: str, modelo: str) -> list:
    """Títulos inteligentes con comprensión semántica"""
    # Resumir puntos para el prompt (max 2000 chars)
    puntos_texto = "\n".join([f"- {p['texto'][:150]}" for p in puntos[:15]])
    
    prompt = f"""Eres un guionista de YouTube experto en copywriting.
Analiza estos puntos de fuentes sobre un tema y genera 5 títulos atractivos.

PUNTOS:
{puntos_texto}

ÁNGULO DETECTADO: {angulo}

REGLAS:
- Identifica el TEMA CENTRAL (no palabras sueltas como "industria" o "problemas")
- Usa fórmulas de copywriting probadas:
  * comparativa: "X vs Y: La verdad que nadie te cuenta"
  * mito_vs_dato: "Lo que te mintieron sobre X"
  * sintesis: "X: La guía definitiva"
  * explicacion_simple: "X explicado en 5 minutos"
- NO uses muletillas ("eh", "este", "bueno")
- Máximo 60 caracteres por título
- Español natural, sin errores gramaticales

OUTPUT: Lista exacta de 5 títulos, uno por línea, sin numeración."""
    
    respuesta = _generar_con_llm(prompt, modelo)
    
    # Parsear respuesta
    lineas = [l.strip() for l in respuesta.split("\n") if l.strip()]
    # Limpiar numeración si existe
    titulos = []
    for l in lineas[:5]:
        l = l.lstrip("0123456789.-) ")
        if l and len(l) > 10:
            titulos.append(l)
    
    # Si no hay suficientes, generar fallback
    while len(titulos) < 5:
        titulos.append(f"Título {len(titulos)+1}: {angulo}")
    
    return titulos[:5]


def generar_gancho_con_llm(puntos: list, angulo: str, modelo: str) -> str:
    """Gancho con contexto real"""
    # Buscar puntos más sustanciales
    puntos_buenos = sorted(puntos, key=lambda p: len(p.get("texto", "")), reverse=True)
    puntos_texto = "\n".join([f"- {p['texto'][:200]}" for p in puntos_buenos[:10]])
    
    prompt = f"""Eres un narrador de YouTube experto en retención.
Genera un gancho de apertura (0-15 segundos) para un video.

PUNTOS CLAVE DEL CONTENIDO:
{puntos_texto}

ÁNGULO: {angulo}

ESTRUCTURA H.I.V.E.:
1. Hook: Dato impactante o pregunta disruptiva (1 oración)
2. Interest: Promesa de valor ("En este video vas a ver...")
3. Value: Anticipación de descubrimiento
4. Engagement: Tensión ("pero hay un detalle...")

REGLAS:
- Frase completa con sujeto + verbo + dato
- NO uses muletillas ("eh", "este", "bueno", "o sea")
- NO copies texto literal de las fuentes
- Máximo 3 oraciones
- Español natural y fluido

OUTPUT: Solo el gancho, sin explicaciones."""
    
    respuesta = _generar_con_llm(prompt, modelo)
    
    # Limpiar respuesta
    respuesta = respuesta.strip()
    if len(respuesta) < 20:
        return "La mayoría cree que sabe, pero la realidad es otra. En este video te muestro la evidencia."
    
    return respuesta


def sintetizar_bloques_con_llm(puntos: list, modelo: str) -> list:
    """Párrafos reales de 2-3 oraciones"""
    if len(puntos) < 3:
        return None
    
    # Agrupar puntos en chunks de 5
    bloques = []
    for i in range(0, min(len(puntos), 15), 5):
        chunk = puntos[i:i+5]
        puntos_texto = "\n".join([f"- {p['texto'][:150]}" for p in chunk])
        
        prompt = f"""Eres un guionista que sintetiza contenido de múltiples fuentes.
Convierte estos puntos en un párrafo coherente de 2-3 oraciones.

PUNTOS:
{puntos_texto}

REGLAS:
- Sintetiza, NO copies texto literal
- Mantén la idea principal de cada punto
- Español natural y fluido
- Incluye referencia sutil a las fuentes (ej: "según expertos", "los datos muestran")

OUTPUT: Solo el párrafo, sin explicaciones."""
        
        respuesta = _generar_con_llm(prompt, modelo)
        if respuesta and len(respuesta) > 30:
            bloques.append({
                "texto": respuesta.strip(),
                "fuentes": [p.get("fuente", "?") for p in chunk]
            })
    
    return bloques if bloques else None


def detectar_contradicciones_con_llm(puntos: list, modelo: str) -> list:
    """Análisis semántico de desacuerdos"""
    # Agrupar por fuente
    por_fuente = {}
    for p in puntos:
        fuente = p.get("fuente", "unknown")
        if fuente not in por_fuente:
            por_fuente[fuente] = []
        por_fuente[fuente].append(p["texto"][:150])
    
    if len(por_fuente) < 2:
        return []
    
    fuentes_texto = ""
    for fuente, textos in por_fuente.items():
        fuentes_texto += f"\nFUENTE {fuente}:\n"
        fuentes_texto += "\n".join(textos[:5]) + "\n"
    
    prompt = f"""Analiza estas fuentes y identifica contradicciones o desacuerdos.

{fuentes_texto}

REGLAS:
- Busca afirmaciones contradictorias sobre el mismo tema
- Identifica datos conflictivos
- Sé específico: cita qué fuente dice qué

OUTPUT: Lista de contradicciones encontradas. Si no hay, responde "Sin contradicciones significativas"."""
    
    respuesta = _generar_con_llm(prompt, modelo)
    
    if "sin contradicciones" in respuesta.lower():
        return []
    
    # Parsear contradicciones
    contradicciones = []
    for linea in respuesta.split("\n"):
        linea = linea.strip()
        if linea and len(linea) > 20 and not linea.startswith("#"):
            contradicciones.append({"descripcion": linea})
    
    return contradicciones[:5]
