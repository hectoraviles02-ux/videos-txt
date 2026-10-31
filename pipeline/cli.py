from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from adapters.base import RenderConfig, RunContext, Storyboard, VideoAdapter
from adapters.ffmpeg_pillow_adapter import FFmpegPillowAdapter
from pipeline.tts_protocol import TTSProvider
from runs.run_manager import RunManager, _sha256

VIDEOS_DIR = Path("videos")


def _slugify_titulo(texto: str) -> str:
    """Convierte un titulo en slug legible: minusculas, sin acentos, guiones."""
    import re
    import unicodedata

    texto = unicodedata.normalize("NFKD", texto)
    texto = texto.encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^a-zA-Z0-9\s]", "", texto)
    texto = re.sub(r"\s+", "-", texto.lower().strip())
    texto = re.sub(r"-+", "-", texto)
    return texto[:70] or "sin-titulo"


def _copiar_video_legible(video_path: str, storyboard: Storyboard) -> str:
    """Copia el video aprobado a ``videos/<fecha>_<slug>.mp4`` (nombre legible).

    Mantiene el original en ``runs/<run_id>/`` para trazabilidad (R05).
    """
    import shutil
    from datetime import datetime

    titulo = getattr(storyboard, "metadata", {}) or {}
    if isinstance(titulo, dict):
        titulo = titulo.get("title") or titulo.get("titulo") or ""
    if not titulo and storyboard.scenes:
        titulo = storyboard.scenes[0].title or ""

    fecha = datetime.now().strftime("%Y-%m-%d")
    nombre = f"{fecha}_{_slugify_titulo(str(titulo))}.mp4"
    dest = VIDEOS_DIR / nombre
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

    # No sobrescribir; si ya existe un video del mismo titulo hoy, añadir sufijo
    contador = 2
    base = dest
    while dest.exists():
        dest = VIDEOS_DIR / f"{base.stem}-{contador}.mp4"
        contador += 1

    shutil.copy2(video_path, str(dest))
    print(f"[pipeline] Video guardado: {dest}")
    return str(dest)


@dataclass
class PipelineResult:
    run_id: str
    status: str
    total_time: float
    tts_time: float
    narration_duration: float
    result: Any  # RenderResult
    manifest_path: str


def _ajustar_escenas_a_narracion(
    sb: Storyboard,
    duracion_real: float,
    por_escena: list[float] | None = None,
) -> None:
    """Ajusta los tiempos de las escenas al audio real.

    Con `por_escena` cada escena dura lo que de verdad duro su narracion. Sin
    eso solo queda repartir en proporcion a lo estimado, que reparte el error
    entre todas: MEDIDO el 2026-08-22 en el documental de agujeros negros, el
    guion daba 8.4s parejos a las trece escenas cuando la narracion real iba
    de 5.6s a 10s. Los cortes de imagen y los subtitulos no caian donde
    terminaba la frase.

    Modifica el storyboard en memoria.
    """
    if duracion_real <= 0 or not sb.scenes:
        return

    if por_escena and len(por_escena) == len(sb.scenes) and all(
            d > 0 for d in por_escena):
        t = 0.0
        for escena, d in zip(sb.scenes, por_escena):
            escena.start_seconds = round(t, 3)
            t += d
            escena.end_seconds = round(t, 3)
        sb.scenes[-1].end_seconds = round(duracion_real, 3)
        if isinstance(sb.metadata, dict):
            sb.metadata["total_duration_seconds"] = round(duracion_real, 3)
        print("[pipeline] Escenas ajustadas a la duracion REAL de cada "
              "narracion (%.1fs total)" % duracion_real)
        return

    fin_guion = float(sb.scenes[-1].end_seconds)
    if fin_guion <= 0:
        return

    factor = duracion_real / fin_guion
    # Diferencias menores no valen el reescalado: el adaptador tolera 2s.
    if abs(fin_guion - duracion_real) < 0.5:
        return

    for escena in sb.scenes:
        escena.start_seconds = round(escena.start_seconds * factor, 3)
        escena.end_seconds = round(escena.end_seconds * factor, 3)
    # El ultimo cierra exacto para que no quede deriva por redondeo.
    sb.scenes[-1].end_seconds = round(duracion_real, 3)
    if isinstance(sb.metadata, dict):
        sb.metadata["total_duration_seconds"] = round(duracion_real, 3)

    print(
        f"[pipeline] Escenas reajustadas: {fin_guion:.1f}s estimados "
        f"-> {duracion_real:.1f}s reales (x{factor:.3f})"
    )


def _parse_resolucion(r: str) -> tuple[int, int]:
    """Ancho y alto. El karaoke los necesita para ajustar el tamano."""
    try:
        a, b = str(r).lower().split("x")
        return int(a), int(b)
    except (ValueError, AttributeError):
        return 1920, 1080


def run_pipeline(
    storyboard_path: str,
    provider: TTSProvider,
    *,
    resolution: str = "1920x1080",
    fps: int = 30,
    run_id: str | None = None,
    ffmpeg_path: str | None = None,
    ffprobe_path: str | None = None,
    adapter: VideoAdapter | None = None,
    storyboard: Storyboard | None = None,
    subtitulos: bool = False,
    musica: str | None = None,
    karaoke: bool = False,
) -> PipelineResult:
    if not storyboard_path and storyboard is None:
        raise ValueError("Either storyboard_path or storyboard must be provided")
    rm = RunManager(pipeline="full_production", run_id=run_id)
    run_id = rm.start()
    print(f"[pipeline] run_id: {run_id}")
    print(f"[pipeline] run_dir: {rm.run_dir}")

    if storyboard is not None:
        sb_json_path = str(rm.run_dir / "storyboard.json")
        with open(sb_json_path, "w", encoding="utf-8") as f:
            json.dump(asdict(storyboard), f, indent=2, ensure_ascii=False)
        rm.add_input("file", sb_json_path, _sha256(sb_json_path))
        sb = Storyboard.from_json(sb_json_path)
    else:
        sb_path = rm.copy_input(storyboard_path, "storyboard.json")
        sb = Storyboard.from_json(sb_path)
        rm.add_input("file", storyboard_path, _sha256(storyboard_path))

    rm.set_tool_versions({"provider": provider.name})

    # ── TTS ──────────────────────────────────────────────────────────────
    print(f"[pipeline] Generating narration via {provider.name}...")
    t0 = time.time()
    narration = provider.generate(sb, str(rm.logs_dir))
    t_tts = time.time() - t0
    print(f"  -> {narration.duration_seconds:.1f}s narration ({t_tts:.1f}s)")

    nar_dst = str(rm.run_dir / "narration.wav")
    if os.path.isfile(narration.audio_path):
        import shutil
        shutil.copy2(narration.audio_path, nar_dst)
        rm.add_output("audio", nar_dst)
    rm.transition("AUDIO_READY")

    # El audio manda. La duracion de las escenas es una ESTIMACION hecha antes
    # de hablar (~2.5 palabras/seg); la narracion real casi nunca cae ahi, y
    # una diferencia de mas de 2s hacia que el adaptador rechazara el render
    # entero -medido: narracion 27.32s contra storyboard 30.0s-. En vez de
    # fallar, se reescalan las escenas a la duracion real, conservando la
    # proporcion entre ellas: el video sigue el ritmo de la voz.
    _ajustar_escenas_a_narracion(
        sb, narration.duration_seconds,
        getattr(narration, "duraciones_por_escena", None),
    )

    # ── Render ───────────────────────────────────────────────────────────
    config = RenderConfig(
        resolution=resolution,
        fps=fps,
        output_filename="output.mp4",
        max_duration_seconds=7200,
        background_music=musica,
    )

    # Los subtitulos salen de la narracion del storyboard -el mismo texto que
    # habla el TTS-, no de la transcripcion de la fuente: coinciden palabra
    # por palabra sin pasar el audio generado por un ASR.
    #
    # Con `karaoke` es al reves, y a proposito: para resaltar la palabra que
    # SUENA hay que saber cuando suena, y eso solo lo da transcribir el audio
    # ya generado. Es una vuelta de mas y es la unica forma de que el
    # resaltado no se despegue de la voz.
    if subtitulos:
        ruta_subs = None
        if karaoke:
            from adapters.subtitulos_karaoke import generar as generar_karaoke
            an, al = _parse_resolucion(resolution)
            ruta_subs = generar_karaoke(
                narration.audio_path, str(rm.run_dir / "subtitulos.ass"),
                ancho=an, alto=al)
            if ruta_subs:
                print("[pipeline] Subtitulos karaoke (palabra resaltada)")
            else:
                print("[pipeline] AVISO: el karaoke no salio; van normales")
        if not ruta_subs:
            from adapters.subtitulos import generar_srt
            ruta_subs = generar_srt(sb, str(rm.run_dir / "subtitulos.srt"))
        if ruta_subs:
            config.engine_specific["subtitles_path"] = ruta_subs
            rm.add_output("subtitulos", ruta_subs)
            print(f"[pipeline] Subtitulos: {os.path.basename(ruta_subs)}")
        else:
            print("[pipeline] AVISO: no hay narracion para subtitular")

    ctx = RunContext(
        run_id=run_id,
        output_dir=str(rm.run_dir),
        log_dir=str(rm.logs_dir),
    )

    print(f"[pipeline] Rendering video ({resolution} @ {fps}fps)...")
    _adapter = adapter or FFmpegPillowAdapter(
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
    )
    result = _adapter.render(sb, narration, config, ctx)
    t_total = time.time() - t0

    if result.status == "success":
        sz_mb = result.video_size_bytes / (1024 * 1024) if result.video_size_bytes else 0
        print(f"  OK: {result.video_path}")
        print(f"      {result.video_duration_seconds:.1f}s @ {result.actual_resolution} ({sz_mb:.1f}MB)")
        rm.add_output("render", result.video_path)

        # ── Quality gate ──────────────────────────────────────────────────
        qg = _run_quality_gate(result.video_path, ffprobe_path)
        if qg:
            qg_path = str(rm.run_dir / "quality_report.json")
            with open(qg_path, "w") as f:
                json.dump(qg, f, indent=2)
            rm.add_evidence(qg_path, "validation_report")

        all_pass = (result.validation and result.validation.all_pass) and (qg and qg.get("all_pass", False))
        if all_pass:
            rm.transition("TECHNICAL_PASS", evidence=["quality_report.json"])
            legible = _copiar_video_legible(result.video_path, sb)
            rm.add_output("video_legible", legible)
        else:
            reject_reason = ""
            if qg and not qg.get("image_content_pass", True):
                reject_reason = "Sin imagenes reales en el video"
            elif result.validation and not result.validation.all_pass:
                reject_reason = "Validacion de adapter fallo"
            else:
                reject_reason = "Quality gate unknown failure"
            rm.add_error("quality_gate", "REJECTED", reject_reason)
            # Delete failed video immediately
            if result.video_path and os.path.isfile(result.video_path):
                os.remove(result.video_path)
                print(f"  VIDEO RECHAZADO y eliminado: {reject_reason}")
            # Skip Telegram delivery for rejected videos
            rm.finish("FAILED")
            return PipelineResult(
                run_id=run_id,
                status="FAILED",
                total_time=t_total,
                tts_time=t_tts,
                narration_duration=narration.duration_seconds,
                result=result,
                manifest_path=rm.manifest_path(),
            )

        # ── Telegram ─────────────────────────────────────────────────────
        _try_telegram(result.video_path, rm, result)
    else:
        err_msg = json.dumps(result.error) if result.error else "unknown error"
        print(f"  FAILED: {err_msg}")
        rm.add_error("render", "RENDER_FAIL", str(result.error))

    status = "TECHNICAL_PASS" if result.status == "success" else "FAILED"
    rm.finish(status)

    print(f"[pipeline] Total: {t_total:.1f}s (TTS: {t_tts:.1f}s)")
    print(f"[pipeline] Manifest: {rm.manifest_path()}")

    return PipelineResult(
        run_id=run_id,
        status=status,
        total_time=t_total,
        tts_time=t_tts,
        narration_duration=narration.duration_seconds,
        result=result,
        manifest_path=rm.manifest_path(),
    )


def _run_quality_gate(video_path: str, ffprobe_path: str | None) -> dict | None:
    try:
        ffprobe = ffprobe_path or "ffprobe"
        r = subprocess.run(
            [ffprobe, "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", video_path],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return None
        probe = json.loads(r.stdout)
        video_s = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {})
        audio_s = next((s for s in probe.get("streams", []) if s.get("codec_type") == "audio"), {})
        fmt = probe.get("format", {})

        # ── Image content check via OpenCV ──────────────────────────────
        image_content_pass = True
        solid_frame_ratio = 0.0
        try:
            import cv2
            cap = cv2.VideoCapture(video_path)
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            if total > 0 and fps > 0:
                # Sample ~10 frames evenly across the video
                sample_count = min(10, total)
                step = max(1, total // sample_count)
                solid_frames = 0
                checked = 0
                for i in range(0, total, step):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, i)
                    ret, frame = cap.read()
                    if not ret:
                        continue
                    checked += 1
                    # Convert to grayscale and check std deviation
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    std = gray.std()
                    if std < 5.0:
                        solid_frames += 1
                cap.release()
                solid_frame_ratio = solid_frames / max(checked, 1)
                image_content_pass = solid_frame_ratio < 0.6
        except ImportError:
            logger.warning("OpenCV not available, skipping image content check")
        except Exception as e:
            logger.warning("Image content check failed: %s", e)

        result = {
            "video_codec": video_s.get("codec_name"),
            "resolution": f"{video_s.get('width')}x{video_s.get('height')}",
            "fps": video_s.get("avg_frame_rate"),
            "audio_codec": audio_s.get("codec_name"),
            "sample_rate": audio_s.get("sample_rate"),
            "channels": audio_s.get("channels"),
            "container": fmt.get("format_name"),
            "duration_seconds": float(fmt.get("duration", 0)),
            "size_bytes": int(fmt.get("size", 0)),
            "image_content_pass": image_content_pass,
            "solid_frame_ratio": round(solid_frame_ratio, 3),
            "all_pass": image_content_pass,
        }
        return result
    except Exception:
        return None


def _try_telegram(video_path: str, rm: RunManager, result, chat_id: int | None = None) -> None:
    try:
        import review_channels.telegram_channel as tg
        if not tg.is_enabled() and chat_id is None:
            rm.add_warning("telegram", "Telegram channel not enabled")
            return

        # If dynamic chat_id is provided, use send_video_to_chat directly
        if chat_id is not None:
            pkg = tg.send_video_to_chat(
                chat_id=chat_id,
                file_path=video_path,
                caption=f"Pipeline completed: {rm.run_id}",
            )
            tel_path = str(rm.run_dir / "telegram_delivery.json")
            with open(tel_path, "w") as f:
                json.dump(pkg, f, indent=2)
            if pkg.get("status") == "success":
                rm.add_evidence(tel_path, "review_package_sent")
                print(f"  Telegram (chat={chat_id}): OK msg_id={pkg.get('message_id')}")
            else:
                rm.add_warning("telegram", f"Delivery to chat {chat_id} failed: {pkg.get('error')}")
            return

        meta = {
            "title": "Pipeline Production",
            "total_duration_seconds": result.video_duration_seconds or 0,
            "resolution": result.actual_resolution or "N/A",
            "run_id": rm.run_id,
            "hash_short": (result.video_hash or "")[:8],
        }

        v_path = str(rm.run_dir / "validation_report.json")
        if not os.path.isfile(v_path):
            v_path = None

        pkg = tg.send_review_package(
            video_path=video_path,
            validation_report_path=v_path,
            metadata=meta,
        )

        tel_path = str(rm.run_dir / "telegram_delivery.json")
        with open(tel_path, "w") as f:
            json.dump(pkg, f, indent=2)

        if pkg.get("status") == "success":
            rm.add_evidence(tel_path, "review_package_sent")
            msg_ids = pkg.get("message_ids", [])
            print(f"  Telegram: OK message_ids={msg_ids}")
        else:
            rm.add_warning("telegram", f"Delivery {pkg.get('status')}: {pkg.get('error')}")

    except Exception as e:
        rm.add_warning("telegram", f"Telegram exception: {e}")


def main() -> None:
    from pipeline.providers.gtts import GTTSAdapter

    parser = argparse.ArgumentParser(
        description="VIDEOS-TXT Pipeline: storyboard.json -> TTS -> render -> MP4 -> Telegram"
    )
    parser.add_argument("storyboard", help="Path to storyboard JSON file")
    parser.add_argument(
        "--resolution", default="1920x1080",
        choices=["1920x1080", "1280x720", "3840x2160"],
    )
    parser.add_argument("--fps", type=int, default=30, choices=[24, 30, 60])
    parser.add_argument("--lang", default="es", help="TTS language code")
    parser.add_argument("--tts-tld", default="com", help="gTTS TLD (com, com.mx, etc.)")
    parser.add_argument("--run-id", default=None, help="Custom run ID")
    parser.add_argument("--ffmpeg", default=None, help="FFmpeg path override")
    parser.add_argument("--ffprobe", default=None, help="FFprobe path override")
    parser.add_argument(
        "--subtitulos", action="store_true",
        help="Quemar subtitulos desde la narracion del storyboard",
    )
    parser.add_argument(
        "--karaoke", action="store_true",
        help="Subtitulos con la palabra hablada resaltada (transcribe el "
             "audio ya generado para saber cuando suena cada palabra)",
    )
    parser.add_argument(
        "--musica", default=None, help="Ruta a un audio de musica de fondo",
    )
    parser.add_argument(
        "--adapter", default="ffmpeg", choices=["ffmpeg", "sd"],
        help="Video render adapter (default: ffmpeg)",
    )

    args = parser.parse_args()

    if not os.path.isfile(args.storyboard):
        print(f"Error: storyboard not found: {args.storyboard}", file=sys.stderr)
        sys.exit(1)

    provider: TTSProvider = GTTSAdapter(
        lang=args.lang,
        tld=args.tts_tld,
        ffmpeg_path=args.ffmpeg,
    )

    adapter = None
    if args.adapter == "sd":
        from adapters.sd_image_adapter import SDImageAdapter
        adapter = SDImageAdapter(
            ffmpeg_path=args.ffmpeg,
            ffprobe_path=args.ffprobe,
        )

    try:
        run_pipeline(
            args.storyboard,
            provider,
            resolution=args.resolution,
            fps=args.fps,
            run_id=args.run_id,
            ffmpeg_path=args.ffmpeg,
            ffprobe_path=args.ffprobe,
            adapter=adapter,
            subtitulos=args.subtitulos,
            musica=args.musica,
            karaoke=args.karaoke,
        )
    except Exception as e:
        print(f"Pipeline failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
