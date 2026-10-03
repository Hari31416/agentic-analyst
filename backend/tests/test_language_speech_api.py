from __future__ import annotations

import hashlib
import io
import json
import wave
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.api import languages as language_api
from app.config import Settings
from app.language import speech
from app.main import app


def _wav(seconds: float = 0.2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\0\0" * int(16_000 * seconds))
    return buffer.getvalue()


def test_local_model_requires_manifest_and_hashes(tmp_path) -> None:
    payloads = {name: ("asset-" + name).encode() for name in speech._MODEL_FILES}
    for name, content in payloads.items():
        (tmp_path / name).write_bytes(content)
    manifest = {
        "model_id": speech._PROVIDER_MODEL,
        "revision": speech._PROVIDER_REVISION,
        "sha256": {
            name: hashlib.sha256(content).hexdigest()
            for name, content in payloads.items()
        },
    }
    manifest_path = tmp_path / "analyst-speech-model.json"
    manifest_path.write_text(json.dumps(manifest))
    assert speech.validate_model_assets(tmp_path)
    (tmp_path / "tokenizer.json").write_text("tampered")
    assert not speech.validate_model_assets(tmp_path)


def test_decode_audio_rejects_invalid_and_overlong_audio() -> None:
    with pytest.raises(ValueError, match="could not be decoded"):
        speech.decode_audio(b"garbage", max_duration_seconds=1)
    with pytest.raises(OverflowError, match="maximum duration"):
        speech.decode_audio(_wav(1.2), max_duration_seconds=1)


def test_capabilities_report_missing_local_assets(monkeypatch) -> None:
    settings = Settings(speech_model_path=None)
    monkeypatch.setattr(speech, "_provider", None)
    status = speech.capabilities(settings)
    assert status["stt"]["available"] is False
    assert status["translation"]["available"] is False
    assert status["tts"]["available"] is False
    assert {item["tag"] for item in status["languages"]} == {"en-IN", "hi-IN"}


def test_transcription_endpoint_bounds_upload_and_unavailable(monkeypatch) -> None:
    settings = Settings(speech_max_upload_bytes=16)
    monkeypatch.setattr(language_api, "get_settings", lambda: settings)
    with TestClient(app) as client:
        too_large = client.post(
            "/api/languages/transcribe", files={"audio": ("voice.wav", b"x" * 17)}
        )
        assert too_large.status_code == 413

        monkeypatch.setattr(language_api, "get_settings", lambda: Settings())
        monkeypatch.setattr(language_api, "transcribe_audio", _raise_unavailable)
        unavailable = client.post(
            "/api/languages/transcribe", files={"audio": ("voice.wav", _wav())}
        )
        assert unavailable.status_code == 503


async def _raise_unavailable(*args) -> dict[str, object]:
    raise LookupError("Local speech recognition is unavailable")


def test_transcription_endpoint_maps_decode_and_timeout_errors(monkeypatch) -> None:
    monkeypatch.setattr(language_api, "get_settings", lambda: Settings())
    with TestClient(app) as client:
        monkeypatch.setattr(language_api, "transcribe_audio", _raise_bad_audio)
        invalid = client.post(
            "/api/languages/transcribe", files={"audio": ("bad.wav", b"bad")}
        )
        assert invalid.status_code == 422
        monkeypatch.setattr(language_api, "transcribe_audio", _raise_timeout)
        timed_out = client.post(
            "/api/languages/transcribe", files={"audio": ("voice.wav", _wav())}
        )
        assert timed_out.status_code == 504


async def _raise_bad_audio(*args) -> dict[str, object]:
    raise ValueError("Audio file could not be decoded")


async def _raise_timeout(*args) -> dict[str, object]:
    raise TimeoutError


@pytest.mark.asyncio
async def test_admission_allows_one_waiter_and_rejects_third(monkeypatch) -> None:
    monkeypatch.setattr(speech, "_admitted_requests", 0)
    monkeypatch.setattr(speech, "_worker_may_still_be_running", False)
    monkeypatch.setattr(speech, "_speech_semaphore", asyncio.Semaphore(1))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def held(*args) -> dict[str, object]:
        entered.set()
        await release.wait()
        return {"text": "ok"}

    monkeypatch.setattr(speech, "_run_transcription", held)
    first = asyncio.create_task(speech.transcribe_audio(b"audio", None))
    await entered.wait()
    second = asyncio.create_task(speech.transcribe_audio(b"audio", None))
    await asyncio.sleep(0)
    with pytest.raises(speech.QueueFullError):
        await speech.transcribe_audio(b"audio", None)
    release.set()
    assert await first == {"text": "ok"}
    assert await second == {"text": "ok"}
    assert speech._admitted_requests == 0


@pytest.mark.asyncio
async def test_provider_timeout_closes_admission_until_process_restart(
    monkeypatch,
) -> None:
    from indic_language_utils.errors import ProviderTimeoutError

    class TimedOutProvider:
        async def transcribe_batch(self, *args, **kwargs):
            raise ProviderTimeoutError("timed out")

    monkeypatch.setattr(speech, "_admitted_requests", 0)
    monkeypatch.setattr(speech, "_worker_may_still_be_running", False)
    monkeypatch.setattr(speech, "_speech_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(speech, "get_speech_provider", lambda *args: TimedOutProvider())
    monkeypatch.setattr(speech, "get_settings", lambda: Settings())
    monkeypatch.setattr(
        speech,
        "decode_audio",
        lambda *args, **kwargs: speech.DecodedAudio(_wav(), 0.2),
    )
    with pytest.raises(TimeoutError):
        await speech.transcribe_audio(b"audio", None)
    with pytest.raises(speech.QueueFullError, match="recovering"):
        await speech.transcribe_audio(b"audio", None)
