"""Tests for the speech (STT + TTS) provider abstraction.

Network calls are stubbed via OpenAI client replacement and an
in-process `google.cloud.speech` / `google.cloud.texttospeech` shim.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch


# ── helpers ────────────────────────────────────────────────────────────────


class _RateLimited(Exception):
    """Looks like a 429 to _looks_like_quota_error."""

    status_code = 429


# ── _looks_like_quota_error ─────────────────────────────────────────────────


class LooksLikeQuotaErrorTest(unittest.TestCase):
    def test_status_code_attribute(self) -> None:
        from medai_api.speech import _looks_like_quota_error

        self.assertTrue(_looks_like_quota_error(_RateLimited("nope")))

    def test_message_match(self) -> None:
        from medai_api.speech import _looks_like_quota_error

        self.assertTrue(_looks_like_quota_error(RuntimeError("rate limit exceeded")))
        self.assertTrue(_looks_like_quota_error(RuntimeError("HTTP 429")))
        self.assertTrue(_looks_like_quota_error(RuntimeError("insufficient_quota")))

    def test_other_errors_pass_through(self) -> None:
        from medai_api.speech import _looks_like_quota_error

        self.assertFalse(_looks_like_quota_error(ValueError("bad input")))


# ── OpenAICompatibleSpeechProvider ─────────────────────────────────────────


class OpenAICompatibleSpeechProviderTest(unittest.TestCase):
    def test_requires_speech_or_openrouter_key(self) -> None:
        with patch.dict(os.environ, {"SPEECH_API_KEY": "", "OPENROUTER_API_KEY": ""}):
            from medai_api.speech import OpenAICompatibleSpeechProvider, SpeechUnavailableError

            with self.assertRaises(SpeechUnavailableError):
                OpenAICompatibleSpeechProvider()

    def _build(self, env: dict[str, str] | None = None):
        with patch.dict(os.environ, {"SPEECH_API_KEY": "k", **(env or {})}):
            from medai_api.speech import OpenAICompatibleSpeechProvider

            return OpenAICompatibleSpeechProvider()

    def test_transcribe_returns_text(self) -> None:
        provider = self._build()
        captured: dict = {}

        class _Result:
            text = "  hello world  "

        class _Transcriptions:
            def create(self, **kwargs):
                captured.update(kwargs)
                return _Result()

        class _Audio:
            transcriptions = _Transcriptions()

        class _Client:
            audio = _Audio()

        provider._client = _Client()
        out = provider.transcribe(b"\x00\x01\x02", "audio/webm")
        self.assertEqual(out, "hello world")
        self.assertIn("model", captured)
        # File tuple shape: (filename, bytes, mime)
        self.assertEqual(captured["file"], ("audio.webm", b"\x00\x01\x02", "audio/webm"))

    def test_transcribe_quota_raises_quota_error(self) -> None:
        from medai_api.speech import SpeechQuotaError

        provider = self._build()

        class _Transcriptions:
            def create(self, **kwargs):
                raise _RateLimited("rate limit")

        class _Audio:
            transcriptions = _Transcriptions()

        class _Client:
            audio = _Audio()

        provider._client = _Client()
        with self.assertRaises(SpeechQuotaError):
            provider.transcribe(b"x", "audio/webm")

    def test_transcribe_other_error_propagates(self) -> None:
        provider = self._build()

        class _Transcriptions:
            def create(self, **kwargs):
                raise ValueError("bad input")

        class _Audio:
            transcriptions = _Transcriptions()

        class _Client:
            audio = _Audio()

        provider._client = _Client()
        with self.assertRaises(ValueError):
            provider.transcribe(b"x", "audio/webm")

    def test_synthesize_returns_bytes(self) -> None:
        provider = self._build()

        class _Resp:
            def read(self) -> bytes:
                return b"mp3-audio"

        class _Speech:
            def create(self, **kwargs):
                return _Resp()

        class _Audio:
            speech = _Speech()

        class _Client:
            audio = _Audio()

        provider._client = _Client()
        self.assertEqual(provider.synthesize("hi", voice="nova"), b"mp3-audio")

    def test_synthesize_quota_raises_quota_error(self) -> None:
        from medai_api.speech import SpeechQuotaError

        provider = self._build()

        class _Speech:
            def create(self, **kwargs):
                raise _RateLimited("429")

        class _Audio:
            speech = _Speech()

        class _Client:
            audio = _Audio()

        provider._client = _Client()
        with self.assertRaises(SpeechQuotaError):
            provider.synthesize("x")


# ── DisabledSpeechProvider ──────────────────────────────────────────────────


class DisabledSpeechProviderTest(unittest.TestCase):
    def test_transcribe_raises(self) -> None:
        from medai_api.speech import DisabledSpeechProvider, SpeechUnavailableError

        with self.assertRaises(SpeechUnavailableError):
            DisabledSpeechProvider().transcribe(b"", "audio/webm")

    def test_synthesize_raises(self) -> None:
        from medai_api.speech import DisabledSpeechProvider, SpeechUnavailableError

        with self.assertRaises(SpeechUnavailableError):
            DisabledSpeechProvider().synthesize("hi")


# ── Factory ─────────────────────────────────────────────────────────────────


class GetSpeechProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        from medai_api import speech as _sp

        _sp._provider = None
        self.addCleanup(lambda: setattr(_sp, "_provider", None))

    def test_default_is_openai_compatible(self) -> None:
        with patch.dict(os.environ, {"SPEECH_API_KEY": "k"}):
            os.environ.pop("SPEECH_PROVIDER", None)
            from medai_api.speech import OpenAICompatibleSpeechProvider, get_speech_provider

            self.assertIsInstance(get_speech_provider(), OpenAICompatibleSpeechProvider)

    def test_explicit_disabled(self) -> None:
        with patch.dict(os.environ, {"SPEECH_PROVIDER": "disabled"}):
            from medai_api.speech import DisabledSpeechProvider, get_speech_provider

            self.assertIsInstance(get_speech_provider(), DisabledSpeechProvider)

    def test_provider_is_memoised(self) -> None:
        with patch.dict(os.environ, {"SPEECH_PROVIDER": "disabled"}):
            from medai_api.speech import get_speech_provider

            self.assertIs(get_speech_provider(), get_speech_provider())


if __name__ == "__main__":
    unittest.main()
