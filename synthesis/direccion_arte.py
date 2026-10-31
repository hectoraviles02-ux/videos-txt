# -*- coding: utf-8 -*-
"""Una sola direccion de arte para todo el video.

**El problema que resuelve.** Un video armado con metraje de banco se ve
armado con metraje de banco: cada clip viene de un rodaje distinto, con otra
camara, otra luz y otro color. El ojo lo nota aunque no sepa nombrarlo, y es
la diferencia mas grande entre lo que producimos y un documental que
funciona.

MEDIDO el 2026-08-22 sobre el documental del Banco de Bangladesh: **dos
cortes de imagen en cinco minutos**, movimiento 5.31 y una paleta identica de
principio a fin. No compensa la falta de variedad con montaje: la sostiene
con coherencia.

**Que hace este modulo.** Define estilos completos -no adjetivos sueltos- y
los aplica a TODAS las escenas del mismo video. Un estilo trae su prefijo de
prompt, su lista de lo que hay que evitar, y las decisiones de encuadre que
lo acompanan.

**Lo que NO hace.** No mejora el metraje de banco: ahi la coherencia es
imposible porque las imagenes ya existen. Sirve cuando las imagenes se
generan, que es cuando se puede pedir que todas se parezcan.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Estilo:
    """Una direccion de arte completa.

    `prefijo` va delante de cada descripcion de escena; `evitar` va al
    negativo. Los dos importan por igual: sin negativo, el modelo mete caras
    deformes y marcas de agua que rompen la coherencia mas que cualquier
    diferencia de color.
    """

    nombre: str
    # Corto a proposito. CLIP corta el prompt en 77 tokens y lo que sobra se
    # pierde sin avisar: MEDIDO el 2026-08-22, un estilo de 30 tokens dejaba
    # 82 en total y truncaba "35mm film grain". Peor, comia la descripcion de
    # la escena en las frases largas y salia una imagen vacia. El estilo tiene
    # que caber dejando sitio a lo que hay que ver.
    prefijo: str
    evitar: str
    descripcion: str
    # Encuadre. El documental de referencia usa imagen 16:9 dentro de un
    # cuadro 9:16, con barra negra arriba y abajo. Esa barra no es un
    # descuido: es donde vive el subtitulo sin tapar nada.
    banda_negra: bool = False


# Sin rostros a proposito. Los modelos de imagen fallan en las caras mas que
# en cualquier otra cosa, y una cara mal hecha destruye la credibilidad de una
# pieza documental. El documental de referencia lo resuelve con un maniqui
# blanco sin rasgos.
#
# VERIFICADO el 2026-08-22, y es un error facil de cometer: escribir "no
# visible faces" en el prompt POSITIVO no quita caras, las ATRAE. Estos
# modelos no procesan la negacion — leen "faces" y pintan caras. Se pidio
# "primer plano de una pantalla con codigo verde" con esa frase delante y
# salio un rostro. Lo que evita algo va SIEMPRE en el negativo.
_SIN_ROSTROS = ("face, portrait, person looking at camera, deformed face, "
                "extra fingers, distorted hands, text, watermark, logo, "
                "signature, blurry, low quality, "
                # MEDIDO el 2026-08-24: los maniquies salian desnudos y con
                # forma femenina. En una pieza documental eso no se publica, y
                # el modelo no lo evita solo: hay que pedirlo.
                "nude, naked, lingerie, mannequin torso, shop window display")

ESTILOS = {
    "noir": Estilo(
        nombre="noir",
        # El maniqui NO va aqui, y esto costo una tanda entera de clips.
        #
        # MEDIDO el 2026-08-24, en dos pasos. Primero: sin maniqui, una escena
        # que decia "cybersecurity experts monitors" saco dos rostros mal
        # resueltos pese al negativo -el negativo no cancela lo que el positivo
        # nombra-. Se metio "featureless white mannequin figures" en ESTE
        # prefijo y se arreglo esa escena. Pero el prefijo va en TODAS, y
        # entonces una escena que solo pedia "screens showing financial
        # graphics" -sin una sola persona- devolvio dos maniquies desnudos y
        # ningun grafico.
        #
        # La leccion: el estilo dice COMO se ve el video entero; QUIEN aparece
        # es cosa de cada escena. El maniqui va en el prompt de las escenas que
        # traen gente, no en la direccion de arte.
        prefijo=("cinematic, dark noir, cold light, deep shadows, teal "
                 "palette, 35mm grain"),
        evitar=_SIN_ROSTROS + ", bright daylight, cheerful, saturated colors",
        descripcion="oscuro y tenso; para investigacion, fraude, crimen",
        banda_negra=True,
    ),
    "cosmico": Estilo(
        nombre="cosmico",
        prefijo=("cinematic astrophotography, deep space, blue violet "
                 "palette, high contrast, volumetric light"),
        evitar=_SIN_ROSTROS + ", people, faces, cartoon, illustration",
        descripcion="frio y vasto; para ciencia, espacio, descubrimiento",
        banda_negra=True,
    ),
    "institucional": Estilo(
        nombre="institucional",
        prefijo=("documentary photo, neutral daylight, desaturated palette, "
                 "clean composition"),
        evitar=_SIN_ROSTROS + ", dramatic lighting, neon, fantasy",
        descripcion="sobrio y real; para datos, salud, gobierno",
        banda_negra=False,
    ),
}
ESTILO_POR_DEFECTO = "institucional"

_PISTAS = {
    "noir": ("fraude", "robo", "hacker", "ataque", "crimen", "corrupcion",
             "falla", "fallo", "crisis", "investigacion", "delito", "estafa",
             "espionaje", "secreto"),
    "cosmico": ("universo", "espacio", "agujero", "galaxia", "cosmos",
                "estrella", "planeta", "telescopio", "astronomia", "cuantica",
                "fisica", "einstein"),
}


def estilo_para(tema: str) -> Estilo:
    """Elige la direccion de arte segun el tema.

    Es una heuristica declarada, no una medicion. Se puede pasar el estilo a
    mano cuando no acierte, y por eso `aplicar` recibe el estilo y no el tema.
    """
    # Coincidencia por RAIZ, no por palabra exacta: "los agujeros negros"
    # elegia el estilo equivocado porque la pista decia "agujero" y el texto
    # "agujeros". Pedir la lista con todos los plurales seria una lista que
    # nadie mantiene al dia.
    texto = " ".join(re.findall(r"[a-záéíóúñü]+", (tema or "").lower()))
    for nombre, pistas in _PISTAS.items():
        if any(p in texto for p in pistas):
            return ESTILOS[nombre]
    return ESTILOS[ESTILO_POR_DEFECTO]


def _componer(descripcion: str, estilo: Estilo) -> str:
    """La escena PRIMERO, el estilo detras.

    VERIFICADO el 2026-08-22: con el estilo delante -treinta tokens de luz,
    paleta y grano- el modelo ignoraba la escena. Se pidio "un cuarto de
    servidores con una impresora apagada" y salio gente en un pasillo. Estos
    modelos pesan mas los primeros tokens, asi que ahi va lo que hay que ver
    y despues como se ve.
    """
    d = (descripcion or "").strip().rstrip(",. ")
    estilo_limpio = estilo.prefijo.strip().rstrip(", ")
    return "%s, %s" % (d, estilo_limpio) if d else estilo_limpio


def aplicar(storyboard: dict, estilo: Estilo) -> dict:
    """Pone el mismo estilo en la descripcion visual de TODAS las escenas.

    Devuelve una copia. La descripcion original se conserva detras del
    prefijo: el estilo dice COMO se ve, la descripcion dice QUE se ve, y las
    dos hacen falta.
    """
    escenas = storyboard.get("scenes") or []
    nuevas = []
    for e in escenas:
        n = dict(e)
        visual = n.get("visual")
        if isinstance(visual, dict):
            visual = dict(visual)
            base = visual.get("description") or visual.get(
                "visual_description") or ""
            visual["description"] = _componer(str(base), estilo)
            visual["negative_prompt"] = estilo.evitar
            visual["estilo"] = estilo.nombre
            n["visual"] = visual
        else:
            base = n.get("visual_description") or ""
            n["visual_description"] = _componer(str(base), estilo)
            n["negative_prompt"] = estilo.evitar
            n["estilo"] = estilo.nombre
        nuevas.append(n)
    salida = dict(storyboard)
    salida["scenes"] = nuevas
    meta = dict(salida.get("metadata") or {})
    meta["estilo"] = estilo.nombre
    meta["banda_negra"] = estilo.banda_negra
    salida["metadata"] = meta
    logger.info("direccion de arte: %s (%s) en %d escenas",
                estilo.nombre, estilo.descripcion, len(nuevas))
    return salida


def filtro_banda_negra(ancho: int, alto: int, proporcion: float = 9 / 16
                       ) -> str:
    """Cadena ffmpeg que mete la imagen en una banda dentro del cuadro.

    La imagen ocupa el ancho completo y queda centrada; arriba y abajo queda
    negro. Ese negro es donde se lee el subtitulo sin taparle nada a la
    imagen — es la razon por la que el documental de referencia mide 37% de
    cobertura y no le pasa nada.
    """
    alto_banda = int(ancho * proporcion)
    alto_banda -= alto_banda % 2
    return ("scale=%d:%d:force_original_aspect_ratio=increase,"
            "crop=%d:%d,pad=%d:%d:0:(oh-ih)/2:black"
            % (ancho, alto_banda, ancho, alto_banda, ancho, alto))
