"""Bounded local speech-to-text adapter."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import threading
import wave
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import Settings, get_settings

TARGET_RATE = 16_000
_provider: Any | None = None
_admission_lock = threading.Lock()
_admitted_requests = 0
_admission_capacity = 2
_speech_semaphore = asyncio.Semaphore(1)
_worker_may_still_be_running = False
_PROVIDER_MODEL = "Systran/faster-whisper-tiny"
_PROVIDER_REVISION = "d90ca5fe260221311c53c58e660288d3deb8d356"
_MODEL_FILES = ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")


@dataclass(frozen=True)
class DecodedAudio:
    wav_bytes: bytes
    duration_seconds: float


def decode_audio(data: bytes, *, max_duration_seconds: int) -> DecodedAudio:
    """Decode one uploaded audio stream to bounded mono 16 kHz PCM WAV."""
    try:
        import av
    except ImportError as exc:
        raise RuntimeError(
            "Audio decoding is unavailable (PyAV is not installed)"
        ) from exc

    try:
        source = av.open(io.BytesIO(data), mode="r")
        streams = source.streams.audio
        if not streams:
            raise ValueError("The uploaded file has no audio stream")
        stream = streams[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=TARGET_RATE)
        output = bytearray()
        sample_limit = max_duration_seconds * TARGET_RATE
        samples = 0
        for frame in source.decode(stream):
            for converted in resampler.resample(frame):
                chunk = bytes(converted.planes[0])[: converted.samples * 2]
                frame_samples = len(chunk) // 2
                samples += frame_samples
                if samples > sample_limit:
                    raise OverflowError("Audio exceeds the maximum duration")
                output.extend(chunk)
        for converted in resampler.resample(None):
            chunk = bytes(converted.planes[0])[: converted.samples * 2]
            samples += len(chunk) // 2
            if samples > sample_limit:
                raise OverflowError("Audio exceeds the maximum duration")
            output.extend(chunk)
        source.close()
    except OverflowError:
        raise
    except Exception as exc:
        raise ValueError("Audio file could not be decoded") from exc
    if samples == 0:
        raise ValueError("Audio file contains no decodable samples")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(TARGET_RATE)
        wav.writeframes(output)
    return DecodedAudio(buffer.getvalue(), samples / TARGET_RATE)


def _build_provider(settings: Settings) -> Any | None:
    model_path = settings.speech_model_path
    if model_path is None or not validate_model_assets(model_path):
        return None
    try:
        from indic_language_utils.stt.whisper import (
            FasterWhisperSTTConfig,
            FasterWhisperSTTProvider,
        )

        return FasterWhisperSTTProvider(
            FasterWhisperSTTConfig(
                model_size_or_path=str(model_path),
                device="cpu",
                compute_type="int8",
                cpu_threads=settings.speech_cpu_threads,
                num_workers=1,
                timeout_seconds=settings.speech_timeout_seconds,
                max_concurrency=1,
                beam_size=1,
                retry_policy=_no_retry_policy(),
            )
        )
    except (ImportError, OSError):
        return None


def _no_retry_policy() -> Any:
    from indic_language_utils.retry import RetryPolicy

    return RetryPolicy(max_attempts=1)


@lru_cache(maxsize=4)
def _verified_asset_fingerprint(
    root: str,
    manifest_mtime: int,
    manifest_size: int,
    fingerprint: tuple[tuple[str, int, int], ...],
) -> bool:
    del manifest_mtime, manifest_size, fingerprint
    path = Path(root)
    manifest_path = path / "analyst-speech-model.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("model_id") != _PROVIDER_MODEL
            or manifest.get("revision") != _PROVIDER_REVISION
            or not isinstance(manifest.get("sha256"), dict)
        ):
            return False
        for name in _MODEL_FILES:
            expected = manifest["sha256"].get(name)
            file_path = path / name
            if (
                not isinstance(expected, str)
                or len(expected) != 64
                or not file_path.is_file()
                or file_path.is_symlink()
            ):
                return False
            digest = hashlib.sha256()
            with file_path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != expected.lower():
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def validate_model_assets(model_path: Path) -> bool:
    try:
        root = model_path.resolve(strict=True)
        manifest = root / "analyst-speech-model.json"
        files = tuple(
            (name, (root / name).stat().st_mtime_ns, (root / name).stat().st_size)
            for name in _MODEL_FILES
        )
        stat = manifest.stat()
        if not root.is_dir() or manifest.is_symlink():
            return False
        return _verified_asset_fingerprint(
            str(root), stat.st_mtime_ns, stat.st_size, files
        )
    except OSError:
        return False


def get_speech_provider(settings: Settings | None = None) -> Any | None:
    global _provider
    if _provider is None:
        _provider = _build_provider(settings or get_settings())
    return _provider


def set_speech_provider_for_tests(provider: Any | None) -> None:
    global _provider
    _provider = provider


def capabilities(settings: Settings | None = None) -> dict[str, object]:
    settings = settings or get_settings()
    from app.language.text import configured_languages

    langs = configured_languages(settings.supported_languages)
    available = get_speech_provider(settings) is not None
    return {
        "languages": langs,
        "audio_limits": {
            "max_upload_bytes": settings.speech_max_upload_bytes,
            "max_duration_seconds": settings.speech_max_duration_seconds,
        },
        "stt": {
            "available": available,
            "provider": "faster_whisper" if available else None,
            "model": str(settings.speech_model_path) if available else None,
            "reason": (
                None
                if available
                else "Configure SPEECH_MODEL_PATH to an installed local model directory"
            ),
        },
        "translation": {
            "available": False,
            "reason": "No translation provider configured",
        },
        "tts": {"available": False, "reason": "No TTS provider configured"},
    }


async def transcribe_audio(data: bytes, language: str | None) -> dict[str, object]:
    global _admitted_requests
    with _admission_lock:
        if _admitted_requests >= _admission_capacity:
            raise QueueFullError("Speech service is busy; retry shortly")
        _admitted_requests += 1
    try:
        async with _speech_semaphore:
            if _worker_may_still_be_running:
                raise QueueFullError(
                    "Speech service is recovering from a timed out transcription"
                )
            return await _run_transcription(data, language)
    finally:
        with _admission_lock:
            _admitted_requests -= 1


class QueueFullError(RuntimeError):
    """The bounded active-plus-waiting speech capacity is full."""


async def _run_transcription(data: bytes, language: str | None) -> dict[str, object]:
    global _worker_may_still_be_running
    from indic_language_utils.languages import DEFAULT_LANGUAGE_REGISTRY

    settings = get_settings()
    provider = get_speech_provider()
    if provider is None:
        raise LookupError("Local speech recognition is unavailable")
    try:
        tag = DEFAULT_LANGUAGE_REGISTRY.normalize(language) if language else None
    except Exception as exc:
        raise ValueError("Unsupported language tag") from exc
    decoded = await asyncio.to_thread(
        decode_audio, data, max_duration_seconds=settings.speech_max_duration_seconds
    )
    # The package's provider enforces its own CPU concurrency and timeout bounds.
    try:
        results = await provider.transcribe_batch(
            (decoded.wav_bytes,),
            language=tag,
            audio_format="wav",
            sampling_rate=TARGET_RATE,
            request_id=str(uuid4()),
            with_timestamps=True,
        )
    except Exception as exc:
        from indic_language_utils.errors import ProviderTimeoutError

        if isinstance(exc, ProviderTimeoutError):
            # Faster-Whisper's native worker cannot be cancelled safely. Its
            # internal provider slot remains occupied until that thread exits.
            # Keep the endpoint closed after timeout to avoid accumulating work.
            _worker_may_still_be_running = True
            raise TimeoutError("Speech recognition timed out") from exc
        raise
    result = results[0]
    return {
        "text": result.text,
        "language": str(result.detected_language) if result.detected_language else None,
        "segments": [
            {"text": segment.text, "start": segment.start, "end": segment.end}
            for segment in result.segments
        ],
        "provider": "faster_whisper",
        "model": result.model_id or str(settings.speech_model_path),
        "duration_seconds": decoded.duration_seconds,
        "uncertainty": None,
    }
