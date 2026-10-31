#!/usr/bin/env python3
"""
Sintetizador de Guiones — Toma múltiples JSON y arma BORRADOR de video.
Uso: python scripts/sintetizar.py <json1> <json2> [json3...]

NO republica contenido ajeno: sintetiza, cruza y transforma.
La IA propone; la persona edita y dirige antes de publicar.
"""
import sys
import json
import re
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict, Counter

try:
    from llm_integration import (inicializar_llm, generar_titulos_con_llm,
                                  generar_gancho_con_llm, sintetizar_bloques_con_llm,
                                  detectar_contradicciones_con_llm, LLMError)
    LLM_DISPONIBLE = True
except ImportError:
    LLM_DISPONIBLE = False

BASE_DIR = Path(__file__).parent.parent
PROCESADO_DIR = BASE_DIR / "procesado"
GUIONES_DIR = BASE_DIR / "guiones"

STOPWORDS_ES = {
    "que", "como", "para", "pero", "este", "esta", "con", "por", "los", "las", "del", "una", "uno",
    "el", "la", "lo", "es", "en", "se", "su", "al", "un", "no", "ya", "muy", "mas", "fue", "ser",
    "tiene", "hay", "esto", "ese", "esa", "aquel", "aquella", "mismo", "misma",
    "nosotros", "nosotras", "ustedes", "ellos", "ellas", "yo", "tu", "vos", "vosotros",
    "podemos", "pueden", "puedo", "puedes", "puede", "tenemos", "tienen", "tengo", "tienes",
    "vamos", "van", "voy", "vas", "va", "estamos", "estan", "estoy", "estas", "esta",
    "hacemos", "hacen", "hago", "haces", "hace", "decimos", "dicen", "digo", "dices", "dice",
    "entonces", "ahora", "despues", "antes", "siempre", "nunca", "hoy", "ayer", "tambien",
    "ademas", "inclusive", "incluso", "apenas", "generalmente", "normalmente", "aunque",
    "mientras", "durante", "mediante", "segun", "traves", "todo", "toda", "todos", "todas",
    "cada", "otro", "otra", "otros", "otras", "estos", "estas", "esos", "esas", "sobre",
    "entre", "cuando", "donde", "hacia", "hasta", "desde", "sin", "tras",
    "normalmente", "generalmente", "usualmente", "frecuentemente",
    "trabajamos", "trabajar", "platicar", "platicamos"
}

MULETILLAS = r"\b(eh|este|o sea|bueno|vale|okay|pues|piho|hola|buenos dias|bienvenidos|gracias|entonces|mas o menos|digamos|osea|eso|claro|verdad|si|no|bueno)\b"

# Palabras genéricas de títulos que NO son temas (para keyword extraction)
TITULO_GENERICAS = {
    "sesión", "sesion", "módulo", "modulo", "clase", "curso", "video",
    "tutorial", "parte", "capítulo", "capitulo", "leccion", "nivel",
    "introducción", "introduccion", "conceptos", "básico", "basico",
    "avanzado", "intermedio", "principiantes", "guía", "guia",
    "descubre", "aprende", "entender", "explicado", "completo",
    "todo", "toda", "necesitas", "saber", "verdad", "realidad"
}


def limpiar_texto(texto: str) -> str:
    """Limpia muletillas, saludos y fragmentos sin sentido de transcripciones."""
    if not texto:
        return ""
    texto = texto.lower().strip()
    texto = re.sub(MULETILLAS, "", texto, flags=re.IGNORECASE)
    texto = re.sub(r'\.{3,}', '.', texto)
    texto = re.sub(r'\s+', ' ', texto).strip()
    texto = texto.strip(".,;:!?()[]\"' ")
    if len(texto) < 10:
        return ""
    return texto.capitalize()


def utf8_stdout():
    """Evita el UnicodeEncodeError de la consola de Windows."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def cargar_json(ruta: Path) -> dict:
    """Carga un archivo JSON de extracción"""
    with open(ruta, 'r', encoding='utf-8') as f:
        return json.load(f)


def extraer_puntos_clave(reg: dict) -> list:
    """Extrae puntos clave de una fuente"""
    puntos = []
    hash_corto = reg["url_hash"][:8]
    titulo = reg.get("titulo", "Sin título")
    
    # Agrupar segmentos en párrafos significativos
    parrafo_actual = []
    for seg in reg.get("segmentos", []):
        texto = (seg.get("texto") or "").strip()
        if not texto:
            continue
        
        parrafo_actual.append(texto)
        
        # Cuando el párrafo tenga sentido completo, guardarlo
        texto_completo = " ".join(parrafo_actual)
        if len(texto_completo) > 100 or texto.endswith(('.', '?', '!')):
            puntos.append({
                "texto": texto_completo,
                "fuente": hash_corto,
                "titulo_fuente": titulo,
                "url": reg["source_url"],
                "timestamp": seg.get("inicio_seg")
            })
            parrafo_actual = []
    
    # Guardar lo que quede
    if parrafo_actual:
        puntos.append({
            "texto": " ".join(parrafo_actual),
            "fuente": hash_corto,
            "titulo_fuente": titulo,
            "url": reg["source_url"],
            "timestamp": None
        })
    
    return puntos


def encontrar_coincidencias(puntos_por_fuente: dict) -> tuple:
    """Encuentra puntos que coinciden y contradicciones entre fuentes"""
    todos_los_puntos = []
    for fuente, puntos in puntos_por_fuente.items():
        for p in puntos:
            todos_los_puntos.append(p)
    
    # Buscar similitudes por palabras clave (simplificado)
    coincidencias = []
    contradicciones = []
    
    # Por ahora, retornar todos los puntos para que el modelo los analice
    return todos_los_puntos, []


def calcular_overlap_tematico(puntos: list) -> float:
    """Calcula qué tan relacionados están los temas entre fuentes (0.0 a 1.0).
    Retorna 0.0 si son temas completamente diferentes, 1.0 si son el mismo tema."""
    # Agrupar puntos por fuente
    puntos_por_fuente = defaultdict(list)
    for p in puntos:
        fuente = p.get("fuente", "unknown")
        puntos_por_fuente[fuente].append(p)
    
    # Si solo hay una fuente, asumir que es un tema coherente
    if len(puntos_por_fuente) < 2:
        return 1.0
    
    # Extraer keywords de cada fuente (usando títulos que ya están en los puntos)
    titulos_por_fuente = {}
    for fuente, pts in puntos_por_fuente.items():
        for p in pts:
            titulo = p.get("titulo_fuente", "")
            if titulo:
                titulos_por_fuente[fuente] = titulo.lower()
                break
    
    # Si no hay títulos, extraer keywords del contenido
    keywords_por_fuente = {}
    for fuente, pts in puntos_por_fuente.items():
        if fuente in titulos_por_fuente:
            # Usar título
            palabras = [w.strip(".,;:!?()[]\"'¿¡-") for w in titulos_por_fuente[fuente].split()]
            keywords_por_fuente[fuente] = set(
                w for w in palabras 
                if len(w) > 3 and w not in STOPWORDS_ES and w not in TITULO_GENERICAS
            )
        else:
            # Extraer de contenido (top 10 keywords)
            texto = " ".join([p["texto"] for p in pts[:50]]).lower()
            palabras = [w.strip(".,;:!?()[]\"'¿¡-") for w in texto.split()]
            palabras_filtradas = [
                w for w in palabras 
                if len(w) > 4 and w not in STOPWORDS_ES and not w.isdigit()
            ]
            # Tomar solo las 10 más frecuentes
            from collections import Counter
            top_10 = [w for w, _ in Counter(palabras_filtradas).most_common(10)]
            keywords_por_fuente[fuente] = set(top_10)
    
    # Calcular overlap entre todas las parejas de fuentes
    fuentes = list(keywords_por_fuente.keys())
    if len(fuentes) < 2:
        return 1.0
    
    overlaps = []
    for i in range(len(fuentes)):
        for j in range(i + 1, len(fuentes)):
            kw1 = keywords_por_fuente.get(fuentes[i], set())
            kw2 = keywords_por_fuente.get(fuentes[j], set())
            
            if not kw1 or not kw2:
                overlaps.append(0.0)
                continue
            
            # Intersección / Unión (Jaccard similarity)
            interseccion = kw1 & kw2
            union = kw1 | kw2
            
            if union:
                overlaps.append(len(interseccion) / len(union))
            else:
                overlaps.append(0.0)
    
    # Retornar promedio de overlaps
    return sum(overlaps) / len(overlaps) if overlaps else 0.0


def detectar_angulo(puntos: list, num_fuentes: int) -> str:
    """Detecta el ángulo más apropiado basado en el contenido y relación temática."""
    # PASO 1: Verificar si las fuentes son del mismo tema
    overlap = calcular_overlap_tematico(puntos)
    
    # Si las fuentes son de temas muy diferentes, NO es comparativa
    if overlap < 0.3:
        return "sintesis" if num_fuentes >= 3 else "explicacion_simple"
    
    # PASO 2: Lógica de detección de ángulo (solo si hay overlap significativo)
    texto_total = " ".join([p["texto"] for p in puntos]).lower()
    
    # Detectar comparaciones
    comparaciones = sum(1 for w in ["mejor", "peor", "diferente", "igual", "vs", "comparar"] if w in texto_total)
    
    # Detectar mitos/datos
    mitos = sum(1 for w in ["creer", "realidad", "mito", "verdad", "falso", "error"] if w in texto_total)
    
    # Solo permitir "comparativa" si hay overlap significativo
    if comparaciones >= 2 and overlap > 0.5:
        return "comparativa"
    elif mitos >= 2:
        return "mito_vs_dato"
    elif num_fuentes >= 3:
        return "sintesis"
    else:
        return "explicacion_simple"


def _extraer_keywords(puntos: list) -> list:
    """Extrae keywords significativas usando títulos + contenido."""
    # Paso 1: Extraer keywords de títulos de cada fuente (PRIORIDAD MÁXIMA)
    titulos_por_fuente = defaultdict(str)
    for p in puntos:
        fuente = p.get("fuente", "unknown")
        titulo = p.get("titulo_fuente", "")
        if titulo and not titulos_por_fuente[fuente]:
            titulos_por_fuente[fuente] = titulo.lower()
    
    # Paso 2: Extraer palabras significativas de títulos (filtrando genéricas)
    keywords_titulos = []
    for fuente, titulo in titulos_por_fuente.items():
        palabras = [w.strip(".,;:!?()[]\"'¿¡-") for w in titulo.split()]
        # Filtrar stopwords, palabras muy cortas, y genéricas de títulos
        palabras_sig = [w for w in palabras 
                       if len(w) > 3 
                       and w not in STOPWORDS_ES 
                       and w not in TITULO_GENERICAS
                       and not w.isdigit()]
        keywords_titulos.extend(palabras_sig[:3])  # Top 3 por título
    
    # Paso 3: Si hay keywords de títulos, usar esas (son las más relevantes)
    if keywords_titulos:
        # Contar frecuencia en títulos
        frecuentes = Counter(keywords_titulos).most_common(5)
        return [p[0] for p in frecuentes]
    
    # Paso 4: Fallback — extraer bigramas del contenido
    bigramas_por_fuente = defaultdict(lambda: defaultdict(int))
    
    for p in puntos:
        texto = limpiar_texto(p["texto"]).lower()
        fuente = p.get("fuente", "unknown")
        
        palabras = [w.strip(".,;:!?()[]\"'¿¡") for w in texto.split()]
        palabras = [w for w in palabras if len(w) > 2 and w not in STOPWORDS_ES and not w.isdigit()]
        
        for i in range(len(palabras) - 1):
            bigram = f"{palabras[i]} {palabras[i+1]}"
            bigramas_por_fuente[fuente][bigram] += 1
    
    conteo_global = defaultdict(int)
    fuentes_por_bigrama = defaultdict(set)
    
    for fuente, bigramas in bigramas_por_fuente.items():
        for bigram, freq in bigramas.items():
            conteo_global[bigram] += freq
            fuentes_por_bigrama[bigram].add(fuente)
    
    puntuaciones = {}
    for bigram in conteo_global:
        num_fuentes = len(fuentes_por_bigrama[bigram])
        puntuaciones[bigram] = conteo_global[bigram] * (num_fuentes ** 2)
    
    FRASES_GENERICAS = {
        "puede ser", "esto es", "hay que", "de hecho", "en este", "porque es",
        "para que", "como un", "como una", "que se", "que es", "en el", "en la",
        "del tema", "sobre el", "sobre la", "con el", "con la", "por el", "por la"
    }
    
    candidatas = []
    for bigram, score in sorted(puntuaciones.items(), key=lambda x: -x[1]):
        if bigram not in FRASES_GENERICAS:
            candidatas.append((bigram, score))
    
    return [b[0] for b in candidatas[:5]]


def generar_titulos(puntos: list, angulo: str) -> list:
    """Genera 5 opciones de título con fórmulas de copywriting profesional."""
    keywords = _extraer_keywords(puntos)
    k1 = keywords[0].title() if keywords else "Este Tema"
    k2 = keywords[1].title() if len(keywords) > 1 else "la Alternativa"
    
    if angulo == "comparativa":
        return [
            f"{k1} vs {k2}: La verdad que nadie te cuenta",
            f"Probé {k1} y {k2}: Los resultados me sorprendieron",
            f"¿{k1} o {k2}? Por qué la mayoría se equivoca",
            f"Dejé de usar {k2} por esto (Análisis de {k1})",
            f"{k1} vs {k2}: Guía definitiva sin relleno"
        ]
    elif angulo == "mito_vs_dato":
        return [
            f"Lo que te mintieron sobre {k1}",
            f"{k1}: 3 verdades incómodas que debes saber",
            f"Por qué todo lo que crees de {k1} está mal",
            f"El gran mito de {k1} (y la realidad)",
            f"Dejé de creer en {k1} cuando descubrí esto"
        ]
    elif angulo == "sintesis":
        return [
            f"{k1}: La guía definitiva en 2026",
            f"Todo lo que necesitas saber de {k1} (sin relleno)",
            f"Resumen de {k1}: Lo esencial en 5 minutos",
            f"La verdad completa sobre {k1} (con fuentes)",
            f"{k1} explicado para que por fin lo entiendas"
        ]
    else:  # explicacion_simple
        return [
            f"{k1} explicado en 5 minutos (sin vueltas)",
            f"¿Qué es {k1}? Guía para principiantes",
            f"Entiende {k1} de una vez por todas",
            f"{k1}: Lo básico que nadie te explica bien",
            f"Domina {k1} con esta guía simple"
        ]


def generar_miniatura(titulo_elegido: str, angulo: str, keywords: list) -> dict:
    """Genera sugerencia de miniatura profesional (Regla de los 3 Elementos)."""
    paletas = {
        "comparativa": {"principal": "#FF2222", "secundario": "#2222FF", "texto": "#FFFFFF"},
        "mito_vs_dato": {"principal": "#FFD700", "secundario": "#111111", "texto": "#FFFFFF"},
        "sintesis": {"principal": "#0066CC", "secundario": "#FFFFFF", "texto": "#000000"},
        "explicacion_simple": {"principal": "#00CC66", "secundario": "#111111", "texto": "#FFFFFF"}
    }
    
    # keywords ahora son frases (bigramas), tomar las primeras 2
    phrases = [k.upper() for k in keywords[:2]]
    
    if angulo == "comparativa" and len(phrases) >= 2:
        # Tomar primera palabra de cada frase para comparación
        w1 = phrases[0].split()[0] if phrases[0] else "TEMA"
        w2 = phrases[1].split()[0] if phrases[1] else "ALTERNATIVA"
        overlay = f"{w1} VS {w2}"
    elif angulo == "mito_vs_dato":
        overlay = f"¿ES VERDAD?" if not phrases else f"NO ES {phrases[0].split()[0]}"
    else:
        # Para otros ángulos, usar primera frase completa (máx 20 chars)
        overlay = phrases[0] if phrases else "GUÍA TOTAL"
        overlay = overlay[:20]
    
    return {
        "visual": "Rostro con expresión de emoción/sorpresa + objeto/tema central en primer plano",
        "texto_overlay": overlay[:25],  # Max 25 chars para legibilidad
        "paleta": paletas.get(angulo, paletas["explicacion_simple"]),
        "estilo": "Alto contraste, texto grande y legible, fondo desenfocado o sólido"
    }


def generar_gancho(puntos: list, angulo: str) -> str:
    """Genera gancho de apertura (0-15s) basado en el contenido más impactante."""
    # PASO 1: Filtrar puntos basura ANTES de buscar impacto
    puntos_limpios = []
    for p in puntos:
        texto = limpiar_texto(p["texto"])
        texto_lower = texto.lower().strip()
        
        # Descartar si muy corto, es saludo, termina en preposición, o tiene pocas palabras reales
        if len(texto_lower) < 40:
            continue
        if texto_lower.startswith(("hola", "buenos", "bienvenidos", "gracias", "piho", "sean")):
            continue
        if re.search(r'\s(en|con|por|para|de|del|la|el|los|las)\s*\.?\s*$', texto_lower):
            continue
        
        palabras_reales = [w for w in texto_lower.split() 
                          if w not in STOPWORDS_ES and len(w) > 2]
        if len(palabras_reales) < 3:
            continue
            
        puntos_limpios.append(texto)
    
    # PASO 2: Buscar dato impactante SOLO en puntos limpios
    for texto in puntos_limpios:
        texto_lower = texto.lower()
        tiene_numero = any(c.isdigit() for c in texto)
        tiene_superlativo = any(w in texto_lower for w in [
            "mejor", "peor", "nunca", "siempre", "increíble", 
            "sorprendente", "único", "primero", "último", "mayor"
        ])
        
        if (tiene_numero or tiene_superlativo) and len(texto) >= 50:
            # Cortar en primer punto para frase completa
            if "." in texto:
                frase = texto[:texto.index(".") + 1]
            else:
                if "," in texto:
                    frase = texto[:texto.index(",")] + "."
                else:
                    frase = texto[:120].rsplit(" ", 1)[0] + "."
            
            # Validación final: que no termine en preposición
            frase_clean = frase.strip()
            if re.search(r'\s(en|con|por|para|de)\s*\.?\s*$', frase_clean.lower()):
                continue
            
            return (f"¿Sabías que {frase_clean.lower()} "
                    f"En este video vas a ver exactamente cómo funciona, "
                    f"pero hay un detalle al final que cambia todo. Quedate.")
    
    # PASO 3: Fallback — primera idea coherente
    if puntos_limpios:
        texto = puntos_limpios[0]
        if "." in texto:
            frase = texto[:texto.index(".") + 1]
        else:
            frase = texto[:120].rsplit(" ", 1)[0] + "."
        return (f"La mayoría piensa que entiende este tema, pero la realidad es muy diferente. "
                f"{frase} "
                f"En los próximos minutos te muestro la evidencia real.")
    
    # PASO 4: Fallback genérico
    keywords = _extraer_keywords(puntos)
    k = keywords[0] if keywords else "este tema"
    return (f"¿Alguna vez te preguntaste qué hay realmente detrás de {k}? "
            f"La mayoría cree que sabe, pero la evidencia dice otra cosa. "
            f"En los próximos minutos te muestro lo que descubrí.")


def generar_guion(datos_fuentes: list, angulo: str = None,
                  usar_llm: bool = False, modelo: str = None) -> str:
    """Genera el borrador del guión"""
    todos_puntos = [p for fd in datos_fuentes for p in fd["puntos"]]
    
    if not angulo:
        angulo = detectar_angulo(todos_puntos, len(datos_fuentes))
    
    # Intentar LLM si está habilitado
    bloques_llm = None
    contradicciones_llm = []
    modelo_activo = None
    
    if usar_llm and LLM_DISPONIBLE:
        try:
            print("  Inicializando LLM...")
            modelo_activo = inicializar_llm(modelo)
            print(f"  Modelo activo: {modelo_activo}")
            
            titulos = generar_titulos_con_llm(todos_puntos, angulo, modelo_activo)
            gancho = generar_gancho_con_llm(todos_puntos, angulo, modelo_activo)
            bloques_llm = sintetizar_bloques_con_llm(todos_puntos, modelo_activo)
            contradicciones_llm = detectar_contradicciones_con_llm(todos_puntos, modelo_activo)
            print("  ✅ LLM: títulos, gancho, bloques y contradicciones generados")
        except LLMError as e:
            print(f"  ⚠️  LLM falló ({e}), usando reglas hardcoded")
            usar_llm = False
        except Exception as e:
            print(f"  ⚠️  Error inesperado con LLM ({e}), usando reglas hardcoded")
            usar_llm = False
    
    if not usar_llm or not modelo_activo:
        # Reglas hardcoded (comportamiento actual)
        titulos = generar_titulos(todos_puntos, angulo)
        gancho = generar_gancho(todos_puntos, angulo)
    
    keywords = _extraer_keywords(todos_puntos)
    miniatura = generar_miniatura(titulos[0], angulo, keywords)
    
    lineas = []
    lineas.append("# TÍTULOS SUGERIDOS (elegí uno o mezclá)")
    lineas.append("")
    for i, t in enumerate(titulos, 1):
        lineas.append(f"{i}. {t}")
    lineas.append("")
    lineas.append("**BORRADOR — editar antes de publicar**")
    lineas.append("")
    lineas.append(f"**Ángulo elegido:** {angulo}")
    lineas.append(f"**Fuentes:** {len(datos_fuentes)}")
    lineas.append(f"**Fecha borrador:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lineas.append("")
    lineas.append("---")
    lineas.append("")
    
    # Miniatura
    lineas.append("## MINIATURA")
    lineas.append("")
    lineas.append(f"- **Visual:** {miniatura['visual']}")
    lineas.append(f"- **Texto overlay:** {miniatura['texto_overlay']}")
    lineas.append(f"- **Paleta:** principal={miniatura['paleta']['principal']}, "
                  f"secundario={miniatura['paleta']['secundario']}, "
                  f"textos={miniatura['paleta']['texto']}")
    lineas.append(f"- **Estilo:** {miniatura['estilo']}")
    lineas.append("")
    
    # Gancho (0-15s)
    lineas.append("## GANCHO (0-15s)")
    lineas.append("")
    lineas.append(gancho)
    lineas.append("")
    
    # Desarrollo
    lineas.append("## DESARROLLO")
    lineas.append("")
    
    # Crear bloques de contenido
    if bloques_llm:
        lineas.append("## DESARROLLO (generado con LLM)")
        lineas.append("")
        for i, bloque in enumerate(bloques_llm, 1):
            lineas.append(f"### Bloque {i}")
            lineas.append("")
            lineas.append(bloque["texto"])
            lineas.append(f"[Fuentes: {', '.join(bloque['fuentes'])}]")
            lineas.append("")
    else:
        lineas.append("## DESARROLLO")
        lineas.append("")
        bloque_num = 1
        for i, punto in enumerate(todos_puntos[:10]):
            lineas.append(f"### Bloque {bloque_num}")
            lineas.append("")
            lineas.append(f"[Reescribir con voz propia - NO copiar texto original]")
            lineas.append(f"[Fuente: {punto['fuente']}]")
            lineas.append(f"[Idea original del contenido: {punto['texto'][:100]}...]")
            lineas.append("")
            bloque_num += 1
    
    # Contradicciones
    lineas.append("## CONTRADICCIONES / PUNTOS DE TENSIÓN")
    lineas.append("")
    if contradicciones_llm:
        for c in contradicciones_llm:
            lineas.append(f"- {c['descripcion']}")
        lineas.append("")
    else:
        lineas.append("[Aquí van los puntos donde las fuentes NO coinciden]")
        lineas.append("[Esto es ORO para retención - el espectador quiere resolver la duda]")
        lineas.append("")
    
    # Cierre
    lineas.append("## CIERRE + CTA")
    lineas.append("")
    lineas.append("[Resumen en 1-2 oraciones]")
    lineas.append("[Call to action: suscribirse, comentar, etc.]")
    lineas.append("")
    lineas.append("---")
    lineas.append("")
    
    # Fuentes
    lineas.append("## FUENTES USADAS")
    lineas.append("")
    for fd in datos_fuentes:
        lineas.append(f"- `{fd['hash'][:8]}` — {fd.get('titulo', 'Sin título')}")
        lineas.append(f"  - URL: {fd.get('url', 'N/A')}")
    lineas.append("")
    
    # Avisos
    lineas.append("## AVISOS")
    lineas.append("")
    lineas.append("- [ ] Revisar que NINGUNA frase sea copia textual")
    lineas.append("- [ ] Verificar afirmaciones con ≥2 fuentes")
    lineas.append("- [ ] Agregar datos que NINGUNA fuente respalda (inventados)")
    lineas.append("- [ ] Editar TÍTULO definitivo")
    lineas.append("- [ ] Grabar NARRACIÓN")
    lineas.append("")
    lineas.append("---")
    lineas.append("")
    lineas.append("**FIN DEL BORRADOR**")
    lineas.append("")
    lineas.append("*Este guión es un BORRADOR. La decisión final es humana.*")
    
    return "\n".join(lineas)


def main():
    utf8_stdout()
    
    parser = argparse.ArgumentParser(
        description="Sintetizador de Guiones — Genera borradores de video desde múltiples fuentes"
    )
    parser.add_argument("archivos", nargs="+", help="Archivos JSON de entrada (mínimo 2)")
    parser.add_argument("--usar-llm", action="store_true",
                        help="Usar LLM local vía Ollama (requiere Ollama instalado)")
    parser.add_argument("--modelo", type=str, default=None,
                        help="Nombre del modelo LLM (ej: qwen2.5:14b)")
    args = parser.parse_args()
    
    archivos = args.archivos
    usar_llm = args.usar_llm
    modelo = args.modelo
    
    if usar_llm and not LLM_DISPONIBLE:
        print("⚠️  Módulo LLM no disponible. Instalar Ollama: https://ollama.com")
        print("   Continuando con reglas hardcoded...")
        usar_llm = False
    
    datos_fuentes = []
    
    print(f"Cargando {len(archivos)} fuentes...")
    for ruta_str in archivos:
        ruta = Path(ruta_str)
        if not ruta.exists():
            print(f"  ERROR: {ruta} no existe")
            continue
        
        try:
            reg = cargar_json(ruta)
            puntos = extraer_puntos_clave(reg)
            datos_fuentes.append({
                "hash": reg.get("url_hash", ruta.stem),
                "titulo": reg.get("titulo", "Sin título"),
                "url": reg.get("source_url", "N/A"),
                "puntos": puntos,
                "metodo": reg.get("metodo", "desconocido"),
                "segmentos": len(reg.get("segmentos", []))
            })
            print(f"  OK: {reg.get('titulo', ruta.name)} ({len(puntos)} puntos)")
        except Exception as e:
            print(f"  ERROR cargando {ruta}: {e}")
    
    if len(datos_fuentes) < 2:
        print("\nERROR: Se necesitan mínimo 2 fuentes válidas")
        sys.exit(1)
    
    # Detectar ángulo
    print(f"\nFuentes válidas: {len(datos_fuentes)}")
    todos_puntos = [p for fd in datos_fuentes for p in fd["puntos"]]
    angulo = detectar_angulo(todos_puntos, len(datos_fuentes))
    print(f"Ángulo detectado: {angulo}")
    
    # Generar guión
    print("\nGenerando borrador de guión...")
    guion = generar_guion(datos_fuentes, angulo, usar_llm=usar_llm, modelo=modelo)
    
    # Guardar
    GUIONES_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    nombre = f"guion_{angulo}_{timestamp}.md"
    salida = GUIONES_DIR / nombre
    
    with open(salida, 'w', encoding='utf-8') as f:
        f.write(guion)
    
    print(f"\nBorrador generado: {salida}")
    print(f"\nRESUMEN:")
    print(f"  Fuentes: {len(datos_fuentes)}")
    print(f"  Puntos totales: {len(todos_puntos)}")
    print(f"  Ángulo: {angulo}")
    print(f"  Archivo: {salida.name}")
    print(f"\n⚠️  RECORDATORIO: Este es un BORRADOR.")
    print(f"    Editar antes de publicar. La IA propone, la persona dirige.")


if __name__ == "__main__":
    main()
