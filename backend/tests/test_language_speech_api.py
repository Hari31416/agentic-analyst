from __future__ import annotations

import hashlib
import io
import json
import wave
import asyncio
import sys
from types import SimpleNamespace

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


def test_decoder_closes_container_after_decode_error(monkeypatch) -> None:
    class Source:
        streams = SimpleNamespace(
            audio=[
                SimpleNamespace(
                    codec_context=SimpleNamespace(channels=1, sample_rate=16_000),
                    rate=16_000,
                )
            ]
        )
        closed = False

        def decode(self, stream):
            raise RuntimeError("malformed packet")

        def close(self):
            self.closed = True

    source = Source()
    fake_av = SimpleNamespace(
        open=lambda *args, **kwargs: source,
        AudioResampler=lambda **kwargs: object(),
    )
    monkeypatch.setitem(sys.modules, "av", fake_av)
    with pytest.raises(speech.AudioInputError, match="could not be decoded"):
        speech.decode_audio(b"audio", max_duration_seconds=1)
    assert source.closed


@pytest.mark.asyncio
async def test_unsupported_configured_language_rejected_before_provider(
    monkeypatch,
) -> None:
    monkeypatch.setattr(speech, "_admitted_requests", 0)
    monkeypatch.setattr(speech, "_worker_may_still_be_running", False)
    monkeypatch.setattr(speech, "_speech_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(speech, "get_settings", lambda: Settings())

    def provider_must_not_be_loaded(*args):
        raise AssertionError("provider should not load for unsupported language")

    monkeypatch.setattr(speech, "get_speech_provider", provider_must_not_be_loaded)
    with pytest.raises(ValueError, match="not enabled"):
        await speech.transcribe_audio(b"audio", "ta-IN")


@pytest.mark.asyncio
async def test_transcription_response_marks_draft_uncertainty(monkeypatch) -> None:
    class Provider:
        async def transcribe_batch(self, *args, **kwargs):
            return (
                SimpleNamespace(
                    text="count 25000",
                    detected_language="en-IN",
                    model_id="tiny",
                    segments=(),
                ),
            )

    monkeypatch.setattr(speech, "get_settings", lambda: Settings())
    monkeypatch.setattr(speech, "get_speech_provider", lambda *args: Provider())
    monkeypatch.setattr(
        speech,
        "decode_audio",
        lambda *args, **kwargs: speech.DecodedAudio(_wav(), 0.2),
    )
    response = await speech._run_transcription(b"audio", "en-IN")
    assert response["text"] == "count 25000"
    assert "verify names, numbers" in response["uncertainty"]


def test_missing_optional_whisper_dependency_is_unavailable(
    monkeypatch, tmp_path
) -> None:
    from indic_language_utils.errors import MissingOptionalDependencyError
    import indic_language_utils.stt.whisper as whisper_module

    monkeypatch.setattr(speech, "_provider_failure", None)
    monkeypatch.setattr(speech, "validate_model_assets", lambda _path: True)

    class MissingProvider:
        def __init__(self, config):
            raise MissingOptionalDependencyError(
                "missing whisper", provider="faster_whisper"
            )

    monkeypatch.setattr(whisper_module, "FasterWhisperSTTProvider", MissingProvider)
    assert speech._build_provider(Settings(speech_model_path=tmp_path)) is None
    assert (
        speech._provider_failure == "Install the configured faster-whisper dependencies"
    )


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


@pytest.mark.asyncio
async def test_cancelled_provider_work_closes_admission(monkeypatch) -> None:
    class CancelledProvider:
        async def transcribe_batch(self, *args, **kwargs):
            raise asyncio.CancelledError

    monkeypatch.setattr(speech, "_admitted_requests", 0)
    monkeypatch.setattr(speech, "_worker_may_still_be_running", False)
    monkeypatch.setattr(speech, "_speech_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(
        speech, "get_speech_provider", lambda *args: CancelledProvider()
    )
    monkeypatch.setattr(speech, "get_settings", lambda: Settings())
    monkeypatch.setattr(
        speech, "decode_audio", lambda *args, **kwargs: speech.DecodedAudio(_wav(), 0.2)
    )
    with pytest.raises(asyncio.CancelledError):
        await speech.transcribe_audio(b"audio", "en-IN")
    assert speech._admitted_requests == 0
    with pytest.raises(speech.QueueFullError, match="recovering"):
        await speech.transcribe_audio(b"audio", "en-IN")


@pytest.mark.asyncio
async def test_provider_declaration_rejects_unavailable_speech_language(monkeypatch):
    from indic_language_utils.providers import CapabilityDeclaration, CapabilityId
    from indic_language_utils.languages import DEFAULT_LANGUAGE_REGISTRY

    class Provider:
        capabilities = (
            CapabilityDeclaration(
                CapabilityId.SPEECH_TO_TEXT,
                languages=frozenset({DEFAULT_LANGUAGE_REGISTRY.normalize("en-IN")}),
            ),
        )

    monkeypatch.setattr(
        speech, "get_settings", lambda: Settings(supported_languages=["brx-IN"])
    )
    monkeypatch.setattr(speech, "get_speech_provider", lambda *args: Provider())
    with pytest.raises(ValueError, match="not supported"):
        await speech._run_transcription(b"audio", "brx-IN")
