from __future__ import annotations

import hashlib
import logging
import os
import random
import subprocess
from typing import Any

from adapters.base import (
    AdapterRenderError,
    AdapterValidationError,
    NarrationInput,
    RenderConfig,
    RenderResult,
    RunContext,
    Storyboard,
    ValidationReport,
    VideoAdapter,
)
from adapters.ffmpeg_tools import (
    probe_video,
    quality_gate,
    resolve_ffmpeg,
    resolve_ffprobe,
)
from adapters.image_generator import ImageGenerator, SDImageGenerator
from adapters.scene_renderer import SceneRenderer

logger = logging.getLogger(__name__)

# Transiciones xfade nativas de ffmpeg (verificado en 8.1.2: 58 disponibles,
# indices -1..57). NO hace falta ningun repo externo. Se rotan por escena.
DEFAULT_TRANSITIONS = [
    "fade",
    "dissolve",
    "slideleft",
    "circleopen",
    "smoothright",
    "wipeup",
]


class SDImageAdapter(VideoAdapter):
    def __init__(
        self,
        image_generator: ImageGenerator | None = None,
        ffmpeg_path: str | None = None,
        ffprobe_path: str | None = None,
        transition_duration: float = 0.75,
        transitions: list[str] | None = None,
    ):
        self._image_generator = image_generator or SDImageGenerator()
        self._ffmpeg_path = ffmpeg_path or self._find_local_tool("ffmpeg.exe")
        self._ffprobe_path = ffprobe_path or self._find_local_tool("ffprobe.exe")
        # 0 desactiva las transiciones y vuelve al concat con cortes secos.
        self._transition_duration = max(0.0, float(transition_duration))
        self._transitions = transitions or DEFAULT_TRANSITIONS

    # ── properties ────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "sd-image-adapter"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def deterministic(self) -> bool:
        return False

    # ── render ────────────────────────────────────────────────────────

    def render(
        self,
        storyboard: Storyboard,
        narration: NarrationInput,
        config: RenderConfig,
        context: RunContext,
    ) -> RenderResult:
        import time

        t0 = time.time()

        output_dir = os.path.abspath(context.output_dir)
        log_dir = os.path.abspath(context.log_dir)
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(log_dir, exist_ok=True)

        resolution = _parse_resolution(config.resolution)

        try:
            self.validate_inputs(storyboard, narration, config)
            self._validate_engine_available()

            font_path = _find_default_font()
            renderer = SceneRenderer(resolution=resolution, font_path=font_path)

            scene_images = self._image_generator.generate(storyboard, output_dir)

            clip_paths: list[str] = []
            clip_durations: list[float] = []

            n_scenes = len(storyboard.scenes)
            # Con una sola escena no hay nada que cruzar.
            td = self._transition_duration if n_scenes > 1 else 0.0

            for idx, scene in enumerate(storyboard.scenes):
                image_path = scene_images.get(scene.id)
                if not image_path:
                    raise AdapterRenderError(
                        f"No image generated for scene {scene.id}"
                    )

                duration = scene.end_seconds - scene.start_seconds
                # xfade SOLAPA `td` segundos con la escena siguiente. Si no se
                # alarga cada clip (menos el ultimo), el video total se acorta
                # (n-1)*td y la narracion queda corrida. Se paga el solapamiento
                # con padding para que la duracion final no se mueva.
                render_duration = duration + td if idx < n_scenes - 1 else duration

                # Si el proveedor devolvio un CLIP DE VIDEO en vez de una foto,
                # no pasa por render_scene ni por Ken Burns: se escala, se
                # recorta y se ajusta a la duracion de la escena.
                # Medido con el quality gate: fotos con zoom dan movimiento
                # 2.62 (umbral 5.0); el metraje real da 14.37.
                if str(image_path).lower().endswith((".mp4", ".mov", ".webm", ".mkv")):
                    clip_path = os.path.join(output_dir, f"{scene.id}_clip.mp4")
                    vr = self._preparar_clip_video(
                        source_video=image_path,
                        output_path=clip_path,
                        duration=render_duration,
                        fps=config.fps,
                        resolution=config.resolution,
                        indice=idx,
                    )
                    if not vr["success"]:
                        return RenderResult(
                            status="failed",
                            error={
                                "message": (
                                    f"Fallo al preparar el clip de {scene.id}: "
                                    f"{vr.get('error', 'desconocido')}"
                                ),
                                "exception_type": "AdapterRenderError",
                            },
                            engine_info={"name": self.name, "version": self.version},
                            duration_seconds=time.time() - t0,
                        )
                    clip_paths.append(clip_path)
                    clip_durations.append(duration)
                    continue

                composited_path = os.path.join(
                    output_dir, f"{scene.id}_composited.png"
                )

                zoom_end = random.uniform(1.03, 1.08)
                pan_x = random.uniform(-0.05, 0.05)
                pan_y = random.uniform(-0.05, 0.05)

                scene.visual["components"] = [
                    {
                        "type": "image",
                        "source": image_path,
                        "ken_burns": {
                            "zoom_start": 1.0,
                            "zoom_end": zoom_end,
                            "pan_x": pan_x,
                            "pan_y": pan_y,
                        },
                    }
                ]

                renderer.render_scene(scene, composited_path)

                clip_path = os.path.join(output_dir, f"{scene.id}_clip.mp4")
                kb_result = self._apply_ken_burns(
                    source_image=composited_path,
                    output_path=clip_path,
                    duration=render_duration,
                    fps=config.fps,
                    resolution=config.resolution,
                    ffmpeg_path=self._ffmpeg_path,
                    zoom_start=1.0,
                    zoom_end=zoom_end,
                    pan_x=pan_x,
                    pan_y=pan_y,
                )

                if not kb_result["success"]:
                    return RenderResult(
                        status="failed",
                        error={
                            "message": (
                                f"Ken Burns render failed for scene {scene.id}: "
                                f"{kb_result.get('error', 'unknown')}"
                            ),
                            "exception_type": "AdapterRenderError",
                        },
                        engine_info={"name": self.name, "version": self.version},
                        duration_seconds=time.time() - t0,
                    )

                clip_paths.append(clip_path)
                clip_durations.append(duration)

            output_path = os.path.join(output_dir, config.output_filename)

            comp_result = None
            if td > 0 and len(clip_paths) > 1:
                comp_result = self._concat_clips_xfade(
                    clip_paths=clip_paths,
                    clip_durations=clip_durations,
                    narration_path=narration.audio_path,
                    output_path=output_path,
                    log_dir=log_dir,
                    timeout=config.max_duration_seconds,
                    transition_duration=td,
                    subtitles_path=config.engine_specific.get("subtitles_path"),
                )
                if not comp_result["success"]:
                    # Degradar a cortes secos antes que perder el video entero.
                    logger.warning(
                        "xfade fallo (%s); cayendo a concat con cortes secos",
                        comp_result.get("error", "unknown"),
                    )
                    comp_result = None

            if comp_result is None:
                comp_result = self._concat_clips(
                    clip_paths=clip_paths,
                    narration_path=narration.audio_path,
                    output_path=output_path,
                    log_dir=log_dir,
                    timeout=config.max_duration_seconds,
                )

            if not comp_result["success"]:
                return RenderResult(
                    status="failed",
                    error={
                        "message": comp_result.get("error", "clip composition failed"),
                        "exception_type": "AdapterRenderError",
                        "details": comp_result.get("stderr", ""),
                    },
                    engine_info={"name": self.name, "version": self.version},
                    duration_seconds=time.time() - t0,
                )

            video_path = comp_result["output_path"]
            video_hash = _sha256(video_path)
            probe = probe_video(video_path, self._ffprobe_path)

            if "error" in probe:
                return RenderResult(
                    status="failed",
                    error={
                        "message": f"ffprobe failed: {probe['error']}",
                        "exception_type": "AdapterRenderError",
                    },
                    engine_info={"name": self.name, "version": self.version},
                    duration_seconds=time.time() - t0,
                )

            qg = quality_gate(
                probe,
                expected_resolution=resolution,
                expected_fps=config.fps,
                expected_duration_seconds=storyboard.metadata.get(
                    "total_duration_seconds", 0
                ),
            )

            validation = ValidationReport(
                codec_check=qg["codec_check"],
                resolution_check=qg["resolution_check"],
                fps_check=qg["fps_check"],
                duration_check=qg["duration_check"],
                audio_check={
                    "pass": qg["audio_check"]["pass"],
                    "channels": qg["audio_check"]["channels"],
                    "sample_rate": qg["audio_check"]["sample_rate"],
                    "audio_codec": qg["audio_codec_check"]["actual"],
                },
                file_integrity=qg["file_integrity"],
                min_frame_check={"pass": True, "message": "No frame integrity check"},
            )

            result = RenderResult(
                status="success",
                video_path=video_path,
                video_size_bytes=probe.get("size_bytes", 0),
                video_duration_seconds=probe.get("duration_seconds", 0.0),
                video_hash=video_hash,
                actual_resolution=qg["resolution_check"]["actual"],
                actual_fps=probe.get("fps", 0.0),
                validation=validation,
                engine_info={"name": self.name, "version": self.version},
                duration_seconds=time.time() - t0,
            )

            self.validate_result(result)
            return result

        except (AdapterValidationError, AdapterRenderError, Exception) as e:
            return RenderResult(
                status="failed",
                error={
                    "message": str(e),
                    "exception_type": type(e).__name__,
                },
                engine_info={"name": self.name, "version": self.version},
                duration_seconds=time.time() - t0,
            )

    # ── Ken Burns ─────────────────────────────────────────────────────

    def _apply_ken_burns(
        self,
        source_image: str,
        output_path: str,
        duration: float,
        fps: int = 24,
        resolution: str = "1920x1080",
        ffmpeg_path: str | None = None,
        zoom_start: float = 1.0,
        zoom_end: float = 1.05,
        pan_x: float = 0.0,
        pan_y: float = 0.0,
    ) -> dict[str, Any]:
        try:
            ffmpeg = resolve_ffmpeg(ffmpeg_path)
        except RuntimeError as e:
            logger.warning("ffmpeg unavailable for Ken Burns: %s", e)
            return self._static_fallback(
                source_image, output_path, duration, fps, ffmpeg_path
            )

        res = _parse_resolution(resolution)
        n_frames = max(int(duration * fps), 1)
        zoom_range = zoom_end - zoom_start
        zoom_per_frame = zoom_range / n_frames if n_frames > 0 else 0

        z_expr = f"min({zoom_start}+{zoom_per_frame:.8f}*on,{zoom_end:.4f})"
        x_expr = (
            f"iw/2-(iw/zoom/2)"
            f"+{pan_x:.4f}*iw*(1-(on-1)/{n_frames - 1 if n_frames > 1 else 1})"
        )
        y_expr = (
            f"ih/2-(ih/zoom/2)"
            f"+{pan_y:.4f}*ih*(1-(on-1)/{n_frames - 1 if n_frames > 1 else 1})"
        )

        filter_str = (
            f"zoompan=z='{z_expr}':"
            f"x='{x_expr}':"
            f"y='{y_expr}':"
            f"d={n_frames}:"
            f"s={res[0]}x{res[1]}:"
            f"fps={fps}"
        )

        cmd = [
            ffmpeg,
            "-y",
            "-loop",
            "1",
            "-i",
            source_image,
            "-vf",
            filter_str,
            # -loop 1 produce un stream infinito desde la imagen. Sin acotar la
            # salida, zoompan genera frames indefinidamente y ffmpeg nunca
            # termina: el timeout de 300 s era lo unico que lo detenia, y el
            # fallback estatico se disparaba SIEMPRE. Medido: 300 s -> 4 s.
            "-frames:v",
            str(n_frames),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            output_path,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            logger.warning("zoompan timed out for %s, falling back", source_image)
            return self._static_fallback(
                source_image, output_path, duration, fps, ffmpeg_path
            )

        if result.returncode != 0 or not os.path.isfile(output_path):
            logger.warning(
                "zoompan filter failed for %s, falling back to static copy: %s",
                source_image,
                result.stderr[-500:],
            )
            return self._static_fallback(
                source_image, output_path, duration, fps, ffmpeg_path
            )

        return {"output_path": output_path, "duration": duration, "success": True}

    def _static_fallback(
        self,
        source_image: str,
        output_path: str,
        duration: float,
        fps: int,
        ffmpeg_path: str | None,
    ) -> dict[str, Any]:
        try:
            ffmpeg = resolve_ffmpeg(ffmpeg_path)
        except RuntimeError as e:
            return {"success": False, "error": str(e)}

        cmd = [
            ffmpeg,
            "-y",
            "-loop",
            "1",
            "-i",
            source_image,
            "-c:v",
            "libx264",
            "-t",
            str(duration),
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
            output_path,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "ffmpeg timed out"}

        if result.returncode != 0:
            return {"success": False, "error": result.stderr[-500:]}

        return {"output_path": output_path, "duration": duration, "success": True}

    # ── concat helper ─────────────────────────────────────────────────

    def _concat_clips(
        self,
        clip_paths: list[str],
        narration_path: str,
        output_path: str,
        log_dir: str,
        timeout: int,
    ) -> dict[str, Any]:
        ffmpeg = resolve_ffmpeg(self._ffmpeg_path)

        concat_file = os.path.join(log_dir, "concat.txt")
        os.makedirs(os.path.dirname(concat_file), exist_ok=True)

        with open(concat_file, "w") as f:
            for path in clip_paths:
                escaped = path.replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")

        cmd = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_file,
            "-i",
            narration_path,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            output_path,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": f"ffmpeg timed out after {timeout}s",
            }

        if result.returncode != 0:
            if log_dir:
                with open(os.path.join(log_dir, "ffmpeg_stderr.log"), "w") as f:
                    f.write(result.stderr)
            return {
                "success": False,
                "error": f"ffmpeg exited with code {result.returncode}",
                "stderr": result.stderr[-2000:] if result.stderr else "",
            }

        return {"success": True, "output_path": output_path}

    # ── clips de video real ───────────────────────────────────────────

    def _preparar_clip_video(
        self,
        source_video: str,
        output_path: str,
        duration: float,
        fps: int,
        resolution: str,
        indice: int = 0,
    ) -> dict[str, Any]:
        """Deja un clip de stock listo para el encadenado con xfade.

        Tres cosas, todas necesarias:

        1. ESCALAR A CUBRIR + RECORTAR CON TRAVELLING. `increase` mas `crop` es
           el equivalente en ffmpeg del cover de la imagen: llena el cuadro sin
           bandas negras. El recorte ademas se desplaza durante la escena; ver
           `_movimiento_camara`.
        2. REPETIR SI ES CORTO. Los clips de Pixabay duran 8-15s y las escenas
           pueden ser mas largas; `-stream_loop -1` con `-t` repite el metraje
           hasta cubrir la duracion en vez de dejar negro al final.
        3. NORMALIZAR fps y pix_fmt. xfade exige que todos los clips coincidan
           en formato; si no, falla el encadenado.

        Se descarta el audio del stock a proposito: la pista es la narracion.
        """
        ffmpeg = resolve_ffmpeg(self._ffmpeg_path)
        w, h = _parse_resolution(resolution)

        # El travelling con sobre-escalado SOLO sirve si el clip llega a
        # resolucion nativa. Los de banco miden 1920x1080 y ampliarlos un 20%
        # no se nota; uno generado mide 512x512 y para CUBRIR 2304x1296 manda
        # el lado corto: 512 se estira a 2304 -cuatro veces y media- y luego
        # se recorta a 1080 de alto, perdiendo el 44% vertical.
        #
        # MEDIDO el 2026-08-24 sobre el documental de Bangladesh: la imagen
        # salio granulada y descuadrada, con la composicion fuera de cuadro.
        # No era el modelo ni el prompt; era este filtro aplicado a material
        # para el que no se diseño.
        #
        # Cuando el clip no cubre el destino se mete ENTERO en el cuadro y lo
        # que falta va en negro. Se pierde el travelling y se gana la imagen
        # que el modelo compuso, que es el intercambio correcto.
        if _cubre_el_cuadro(source_video, w, h, self._ffprobe_path):
            movimiento = _movimiento_camara(w, h, duration, indice)
        else:
            logger.info("clip por debajo de %dx%d: va en banda, sin recorte",
                        w, h)
            movimiento = (
                f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"
            )

        filtro = f"{movimiento},fps={fps},format=yuv420p"
        cmd = [
            ffmpeg, "-y",
            "-stream_loop", "-1",
            "-i", source_video,
            "-t", f"{duration:.3f}",
            "-vf", filtro,
            "-an",
            "-c:v", "libx264",
            "-preset", "medium",
            output_path,
        ]

        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "ffmpeg se paso de 300s"}

        if r.returncode != 0:
            return {
                "success": False,
                "error": f"ffmpeg salio con codigo {r.returncode}",
                "stderr": (r.stderr or "")[-800:],
            }

        logger.info("clip de video listo: %s (%.2fs)",
                    os.path.basename(output_path), duration)
        return {"success": True, "output_path": output_path, "duration": duration}

    # ── concat con transiciones xfade ─────────────────────────────────

    def _concat_clips_xfade(
        self,
        clip_paths: list[str],
        clip_durations: list[float],
        narration_path: str,
        output_path: str,
        log_dir: str,
        timeout: int,
        transition_duration: float,
        subtitles_path: str | None = None,
    ) -> dict[str, Any]:
        """Encadena los clips con transiciones xfade nativas de ffmpeg.

        Cada clip (menos el ultimo) viene renderizado con `transition_duration`
        segundos de mas, asi el solapamiento del xfade se come el padding y la
        duracion final sigue coincidiendo con la narracion.

        El offset de cada xfade es la suma acumulada de las duraciones
        *pretendidas* (sin padding), que es justo donde arranca cada escena.
        """
        ffmpeg = resolve_ffmpeg(self._ffmpeg_path)
        n = len(clip_paths)
        td = transition_duration

        cmd = [ffmpeg, "-y"]
        for path in clip_paths:
            cmd += ["-i", path]
        cmd += ["-i", narration_path]

        filters: list[str] = []
        current = "[0:v]"
        offset = 0.0
        for k in range(1, n):
            offset += clip_durations[k - 1]
            name = self._transitions[(k - 1) % len(self._transitions)]
            label = "[vout]" if k == n - 1 else f"[vx{k}]"
            filters.append(
                f"{current}[{k}:v]xfade=transition={name}"
                f":duration={td:.3f}:offset={offset:.3f}{label}"
            )
            current = label

        # Los subtitulos se queman sobre el video YA encadenado: si se
        # quemaran por clip, los tiempos del SRT -que son de la linea de
        # tiempo final- no coincidirian con cada clip suelto.
        salida_video = "[vout]"
        if subtitles_path:
            from adapters.subtitulos import ESTILO_POR_DEFECTO, ruta_para_filtro

            filters.append(
                # SIN force_style cuando el archivo es .ass: ese formato
                # trae su propio estilo y force_style lo PISA. El .ass del
                # karaoke se genera con Fontsize 98 y caja negra, y salia
                # quemado a 22 con contorno simple. force_style es para
                # .srt, que no trae estilo.
                f"[vout]subtitles='{ruta_para_filtro(subtitles_path)}'"
                + (""
                   if str(subtitles_path).lower().endswith(".ass")
                   else f":force_style='{ESTILO_POR_DEFECTO}'")
                + "[vsub]"
            )
            salida_video = "[vsub]"

        cmd += [
            "-filter_complex",
            ";".join(filters),
            "-map",
            salida_video,
            "-map",
            f"{n}:a",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            output_path,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"xfade timed out after {timeout}s"}

        if result.returncode != 0:
            if log_dir:
                with open(os.path.join(log_dir, "ffmpeg_xfade_stderr.log"), "w") as f:
                    f.write(result.stderr or "")
            return {
                "success": False,
                "error": f"xfade exited with code {result.returncode}",
                "stderr": result.stderr[-2000:] if result.stderr else "",
            }

        logger.info(
            "xfade OK: %d clips, %d transiciones de %.2fs", n, n - 1, td
        )
        return {"success": True, "output_path": output_path, "transitions": n - 1}

    # ── lifecycle ─────────────────────────────────────────────────────

    def cleanup(self) -> None:
        self._image_generator.cleanup()

    # ── internal helpers ──────────────────────────────────────────────

    def _validate_engine_available(self) -> None:
        try:
            resolve_ffmpeg(self._ffmpeg_path)
        except RuntimeError as e:
            raise AdapterRenderError(str(e))
        try:
            resolve_ffprobe(self._ffprobe_path)
        except RuntimeError as e:
            raise AdapterRenderError(str(e))

    @staticmethod
    def _find_local_tool(name: str) -> str | None:
        local = os.path.join(os.path.dirname(__file__), "..", "bin", name)
        if os.path.isfile(local):
            return local
        return None


# ── module-level helpers ──────────────────────────────────────────────


# Sobre-escalado que deja margen para desplazar el encuadre. 1.20 recorre el
# 20% del cuadro a lo largo de la escena: un travelling creible.
#
# MEDIDO el 2026-08-21 sobre tres clips de stock distintos, movimiento medio:
#
#   sin movimiento   2.51      s=1.20   6.56
#   s=1.12           4.84      s=1.30   8.42
#
# 1.30 da mas, pero recorre el 30% del cuadro en cinco segundos -se ve
# nervioso- y descarta mas pixeles del original.
SOBRE_ESCALADO = 1.20

# Cuatro recorridos que se turnan por escena. La alternancia no es estetica:
# dos travellings iguales seguidos delatan que el movimiento es sintetico.
# (signo_x, signo_y); 1 = de izquierda a derecha / de arriba hacia abajo.
_RECORRIDOS = ((1, 1), (-1, -1), (1, -1), (-1, 1))


def _cubre_el_cuadro(ruta: str, w: int, h: int,
                    ffprobe_path: str | None = None) -> bool:
    """Si el clip tiene pixeles de sobra para recortarlo sin ampliarlo.

    Se compara contra el cuadro CON el sobre-escalado ya aplicado, que es lo
    que el travelling necesita de verdad. Ante la duda -ffprobe falla, el
    clip no dice su tamaño- se devuelve False: meterlo en banda se ve peor
    que un travelling, pero mucho mejor que una imagen reventada.
    """
    try:
        r = subprocess.run(
            [resolve_ffprobe(ffprobe_path), "-v", "error", "-select_streams",
             "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0",
             ruta],
            capture_output=True, text=True, timeout=60)
        cw, ch = (int(x) for x in r.stdout.strip().split(",")[:2])
    except (subprocess.SubprocessError, ValueError, OSError) as e:
        logger.warning("no se pudo medir %s (%s); va en banda",
                       os.path.basename(ruta), e)
        return False
    return cw >= int(w * SOBRE_ESCALADO) and ch >= int(h * SOBRE_ESCALADO)


def _movimiento_camara(w: int, h: int, duration: float, indice: int) -> str:
    """Cadena de filtros que agrega travelling a un clip de stock.

    Los clips de archivo llegan con el movimiento que tenga su contenido y
    nada mas: a diferencia de las fotos, no pasaban por Ken Burns. Medido con
    el quality gate, un video armado con metraje pertinente de oficina daba
    movimiento 8.27 -gente sentada trabajando, que es lo correcto pero es
    calmado-. El travelling lo compensa sin cambiar de metraje.

    Se sobre-escala y se recorta una ventana movil: `crop` acepta expresiones
    con `t`, asi que el desplazamiento es continuo y sin saltos. No se usa
    `zoompan`, que esta pensado para fotos fijas y en video da tiron.
    """
    if duration <= 0:
        return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"

    sx, sy = _RECORRIDOS[indice % len(_RECORRIDOS)]
    # avance normalizado 0->1 (o 1->0 si el recorrido va al reves)
    av_x = f"(t/{duration:.3f})" if sx > 0 else f"(1-t/{duration:.3f})"
    av_y = f"(t/{duration:.3f})" if sy > 0 else f"(1-t/{duration:.3f})"
    gw, gh = int(w * SOBRE_ESCALADO), int(h * SOBRE_ESCALADO)
    return (
        f"scale={gw}:{gh}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h}:x='(iw-ow)*{av_x}':y='(ih-oh)*{av_y}'"
    )


def _parse_resolution(resolution_str: str) -> tuple[int, int]:
    parts = resolution_str.split("x")
    if len(parts) != 2:
        raise AdapterValidationError(f"Invalid resolution format: {resolution_str}")
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        raise AdapterValidationError(f"Invalid resolution values: {resolution_str}")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_default_font() -> str | None:
    candidates = [
        "C:\\Windows\\Fonts\\arial.ttf",
        "C:\\Windows\\Fonts\\segoeui.ttf",
        "C:\\Windows\\Fonts\\calibri.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None
