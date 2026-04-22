"""
Speech (STT + TTS) provider abstraction.

Local/OpenAI: Whisper (transcription) + OpenAI TTS (synthesis) via direct
OpenAI API (NOT OpenRouter — neither model is available on OpenRouter today).

Prod (GCP): Google Cloud Speech-to-Text + Text-to-Speech (stub for now).

Swap via env var: SPEECH_PROVIDER=openai|gcloud.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol

log = logging.getLogger("medai.speech")


class SpeechProvider(Protocol):
    def transcribe(self, audio_bytes: bytes, mime_type: str, filename: str = "audio.webm") -> str: ...
    def synthesize(self, text: str, voice: str = "alloy") -> bytes: ...


class OpenAISpeechProvider:
    """Whisper + OpenAI TTS via the direct OpenAI API.

    Requires OPENAI_API_KEY (separate from OpenRouter). This keeps the routing
    concerns clean: inference goes through OpenRouter/Vertex; speech goes
    through its own provider because Whisper/TTS aren't universally exposed.
    """

    def __init__(self) -> None:
        from openai import OpenAI

        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "OPENAI_API_KEY is required for OpenAISpeechProvider. "
                "Add it to apps/api/.env.local"
            )
        self._client = OpenAI(api_key=key)

    def transcribe(self, audio_bytes: bytes, mime_type: str, filename: str = "audio.webm") -> str:
        # The SDK accepts a (filename, bytes, mime) tuple for in-memory audio.
        result = self._client.audio.transcriptions.create(
            model="whisper-1",
            file=(filename, audio_bytes, mime_type),
        )
        return (result.text or "").strip()

    def synthesize(self, text: str, voice: str = "alloy") -> bytes:
        # tts-1 is fast + cheap; tts-1-hd is higher quality. Start with tts-1.
        response = self._client.audio.speech.create(
            model="tts-1",
            voice=voice,
            input=text,
            response_format="mp3",
        )
        # The SDK exposes read() on the response stream.
        return response.read()


class GoogleSpeechProvider:
    """Stub — implement on GCP migration using google-cloud-speech + google-cloud-texttospeech."""

    def transcribe(self, *args, **kwargs) -> str:
        raise NotImplementedError("GoogleSpeechProvider not yet implemented.")

    def synthesize(self, *args, **kwargs) -> bytes:
        raise NotImplementedError("GoogleSpeechProvider not yet implemented.")


_provider: SpeechProvider | None = None


def get_speech_provider() -> SpeechProvider:
    """Return the configured speech provider. Lazily constructed."""
    global _provider
    if _provider is not None:
        return _provider

    backend = (os.environ.get("SPEECH_PROVIDER") or "openai").lower()
    if backend == "gcloud":
        _provider = GoogleSpeechProvider()
    else:
        _provider = OpenAISpeechProvider()
    return _provider
