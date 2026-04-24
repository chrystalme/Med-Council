"""
Speech (STT + TTS) provider abstraction.

Three backends, selected by `SPEECH_PROVIDER`:

- `openrouter` (default) — Whisper + TTS routed through the OpenRouter unified
                           OpenAI-compatible API (reuses `OPENROUTER_API_KEY`).
                           Override endpoint/key/model via `SPEECH_BASE_URL`,
                           `SPEECH_API_KEY`, `SPEECH_STT_MODEL`, `SPEECH_TTS_MODEL`
                           to swap in Groq, OpenAI direct, or a self-hosted server.
                           **Note:** `OPENAI_API_KEY` is reserved for tracing and
                           is never used here — keeps speech billing separate.
- `gcloud`               — Google Cloud Speech-to-Text + Text-to-Speech. Uses
                           the Cloud Run runtime service account on GCP.
- `disabled`             — transcription/synthesis endpoints return 503.

On quota errors (HTTP 429), the provider raises `SpeechQuotaError` with a
user-friendly message so the API layer can surface it as a structured 429 rather
than a generic 502.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol

log = logging.getLogger("medai.speech")


class SpeechQuotaError(RuntimeError):
    """Raised when the upstream speech provider reports quota/rate-limit (429)."""


class SpeechUnavailableError(RuntimeError):
    """Raised when the configured provider isn't set up (e.g. disabled/stub)."""


class SpeechProvider(Protocol):
    def transcribe(self, audio_bytes: bytes, mime_type: str, filename: str = "audio.webm") -> str: ...
    def synthesize(self, text: str, voice: str = "alloy") -> bytes: ...


def _looks_like_quota_error(exc: BaseException) -> bool:
    """Heuristic — OpenAI SDK wraps 429s as RateLimitError, but providers vary."""
    msg = str(exc).lower()
    if "429" in msg or "quota" in msg or "rate limit" in msg or "insufficient_quota" in msg:
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    return status == 429


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenAICompatibleSpeechProvider:
    """Whisper + TTS via any OpenAI-compatible endpoint.

    **Default routing:** OpenRouter (reuses `OPENROUTER_API_KEY`). `OPENAI_API_KEY`
    is deliberately *not* read here — that key is reserved for tracing and
    keeping it out of the speech path prevents surprise billing on the OpenAI
    account when the OpenRouter quota or model coverage moves.

    Env:
        SPEECH_API_KEY     — API key for the endpoint. Falls back to OPENROUTER_API_KEY.
        SPEECH_BASE_URL    — endpoint base URL. Defaults to OpenRouter's
                             (https://openrouter.ai/api/v1). Set to
                             https://api.groq.com/openai/v1 for Groq Whisper,
                             https://api.openai.com/v1 for OpenAI direct.
        SPEECH_STT_MODEL   — transcription model id. Defaults to `openai/whisper-1`
                             (OpenRouter's prefixed name).
        SPEECH_TTS_MODEL   — synthesis model id. Defaults to `openai/tts-1`.
    """

    def __init__(self) -> None:
        from openai import OpenAI

        key = (os.environ.get("SPEECH_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or "").strip()
        if not key:
            raise SpeechUnavailableError(
                "SPEECH_API_KEY (or OPENROUTER_API_KEY) is required for the openrouter "
                "speech provider. OPENAI_API_KEY is intentionally not used — it's for tracing only."
            )
        base_url = (os.environ.get("SPEECH_BASE_URL") or "").strip() or OPENROUTER_BASE_URL
        self._client = OpenAI(
            api_key=key,
            base_url=base_url,
            default_headers={
                "HTTP-Referer": "https://medai-council.local",
                "X-Title": "MedAI Council",
            },
        )
        self._stt_model = (os.environ.get("SPEECH_STT_MODEL") or "openai/whisper-1").strip()
        self._tts_model = (os.environ.get("SPEECH_TTS_MODEL") or "openai/tts-1").strip()

    def transcribe(self, audio_bytes: bytes, mime_type: str, filename: str = "audio.webm") -> str:
        try:
            result = self._client.audio.transcriptions.create(
                model=self._stt_model,
                file=(filename, audio_bytes, mime_type),
            )
        except Exception as exc:
            if _looks_like_quota_error(exc):
                raise SpeechQuotaError(
                    "Speech transcription provider is out of quota. Top up your OpenRouter "
                    "credits, or switch providers (SPEECH_PROVIDER=gcloud, or "
                    "SPEECH_BASE_URL=https://api.groq.com/openai/v1 with a Groq key)."
                ) from exc
            raise
        return (result.text or "").strip()

    def synthesize(self, text: str, voice: str = "alloy") -> bytes:
        try:
            response = self._client.audio.speech.create(
                model=self._tts_model,
                voice=voice,
                input=text,
                response_format="mp3",
            )
        except Exception as exc:
            if _looks_like_quota_error(exc):
                raise SpeechQuotaError(
                    "Speech synthesis provider is out of quota. Top up your OpenRouter "
                    "credits, or switch SPEECH_PROVIDER (gcloud on GCP, or set "
                    "SPEECH_BASE_URL to a different provider)."
                ) from exc
            raise
        return response.read()


# Kept as an alias so older call sites still work.
OpenAISpeechProvider = OpenAICompatibleSpeechProvider


class GoogleSpeechProvider:
    """Google Cloud Speech-to-Text v1 + Text-to-Speech for GCP deploys.

    On Cloud Run, ADC (Application Default Credentials) picks up the runtime
    service account automatically — no key file. Locally, set
    ``GOOGLE_APPLICATION_CREDENTIALS`` to a service-account JSON or run
    ``gcloud auth application-default login``.

    Env:
        GOOGLE_STT_LANGUAGE   — BCP-47 code. Defaults to "en-US".
        GOOGLE_TTS_VOICE      — Google voice name. Defaults to "en-US-Neural2-C".
                                The `voice` argument to ``synthesize`` is ignored
                                when this env is set.
    """

    _MIME_TO_ENCODING = {
        "audio/webm": "WEBM_OPUS",
        "audio/ogg": "OGG_OPUS",
        "audio/ogg; codecs=opus": "OGG_OPUS",
        "audio/wav": "LINEAR16",
        "audio/x-wav": "LINEAR16",
        "audio/mpeg": "MP3",
        "audio/mp3": "MP3",
        "audio/flac": "FLAC",
        "audio/x-flac": "FLAC",
    }

    def __init__(self) -> None:
        try:
            from google.cloud import speech, texttospeech  # noqa: F401
        except ImportError as exc:  # pragma: no cover — only hit when deps missing
            raise SpeechUnavailableError(
                "google-cloud-speech / google-cloud-texttospeech are not installed."
            ) from exc

        from google.cloud import speech as _speech
        from google.cloud import texttospeech as _tts

        self._stt = _speech.SpeechClient()
        self._tts = _tts.TextToSpeechClient()
        self._stt_types = _speech
        self._tts_types = _tts
        self._lang = (os.environ.get("GOOGLE_STT_LANGUAGE") or "en-US").strip()
        self._voice_override = (os.environ.get("GOOGLE_TTS_VOICE") or "").strip()

    def _encoding_for(self, mime: str):
        key = (mime or "").split(";")[0].strip().lower()
        name = self._MIME_TO_ENCODING.get(key) or self._MIME_TO_ENCODING.get(mime.lower())
        if not name:
            return self._stt_types.RecognitionConfig.AudioEncoding.ENCODING_UNSPECIFIED
        return getattr(self._stt_types.RecognitionConfig.AudioEncoding, name)

    def transcribe(self, audio_bytes: bytes, mime_type: str, filename: str = "audio.webm") -> str:
        speech = self._stt_types
        config = speech.RecognitionConfig(
            encoding=self._encoding_for(mime_type),
            language_code=self._lang,
            enable_automatic_punctuation=True,
            model="default",
        )
        audio = speech.RecognitionAudio(content=audio_bytes)
        try:
            response = self._stt.recognize(config=config, audio=audio)
        except Exception as exc:
            if _looks_like_quota_error(exc):
                raise SpeechQuotaError(
                    "Google Speech-to-Text quota exhausted. Check the quota for the "
                    "Cloud Run runtime service account in the GCP console."
                ) from exc
            raise
        return " ".join(
            alt.transcript.strip()
            for result in response.results
            for alt in (result.alternatives[:1] if result.alternatives else [])
            if alt.transcript
        ).strip()

    def synthesize(self, text: str, voice: str = "alloy") -> bytes:
        tts = self._tts_types
        voice_name = self._voice_override or voice or "en-US-Neural2-C"
        # OpenAI's "alloy"/"nova" aren't Google voice names — fall back to a sensible default.
        if voice_name in {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}:
            voice_name = "en-US-Neural2-C"
        language_code = "-".join(voice_name.split("-")[:2]) or "en-US"

        voice_params = tts.VoiceSelectionParams(language_code=language_code, name=voice_name)
        audio_config = tts.AudioConfig(audio_encoding=tts.AudioEncoding.MP3)
        input_text = tts.SynthesisInput(text=text)
        try:
            response = self._tts.synthesize_speech(
                input=input_text, voice=voice_params, audio_config=audio_config
            )
        except Exception as exc:
            if _looks_like_quota_error(exc):
                raise SpeechQuotaError("Google TTS quota exhausted.") from exc
            raise
        return response.audio_content


class DisabledSpeechProvider:
    def transcribe(self, *args, **kwargs) -> str:
        raise SpeechUnavailableError("Speech is disabled (SPEECH_PROVIDER=disabled).")

    def synthesize(self, *args, **kwargs) -> bytes:
        raise SpeechUnavailableError("Speech is disabled (SPEECH_PROVIDER=disabled).")


_provider: SpeechProvider | None = None


def get_speech_provider() -> SpeechProvider:
    """Return the configured speech provider. Lazily constructed."""
    global _provider
    if _provider is not None:
        return _provider

    backend = (os.environ.get("SPEECH_PROVIDER") or "openrouter").lower()
    if backend == "gcloud":
        _provider = GoogleSpeechProvider()
    elif backend == "disabled":
        _provider = DisabledSpeechProvider()
    else:
        # openrouter / openai / any OpenAI-compatible endpoint
        _provider = OpenAICompatibleSpeechProvider()
    return _provider


def reset_speech_provider_for_test() -> None:  # pragma: no cover
    """Test hook — drop the memoised provider so a new env-var set takes effect."""
    global _provider
    _provider = None
