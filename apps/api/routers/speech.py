"""Voice I/O (Phase 2) — Whisper transcription + OpenAI TTS (Pro-only).

Free tier is expected to use the browser-native Web Speech API on the
client; these routes gate access to the higher-quality server-side flow
behind `require_pro`.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from auth import AuthUser, require_pro

log = logging.getLogger("medai.speech")

router = APIRouter()


_SPEECH_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}


class TTSIn(BaseModel):
    text: Annotated[str, Field(min_length=1, max_length=4000)]
    voice: str = "alloy"


@router.post("/api/speech/transcribe")
async def speech_transcribe(
    audio: UploadFile = File(...),
    user: AuthUser = Depends(require_pro),  # noqa: B008 — FastAPI dep pattern
):
    """Transcribe an uploaded audio blob via the configured SpeechProvider."""
    from speech import SpeechQuotaError, SpeechUnavailableError, get_speech_provider

    try:
        data = await audio.read()
        if not data:
            raise HTTPException(status_code=400, detail="Empty audio upload")
        mime = audio.content_type or "audio/webm"
        filename = audio.filename or "audio.webm"
        provider = get_speech_provider()
        text = provider.transcribe(data, mime, filename=filename)
        return {"text": text}
    except HTTPException:
        raise
    except SpeechQuotaError as exc:
        raise HTTPException(
            status_code=429,
            detail={"code": "transcribe_quota", "message": str(exc)},
        ) from exc
    except SpeechUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "transcribe_unavailable", "message": str(exc)},
        ) from exc
    except Exception as exc:
        log.exception("transcription failed")
        raise HTTPException(
            status_code=502,
            detail={"code": "transcribe_failed", "message": str(exc)[:200]},
        ) from exc


@router.post("/api/speech/synthesize")
async def speech_synthesize(
    req: TTSIn,
    user: AuthUser = Depends(require_pro),
):
    """Synthesise an mp3 from `text` via the configured SpeechProvider."""
    from speech import SpeechQuotaError, SpeechUnavailableError, get_speech_provider

    voice = req.voice if req.voice in _SPEECH_VOICES else "alloy"
    try:
        provider = get_speech_provider()
        audio_bytes = provider.synthesize(req.text, voice=voice)
        return Response(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers={"Cache-Control": "no-store"},
        )
    except HTTPException:
        raise
    except SpeechQuotaError as exc:
        raise HTTPException(
            status_code=429,
            detail={"code": "synthesize_quota", "message": str(exc)},
        ) from exc
    except SpeechUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "synthesize_unavailable", "message": str(exc)},
        ) from exc
    except Exception as exc:
        log.exception("synthesis failed")
        raise HTTPException(
            status_code=502,
            detail={"code": "synthesize_failed", "message": str(exc)[:200]},
        ) from exc
