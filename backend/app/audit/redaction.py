import json
import logging
import re
from typing import Any

SENSITIVE = re.compile(
    r"password|passwd|secret|token|credential|api.?key|authorization", re.I
)
BEARER = re.compile(r"(?i)Bearer\s+\S+")
URL_CREDENTIALS = re.compile(r"([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@", re.I)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): "[redacted]" if SENSITIVE.search(str(k)) else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
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
