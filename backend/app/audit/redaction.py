import json
import logging
import re
from typing import Any

SENSITIVE = re.compile(
    r"password|passwd|secret|token|credential|api.?key|authorization", re.I
)
PUBLIC_COUNTERS = frozenset(
    {
        "tokens",
        "total_tokens",
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "token_count",
        "token_budget",
    }
)
BEARER = re.compile(r"(?i)Bearer\s+\S+")
URL_CREDENTIALS = re.compile(r"([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@", re.I)


def configured_secrets() -> tuple[str, ...]:
    # Settings itself is cached; values are never logged or exposed as metadata.
    from app.config import get_settings

    settings = get_settings()
    values = []
    for name in type(settings).model_fields:
        item = getattr(settings, name)
        if hasattr(item, "get_secret_value"):
            raw = item.get_secret_value()
            if isinstance(raw, str) and len(raw) >= 8:
                values.append(raw)
    return tuple(sorted(set(values), key=len, reverse=True))


def contains_secret(value: Any) -> bool:
    if isinstance(value, bytes):
        return any(secret.encode() in value for secret in configured_secrets())
    if isinstance(value, str):
        return any(secret in value for secret in configured_secrets())
    if isinstance(value, dict):
        return any(contains_secret(k) or contains_secret(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(contains_secret(item) for item in value)
    return False


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            redact(str(k)): (
                "[redacted]"
                if SENSITIVE.search(str(k))
                and not (
                    str(k) in PUBLIC_COUNTERS
                    and isinstance(v, int)
                    and not isinstance(v, bool)
                )
                else redact(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        for secret in configured_secrets():
            value = value.replace(secret, "[redacted]")
        return URL_CREDENTIALS.sub(
            r"\1[redacted]@", BEARER.sub("Bearer [redacted]", value)
        )
    return value


class SafeJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Log only explicitly supplied operational fields. Exception bodies can include credentials.
        return json.dumps(
            redact(
                {
                    "level": record.levelname,
                    "message": record.getMessage(),
                    "run_id": getattr(record, "run_id", None),
                    "tool_call_id": getattr(record, "tool_call_id", None),
                    "source_id": getattr(record, "source_id", None),
                }
            ),
            ensure_ascii=False,
        )


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(SafeJsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
