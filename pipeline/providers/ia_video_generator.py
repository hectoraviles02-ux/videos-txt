# -*- coding: utf-8 -*-
"""Clips generados con IA local, con una sola direccion de arte.

Es la alternativa a `StockVideoGenerator`. El banco tiene metraje real y
gratis, pero cada clip viene de otro rodaje: no hay forma de que doce escenas
se parezcan entre si. Aqui se generan, y por eso se les puede pedir que
compartan luz, paleta y encuadre.

MEDIDO el 2026-08-22 con AnimateDiff sobre la RTX 2000 Ada (8.6 GB):

    16 cuadros a 512x512      67 s      VRAM pico 4.85 GB
    movimiento del clip       8.93      (el banco filtrado daba 5.11)

Cabe holgado, pero NO junto a la voz clonada: VoxCPM2 pico en 6.4 GB y los
dos no entran a la vez. Por eso la narracion se genera antes y este
generador corre despues, nunca en paralelo.

**El costo real.** A 67 s por cada 2 s de video, un documental de minuto y
medio son unos 45 minutos de computo. El banco tarda segundos. Esta pieza no
reemplaza al banco: es la opcion para cuando la coherencia importa mas que
el tiempo.

MEDIDO de nuevo el 2026-08-27 en la misma tarjeta, mismo prompt y misma
semilla, 16 cuadros a 512x512. Se agrega sin borrar lo de arriba: son
condiciones distintas y las dos cuentan.

    DDIM  20 pasos  guia 7.5   56.4 s   contraste 52.3   saturacion 42.8%
    DDIM   6 pasos  guia 1.0   11.1 s   contraste 14.6   saturacion 18.5%
    LCM    4 pasos  guia 1.5   16.3 s   contraste 50.0   saturacion 47.9%
    LCM    6 pasos  guia 1.5   23.0 s   contraste 45.3   saturacion 51.9%
    LCM    8 pasos  guia 2.0   29.3 s   contraste 50.7   saturacion 59.7%

De ahi sale el modo rapido (`rapido=True`), y de las tres filas LCM se
eligio la de 8 pasos: 48% menos tiempo por clip. No es la mas rapida de las
tres, es la que paso la revision a ojo contra las otras cuatro. Las de 4 y
6 pasos estan medidas arriba y quedaron descartadas: la eleccion fue
visual, no aritmetica, y por eso se deja escrito que existieron. La
fila del medio es la trampa que no se tomo: bajar la guia a 1.0 ahorra la
mitad del computo porque el UNet deja de correr dos veces por paso, pero
lava la imagen un 72% de contraste. Se ve gris. El modo rapido no baja la
guia: cambia el motion module por uno destilado, que sostiene el color con
cuatro pasos.

**Por que 512x512 y no mas.** Es lo que el modelo entrega bien; subirlo
produce composiciones rotas antes que detalle. Se escala despues, y por eso
la direccion de arte pide grano de pelicula y poca profundidad de campo: el
escalado se nota menos sobre una imagen que ya es suave.
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

MODELO_BASE = "SG161222/Realistic_Vision_V5.1_noVAE"
ADAPTADOR = "guoyww/animatediff-motion-adapter-v1-5-2"

# Cuadros por clip y su ritmo. 16 a 8 fps son 2 segundos: el maximo que el
# modelo sostiene sin que la escena se deshaga.
CUADROS = 16

# Cuadro de generacion, en 16:9 NATIVO. Ambos divisibles entre 8, que es
# lo que la UNet necesita.
#
# MEDIDO el 2026-08-25 sobre tres propuestas de la misma escena: la
# medicion vieja decia que subir la resolucion rompia composiciones, y
# era cierta, pero era sobre subir el AREA. Nadie habia probado cambiar
# la RELACION dejando el area casi igual (768x432 = 331k pixeles contra
# 262k de 512x512). En 16:9 nativo la composicion aguanta, la VRAM cabe,
# y DESAPARECEN las barras negras que en 1:1 se comian medio ancho del
# cuadro final.
#
# Importa mas por lo que viene despues: al montar en 1920x1080, desde
# 512x512 hay que estirar 4.5x a lo ancho contra 2.5x a lo alto
# -deformacion, no solo perdida de detalle-, mientras desde 768x432 son
# 3.0x parejos en los dos ejes.
ANCHO = 768
ALTO = 432
FPS_FUENTE = 8

# Pasos de difusion. Con menos de 20 la imagen sale sucia; con mas, el tiempo
# crece y la mejora deja de verse.
PASOS = 20
GUIA = 7.5

# Modo rapido, OPT-IN. Nada de esto corre salvo que se pida `rapido=True`:
# produccion sigue en DDIM 20 pasos como siempre.
ADAPTADOR_LCM = "wangfuyun/AnimateLCM"
LORA_LCM = "AnimateLCM_sd15_t2v_lora.safetensors"
PASOS_LCM = 8
# 2.0 y no 1.0. Con guia > 1 el UNet SIGUE corriendo dos veces por paso, y
# los 29.3 s medidos ya lo incluyen. Bajarla a 1.0 partiria el tiempo a la
# mitad y traeria de vuelta el lavado (-72% de contraste, medido). No es una
# optimizacion pendiente: es la linea que sostiene el color.
GUIA_LCM = 2.0


class ModeloNoDisponible(RuntimeError):
    """Faltan los pesos o la GPU. Se dice; el llamador decide si cae al banco."""


class IAVideoGenerator:
    """Genera un clip por escena. Mismo contrato que StockVideoGenerator."""

    def __init__(self, fallback_generator: Any | None = None,
                 estilo: Any | None = None, pasos: int | None = None,
                 rapido: bool = False):
        # Si el modelo no carga o una escena falla, el video sale igual con
        # metraje de banco. Perder la coherencia es peor que perder el video,
        # pero mucho menos malo que no entregar nada.
        self._fallback = fallback_generator
        self._estilo = estilo
        # `rapido` elige el juego de valores; un `pasos` explicito manda
        # sobre los dos. None NO es 20: es "el default del modo que elegi".
        self._rapido = rapido
        self._pasos = pasos if pasos is not None else (
            PASOS_LCM if rapido else PASOS)
        self._guia = GUIA_LCM if rapido else GUIA
        self._pipe = None

    @property
    def name(self) -> str:
        # Lo que CORRIO, no lo que se pidio. Si el modo rapido no pudo
        # cargar, `_cargar` apaga la bandera y esto vuelve a decir animatediff:
        # el artefacto tiene que delatar con que se hizo el video.
        return "ia-animatelcm" if self._rapido else "ia-animatediff"

    def _cargar(self):
        if self._pipe is not None:
            return self._pipe
        try:
            import torch
            from diffusers import AnimateDiffPipeline, DDIMScheduler, MotionAdapter
        except ImportError as e:
            raise ModeloNoDisponible("falta diffusers o torch: %s" % e) from e
        if not torch.cuda.is_available():
            raise ModeloNoDisponible("no hay GPU; en CPU esto no termina")

        # Antes de pedir 6 GB, se barre lo que dejaron las etapas previas.
        # La transcripcion y la voz corren en ESTE mismo proceso y sus
        # pesos siguen en la tarjeta hasta que alguien los suelta. MEDIDO
        # el 2026-08-29: con la GPU al 97% este generador tardaba 19 min
        # por clip; con la tarjeta libre, 28 s. No es margen, es 40x.
        import gc
        gc.collect()
        torch.cuda.empty_cache()
        libre = torch.cuda.mem_get_info()[0] / 1024 ** 2
        logger.info("ia_video: %.0f MiB libres antes de cargar", libre)
        if libre < 5000:
            # Se avisa y se sigue: un video lento es mejor que ninguno,
            # pero quien lea el log tiene que saber por que tardo tanto.
            logger.warning(
                "ia_video: solo %.0f MiB libres. Algo mas quedo cargado en "
                "la tarjeta y esto va a ir MUCHO mas lento de lo medido.",
                libre)

        if self._rapido:
            pipe = self._cargar_rapido(torch)
            if pipe is not None:
                self._pipe = pipe
                logger.info("ia_video: modelo cargado (rapido/AnimateLCM, "
                            "%d pasos, guia %.1f)", self._pasos, self._guia)
                return pipe
            # No se degrada en silencio: se avisa Y se corrige el estado, para
            # que `name` y el log digan DDIM y nadie crea que corrio en rapido.
            logger.warning(
                "ia_video: se pidio modo rapido y NO esta disponible; se sigue "
                "en DDIM %d pasos, que tarda ~3 veces mas. Falta 'peft' o los "
                "pesos de %s.", PASOS, ADAPTADOR_LCM)
            self._rapido = False
            self._pasos = PASOS
            self._guia = GUIA

        adaptador = MotionAdapter.from_pretrained(ADAPTADOR,
                                                  torch_dtype=torch.float16)
        pipe = AnimateDiffPipeline.from_pretrained(
            MODELO_BASE, motion_adapter=adaptador, torch_dtype=torch.float16)
        pipe.scheduler = DDIMScheduler.from_pretrained(
            MODELO_BASE, subfolder="scheduler", clip_sample=False,
            timestep_spacing="linspace", beta_schedule="linear",
            steps_offset=1)
        pipe.vae.enable_slicing()
        # Descarga a CPU lo que no esta en uso. Sin esto no cabe junto con el
        # decodificador de video en 8 GB.
        pipe.enable_model_cpu_offload()
        self._pipe = pipe
        logger.info("ia_video: modelo cargado")
        return pipe

    def _cargar_rapido(self, torch):
        """AnimateLCM: motion module destilado para pocos pasos.

        Devuelve None si falta algo. No lanza: el llamador decide, y lo que
        decide es seguir en DDIM avisando, no quedarse sin video.

        No es el mismo artefacto que `lcm-lora-sdv1-5` sobre el motion module
        estandar. Ese par degrada el movimiento, porque el LoRA esta destilado
        y el modulo de movimiento no. Aqui los dos lo estan.
        """
        try:
            import peft  # noqa: F401  -- load_lora_weights lo exige
            from diffusers import (AnimateDiffPipeline, LCMScheduler,
                                   MotionAdapter)
        except ImportError as e:
            logger.warning("ia_video: modo rapido sin dependencias (%s)", e)
            return None
        try:
            adaptador = MotionAdapter.from_pretrained(
                ADAPTADOR_LCM, torch_dtype=torch.float16)
            pipe = AnimateDiffPipeline.from_pretrained(
                MODELO_BASE, motion_adapter=adaptador,
                torch_dtype=torch.float16)
            # LCMScheduler, no DDIM. Con DDIM el modelo destilado devuelve
            # ruido: no es que salga peor, es que no converge.
            pipe.scheduler = LCMScheduler.from_config(
                pipe.scheduler.config, beta_schedule="linear")
            pipe.load_lora_weights(ADAPTADOR_LCM, weight_name=LORA_LCM,
                                   adapter_name="lcm-lora")
            pipe.set_adapters(["lcm-lora"], [0.8])
            # Fusionar el LoRA en los pesos y residir en GPU. MEDIDO el
            # 2026-08-27, un clip de 16 cuadros a 4 pasos:
            #     cpu_offload             23.4 s   pico 5401 MiB
            #     to(cuda)                16.3 s   pico 6686 MiB
            #     to(cuda) + fuse_lora    13.9 s   pico 6046 MiB
            # El offload que usa la rama normal aqui cuesta 9 s por clip,
            # porque con el LoRA sin fusionar hay que mover pesos en cada
            # paso. Fusionado cabe en la tarjeta y no hace falta.
            pipe.fuse_lora()
            pipe.vae.enable_slicing()
            return pipe.to("cuda")
        except Exception as e:
            logger.warning("ia_video: AnimateLCM no cargo (%s)", e)
            return None

    def _prompt(self, scene: Any) -> tuple[str, str]:
        """Descripcion de la escena, con el estilo delante si lo hay.

        EN INGLES, y no es un detalle de estilo. MEDIDO el 2026-08-24 con la
        misma semilla, el mismo estilo y el mismo negativo, cambiando solo el
        idioma del texto:

            es  "Luces parpadeantes y monitores apagados llenan una sala de
                 computo del banco"        -> una oficina vacia, sin una sola
                                              computadora
            en  "flickering lights and dark monitors fill a bank server room,
                 rows of computers"        -> el pasillo de servidores pedido

        El modelo esta entrenado con pares imagen-texto en ingles: el espanol
        no lo entiende, lo ignora, y rellena con lo que sabe. Por eso se
        prefiere `search_keywords`, que el generador de guion YA produce en
        ingles, sobre la descripcion visual, que sale en espanol.
        """
        v = getattr(scene, "visual", None) or {}
        en_ingles = (v.get("search_keywords")
                     or getattr(scene, "search_keywords", "") or "")
        if isinstance(en_ingles, list):
            en_ingles = " ".join(str(x) for x in en_ingles)
        desc = (str(en_ingles).strip()
                or v.get("description") or v.get("visual_description")
                or getattr(scene, "title", "") or "")
        if isinstance(desc, list):
            desc = " ".join(str(x) for x in desc)
        desc = str(desc).strip()
        negativo = v.get("negative_prompt") or ""
        if self._estilo is not None:
            # El estilo va DETRAS de la escena, nunca delante. No es una
            # preferencia: synthesis/direccion_arte.py lo midio el 2026-08-22
            # y aqui se cometio el error contrario. Con los treinta tokens de
            # luz, paleta y grano por delante, el modelo se queda con ellos y
            # pierde lo que hay que ver.
            #
            # El sintoma es reconocible, y se reprodujo el 2026-08-24: se pidio
            # "expertos examinando monitores" con el estilo delante y salio una
            # figura sola en un pasillo. La misma falla que aquel dia produjo
            # "gente en un pasillo" cuando se pedia un cuarto de servidores.
            if self._estilo.prefijo not in desc:
                limpio = desc.strip().rstrip(",. ")
                prefijo = self._estilo.prefijo.strip().rstrip(", ")
                desc = "%s, %s" % (limpio, prefijo) if limpio else prefijo
            negativo = negativo or self._estilo.evitar
        return desc, negativo

    def generate(self, storyboard: Any, output_dir: str) -> dict[str, str]:
        os.makedirs(output_dir, exist_ok=True)
        escenas = list(getattr(storyboard, "scenes", []) or [])
        resultados: dict[str, str] = {}
        sin_clip: list[Any] = []

        try:
            pipe = self._cargar()
        except ModeloNoDisponible as e:
            logger.warning("ia_video: %s; todo va al respaldo", e)
            return self._respaldo(escenas, output_dir, {})

        import numpy as np
        import torch

        for i, scene in enumerate(escenas, 1):
            prompt, negativo = self._prompt(scene)
            if not prompt:
                sin_clip.append(scene)
                continue
            destino = os.path.join(output_dir, "%s_ia.mp4" % scene.id)
            try:
                salida = pipe(
                    prompt=prompt, negative_prompt=negativo or None,
                    num_frames=CUADROS, guidance_scale=self._guia,
                    # Explicito: sin estos dos el pipeline cae al default
                    # del modelo, que es 512x512 y deja barras negras.
                    width=ANCHO, height=ALTO,
                    num_inference_steps=self._pasos,
                    # Semilla derivada del indice: el mismo storyboard da el
                    # mismo video, que es la regla del proyecto.
                    generator=torch.Generator("cpu").manual_seed(1000 + i),
                )
                cuadros = salida.frames[0]
            except Exception as e:
                logger.warning("ia_video: escena %s fallo (%s)", scene.id, e)
                sin_clip.append(scene)
                continue
            if self._escribir(cuadros, destino, np):
                resultados[scene.id] = destino
                logger.info("ia_video: %s listo (%d/%d)", scene.id, i,
                            len(escenas))
            else:
                sin_clip.append(scene)

        if sin_clip:
            resultados.update(self._respaldo(sin_clip, output_dir, resultados))
        logger.info("ia_video: %d generados, %d por respaldo",
                    len(resultados) - len(sin_clip), len(sin_clip))
        return resultados

    @staticmethod
    def _escribir(cuadros, destino: str, np) -> bool:
        """Cuadros a mp4. Se usa ffmpeg y no cv2 para no depender de codecs."""
        try:
            import imageio
            imageio.mimsave(destino, [np.array(c) for c in cuadros],
                            fps=FPS_FUENTE, codec="libx264",
                            output_params=["-pix_fmt", "yuv420p"])
            return os.path.getsize(destino) > 1000
        except Exception as e:
            logger.warning("ia_video: no se pudo escribir %s (%s)",
                           os.path.basename(destino), e)
            return False

    def _respaldo(self, escenas: list, output_dir: str,
                  ya: dict) -> dict[str, str]:
        if not (escenas and self._fallback is not None):
            return {}

        class _Parcial:
            scenes = escenas

        try:
            return self._fallback.generate(_Parcial(), output_dir)
        except Exception as e:
            logger.warning("ia_video: el respaldo tambien fallo (%s)", e)
            return {}


def duracion_de(ruta: str) -> float:
    """Segundos del clip. Util para saber cuanto hay que repetirlo."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", ruta],
            capture_output=True, text=True, timeout=60)
        return float((r.stdout or "0").strip() or 0)
    except (subprocess.SubprocessError, ValueError, OSError):
        return 0.0
