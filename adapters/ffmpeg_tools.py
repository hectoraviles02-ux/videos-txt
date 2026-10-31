"""Utilidades para invocar FFmpeg y FFprobe desde el adaptador."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any


def resolve_ffmpeg(path_override: str | None = None) -> str:
    if path_override:
        if not shutil.which(path_override) and not os.path.isfile(path_override):
            raise RuntimeError(f"ffmpeg not found at configured path: {path_override}")
        return path_override
    which = shutil.which("ffmpeg")
    if which:
        return which
    raise RuntimeError(
        "ffmpeg not found in PATH. Install ffmpeg or configure ffmpeg_path."
    )


def resolve_ffprobe(path_override: str | None = None) -> str:
    if path_override:
        if not shutil.which(path_override) and not os.path.isfile(path_override):
            raise RuntimeError(
                f"ffprobe not found at configured path: {path_override}"
            )
        return path_override
    which = shutil.which("ffprobe")
    if which:
        return which
    raise RuntimeError(
        "ffprobe not found in PATH. Install ffprobe or configure ffprobe_path."
    )


def check_ffmpeg(ffmpeg_path: str | None = None) -> dict[str, Any]:
    try:
        path = resolve_ffmpeg(ffmpeg_path)
        result = subprocess.run(
            [path, "-version"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return {"available": False, "error": result.stderr.strip()}
        first_line = result.stdout.split("\n")[0] if result.stdout else "unknown"
        return {"available": True, "path": path, "version": first_line}
    except (FileNotFoundError, RuntimeError, subprocess.TimeoutExpired) as e:
        return {"available": False, "error": str(e)}


def check_ffprobe(ffprobe_path: str | None = None) -> dict[str, Any]:
    try:
        path = resolve_ffprobe(ffprobe_path)
        result = subprocess.run(
            [path, "-version"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return {"available": False, "error": result.stderr.strip()}
        first_line = result.stdout.split("\n")[0] if result.stdout else "unknown"
        return {"available": True, "path": path, "version": first_line}
    except (FileNotFoundError, RuntimeError, subprocess.TimeoutExpired) as e:
        return {"available": False, "error": str(e)}


def quality_gate(
    probe: dict[str, Any],
    expected_resolution: tuple[int, int],
    expected_fps: int,
    expected_duration_seconds: float,
    expected_video_codec: str = "h264",
    expected_audio_codec: str = "aac",
    tolerance_duration: float = 3.0,
    tolerance_fps: float = 1.0,
) -> dict[str, Any]:
    """Valida calidad del output usando resultado de ffprobe.

    Returns dict con checks individuales + all_pass.
    """
    if "error" in probe:
        return {
            "all_pass": False,
            "error": probe["error"],
        }

    width, height = expected_resolution

    codec_check = {
        "pass": probe.get("video_codec") == expected_video_codec,
        "actual": probe.get("video_codec", "unknown"),
        "expected": expected_video_codec,
    }

    resolution_check = {
        "pass": probe.get("width") == width and probe.get("height") == height,
        "actual": f"{probe.get('width')}x{probe.get('height')}",
        "expected": f"{width}x{height}",
    }

    fps_check = {
        "pass": abs(probe.get("fps", 0) - expected_fps) < tolerance_fps,
        "actual": probe.get("fps", 0),
        "expected": expected_fps,
    }

    duration_check = {
        "pass": abs(probe.get("duration_seconds", 0) - expected_duration_seconds) < tolerance_duration,
        "actual": probe.get("duration_seconds", 0),
        "expected": expected_duration_seconds,
    }

    audio_check = {
        "pass": probe.get("has_audio", False),
        "actual": bool(probe.get("has_audio", False)),
        "expected": True,
        "channels": probe.get("audio_channels", 0),
        "sample_rate": probe.get("audio_sample_rate", 0),
    }

    audio_codec_check = {
        "pass": probe.get("audio_codec") == expected_audio_codec,
        "actual": probe.get("audio_codec", "none"),
        "expected": expected_audio_codec,
    }

    file_integrity = {
        "pass": probe.get("size_bytes", 0) > 1000,
        "size_bytes": probe.get("size_bytes", 0),
    }

    checks = [codec_check, resolution_check, fps_check, duration_check, audio_check, audio_codec_check, file_integrity]

    return {
        "all_pass": all(c["pass"] for c in checks if "pass" in c),
        "codec_check": codec_check,
        "resolution_check": resolution_check,
        "fps_check": fps_check,
        "duration_check": duration_check,
        "audio_check": audio_check,
        "audio_codec_check": audio_codec_check,
        "file_integrity": file_integrity,
    }


def check_pillow() -> dict[str, Any]:
    try:
        from PIL import Image
        return {"available": True, "version": Image.__version__}
    except ImportError as e:
        return {"available": False, "error": str(e)}


def probe_video(
    video_path: str, ffprobe_path: str | None = None
) -> dict[str, Any]:
    if not os.path.isfile(video_path):
        return {"error": f"File not found: {video_path}"}

    ffprobe = resolve_ffprobe(ffprobe_path)
    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", video_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip()}
        data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, RuntimeError) as e:
        return {"error": str(e)}

    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})

    info: dict[str, Any] = {
        "container": fmt.get("format_name", "unknown"),
        "duration_seconds": float(fmt.get("duration", 0)),
        "size_bytes": int(fmt.get("size", 0)),
        "bit_rate": fmt.get("bit_rate", "N/A"),
    }

    if video_stream:
        info["video_codec"] = video_stream.get("codec_name", "unknown")
        info["width"] = int(video_stream.get("width", 0))
        info["height"] = int(video_stream.get("height", 0))
        info["fps"] = _parse_fps(video_stream.get("avg_frame_rate", "0/1"))
        info["has_video"] = True
    else:
        info["has_video"] = False

    if audio_stream:
        info["audio_codec"] = audio_stream.get("codec_name", "unknown")
        info["audio_sample_rate"] = int(audio_stream.get("sample_rate", 0))
        info["audio_channels"] = int(audio_stream.get("channels", 0))
        info["has_audio"] = True
    else:
        info["has_audio"] = False

    return info


def _parse_fps(fps_str: str) -> float:
    try:
        if "/" in fps_str:
            num, den = fps_str.split("/")
            return float(num) / float(den)
        return float(fps_str)
    except (ValueError, ZeroDivisionError):
        return 0.0


BGM_FADE_OUT_SECONDS = 3.0


def probe_duration(media_path: str, ffprobe_path: str | None = None) -> float:
    """Duración en segundos de un archivo de audio o video. 0.0 si no se puede leer."""
    if not os.path.isfile(media_path):
        return 0.0
    try:
        ffprobe = resolve_ffprobe(ffprobe_path)
        result = subprocess.run(
            [
                ffprobe, "-v", "quiet", "-print_format", "json",
                "-show_format", media_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return 0.0
        return float(json.loads(result.stdout).get("format", {}).get("duration", 0))
    except (subprocess.TimeoutExpired, json.JSONDecodeError, RuntimeError, ValueError):
        return 0.0


def _build_compose_args(
    ffmpeg: str,
    scene_paths: list[str],
    scene_durations: list[float],
    narration_path: str,
    output_path: str,
    fps: int,
    bgm_path: str | None,
    bgm_volume: float,
    output_duration: float,
    subtitles_path: str | None = None,
    subtitles_style: str | None = None,
) -> list[str]:
    """Arma la línea de comando de ffmpeg. Sin ``bgm_path`` ni
    ``subtitles_path`` produce exactamente la misma invocación que antes de
    existir la música de fondo y los subtítulos."""
    n = len(scene_paths)

    inputs: list[str] = []
    for path in scene_paths:
        inputs.extend(["-loop", "1", "-framerate", str(fps), "-i", path])
    # La narración queda SIEMPRE en el índice n; la música, si la hay, en n+1.
    inputs.extend(["-i", narration_path])

    trim_parts = [
        f"[{i}:v]trim=duration={dur:.3f}[{i}v]"
        for i, dur in enumerate(scene_durations)
    ]
    concat_inputs = "".join(f"[{i}v]" for i in range(n))
    filter_complex = f"{';'.join(trim_parts)};{concat_inputs}concat=n={n}:v=1:a=0[v]"

    video_map = "[v]"
    if subtitles_path:
        # Los subtitulos se queman DESPUES del concat: sobre el video ya
        # armado, para que los tiempos del SRT coincidan con la linea de
        # tiempo final y no con la de un clip suelto.
        from adapters.subtitulos import ESTILO_POR_DEFECTO, ruta_para_filtro

        # Un .ass trae su propio estilo y force_style lo PISA: el del
        # karaoke se genera con Fontsize 98 y caja negra y salia a 22.
        # Solo se fuerza estilo cuando el llamador lo pide a proposito, o
        # cuando el archivo no es .ass (un .srt no trae estilo).
        es_ass = str(subtitles_path).lower().endswith(".ass")
        estilo = subtitles_style or (None if es_ass else ESTILO_POR_DEFECTO)
        filter_complex += (
            f";[v]subtitles='{ruta_para_filtro(subtitles_path)}'"
            + (f":force_style='{estilo}'" if estilo else "")
            + "[vsub]"
        )
        video_map = "[vsub]"

    if bgm_path:
        # -stream_loop es opción de ENTRADA: va pegada a su propio -i.
        inputs.extend(["-stream_loop", "-1", "-i", bgm_path])

        # El fade se ancla a la duración real de salida (la impone -shortest),
        # no a la suma de escenas: si la narración es más corta, un fade
        # calculado sobre las escenas nunca se dispararía.
        fade_start = max(0.0, output_duration - BGM_FADE_OUT_SECONDS)
        filter_complex += (
            f";[{n + 1}:a]volume={bgm_volume:.3f},"
            f"afade=t=out:st={fade_start:.3f}:d={BGM_FADE_OUT_SECONDS:.0f}[bgm]"
            # normalize=0 evita que amix atenúe la narración a la mitad; el
            # volumen de la música ya viene bajado por el filtro anterior.
            f";[{n}:a][bgm]amix=inputs=2:duration=first:normalize=0[a]"
        )
        audio_map = "[a]"
    else:
        audio_map = f"{n}:a"

    return [
        ffmpeg, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", video_map,
        "-map", audio_map,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        output_path,
    ]


def compose_video(
    scene_paths: list[str],
    scene_durations: list[float],
    narration_path: str,
    output_path: str,
    resolution: tuple[int, int] = (1920, 1080),
    fps: int = 30,
    ffmpeg_path: str | None = None,
    log_dir: str | None = None,
    timeout: int = 300,
    background_music_path: str | None = None,
    background_music_volume: float = 0.1,
    ffprobe_path: str | None = None,
    subtitles_path: str | None = None,
    subtitles_style: str | None = None,
) -> dict[str, Any]:
    """Compose scene images + narration audio into an MP4 video.

    Uses trim+concat filterchain (NOT concat demuxer) for reliable
    per-scene durations — the concat demuxer ignores the last entry's duration.

    Con ``background_music_path`` la música se repite hasta cubrir el video,
    se atenúa a ``background_music_volume`` y cierra con un fade de 3s.
    Si la mezcla falla, el video se produce SIN música (no se aborta) y el
    resultado lo declara en ``bgm_applied`` y ``warnings`` — nunca en silencio.

    Con ``subtitles_path`` (un .srt) los subtítulos se queman sobre el video
    ya concatenado. Misma regla: si fallan, el video sale sin ellos y se
    declara en ``subs_applied`` y ``warnings``.
    """
    ffmpeg = resolve_ffmpeg(ffmpeg_path)

    if os.path.exists(output_path):
        return {
            "success": False,
            "error": f"Output already exists: {output_path}",
        }

    warnings: list[str] = []
    bgm_path = background_music_path or None
    subs_path = subtitles_path or None

    if bgm_path and not os.path.isfile(bgm_path):
        warnings.append(f"[SIN MUSICA] archivo no encontrado: {bgm_path}")
        bgm_path = None
    if bgm_path and background_music_volume <= 0:
        warnings.append(
            f"[SIN MUSICA] volumen no positivo: {background_music_volume}"
        )
        bgm_path = None
    if subs_path and not os.path.isfile(subs_path):
        warnings.append(f"[SIN SUBTITULOS] archivo no encontrado: {subs_path}")
        subs_path = None

    try:
        total_duration = sum(scene_durations)
        output_duration = total_duration
        if bgm_path:
            narration_duration = probe_duration(narration_path, ffprobe_path)
            if narration_duration > 0:
                # -shortest recorta al menor de los dos.
                output_duration = min(total_duration, narration_duration)

        def _run(with_bgm: bool, with_subs: bool) -> subprocess.CompletedProcess:
            args = _build_compose_args(
                ffmpeg=ffmpeg,
                scene_paths=scene_paths,
                scene_durations=scene_durations,
                narration_path=narration_path,
                output_path=output_path,
                fps=fps,
                bgm_path=bgm_path if with_bgm else None,
                bgm_volume=background_music_volume,
                output_duration=output_duration,
                subtitles_path=subs_path if with_subs else None,
                subtitles_style=subtitles_style,
            )
            return subprocess.run(
                args, capture_output=True, text=True, timeout=timeout
            )

        # Se degrada de a un extra por vez, del menos esencial al mas: antes
        # de rendirse se prueba sin subtitulos, luego sin musica. Un video con
        # narracion vale mas que ningun video, pero la perdida se declara.
        intentos = [(bool(bgm_path), bool(subs_path))]
        if subs_path:
            intentos.append((bool(bgm_path), False))
        if bgm_path:
            intentos.append((False, False))

        bgm_applied = subs_applied = False
        result = None
        for i, (con_bgm, con_subs) in enumerate(intentos):
            if i > 0:
                # El guard de "output already exists" haria fallar el reintento
                # por el motivo equivocado: se descarta el archivo parcial.
                if os.path.exists(output_path):
                    os.remove(output_path)
            result = _run(with_bgm=con_bgm, with_subs=con_subs)
            if result.returncode == 0:
                bgm_applied, subs_applied = con_bgm, con_subs
                break
            if i + 1 < len(intentos):
                perdido = "SUBTITULOS" if con_subs and not intentos[i + 1][1] \
                    else "MUSICA"
                warnings.append(
                    f"[SIN {perdido}] ffmpeg salió con {result.returncode}; "
                    "se reintenta sin ese elemento"
                )
                if log_dir:
                    os.makedirs(log_dir, exist_ok=True)
                    with open(
                        os.path.join(log_dir, f"ffmpeg_intento{i}_stderr.log"), "w"
                    ) as f:
                        f.write(result.stderr or "")

        if result.returncode != 0:
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
                with open(os.path.join(log_dir, "ffmpeg_stderr.log"), "w") as f:
                    f.write(result.stderr)
            return {
                "success": False,
                "error": f"ffmpeg exited with code {result.returncode}",
                "stderr": result.stderr[-2000:] if result.stderr else "",
                "bgm_applied": False,
                "subs_applied": False,
                "warnings": warnings,
            }

        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, "ffmpeg_stdout.log"), "w") as f:
                f.write(result.stdout)
            with open(os.path.join(log_dir, "ffmpeg_stderr.log"), "w") as f:
                f.write(result.stderr)

        return {
            "success": True,
            "output_path": output_path,
            "bgm_applied": bgm_applied,
            "subs_applied": subs_applied,
            "warnings": warnings,
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"ffmpeg timed out after {timeout}s",
            "bgm_applied": False,
            "subs_applied": False,
            "warnings": warnings,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "bgm_applied": False,
            "subs_applied": False,
            "warnings": warnings,
        }


def _write_concat_file(
    path: str, scene_paths: list[str], scene_durations: list[float]
) -> None:
    """Write concat demuxer file.

    NOTE: ffmpeg concat demuxer IGNORES the duration of the LAST file entry.
    Workaround: repeat the last scene without a duration directive so the
    penultimate entry's duration is honored.
    """
    ONE_FRAME = 0.040  # 1 frame at 25fps

    with open(path, "w") as f:
        for i, (img_path, dur) in enumerate(zip(scene_paths, scene_durations)):
            escaped = img_path.replace("'", "'\\''")
            if i == len(scene_paths) - 1 and dur > ONE_FRAME:
                f.write(f"file '{escaped}'\n")
                f.write(f"duration {dur - ONE_FRAME:.3f}\n")
                f.write(f"file '{escaped}'\n")
            else:
                f.write(f"file '{escaped}'\n")
                f.write(f"duration {dur:.3f}\n")
