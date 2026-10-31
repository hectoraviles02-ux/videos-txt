from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from adapters.base import NarrationInput


@runtime_checkable
class TTSProvider(Protocol):
    """Contrato que cualquier proveedor TTS debe implementar.

    El pipeline solo conoce este Protocol. Cada proveedor (gTTS, Piper,
    Edge-TTS, ElevenLabs) encapsula sus dependencias internas.
    """

    @property
    def name(self) -> str:
        """Nombre único del proveedor (ej. 'gtts', 'piper', 'elevenlabs')."""
        ...

    def generate(
        self,
        storyboard: Any,
        output_dir: str,
    ) -> NarrationInput:
        """Produce audio narrado a partir de un storyboard.

        Args:
            storyboard: Storyboard con escenas que contienen narration text.
            output_dir: Directorio donde escribir el archivo de audio.

        Returns:
            NarrationInput apuntando al archivo generado.

        Raises:
            RuntimeError: Si la generación falla.
        """
        ...
