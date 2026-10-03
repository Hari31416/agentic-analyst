"""Language capability and transient speech endpoints."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.config import get_settings
from app.language.speech import capabilities, transcribe_audio

router = APIRouter(prefix="/api/languages", tags=["languages"])


@router.get("/capabilities")
def language_capabilities() -> dict[str, object]:
    return capabilities()


@router.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...), language: str | None = Form(default=None)
) -> dict[str, object]:
    settings = get_settings()
    try:
        data = await audio.read(settings.speech_max_upload_bytes + 1)
    finally:
        await audio.close()
    if not data:
        raise HTTPException(422, "Audio upload is empty")
    if len(data) > settings.speech_max_upload_bytes:
        raise HTTPException(413, "Audio upload exceeds the configured size limit")
    try:
        return await transcribe_audio(data, language)
    except LookupError as exc:
        raise HTTPException(503, str(exc)) from exc
    except OverflowError as exc:
        raise HTTPException(413, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(504, "Speech recognition timed out") from exc
    except Exception as exc:
        raise HTTPException(502, "Speech recognition failed") from exc
