import re
from pathlib import PurePosixPath


def validate_key(key: str) -> None:
    parts = PurePosixPath(key).parts
    if (
        not parts
        or parts[0] not in {"originals", "derived"}
        or len(parts) < 2
        or "/".join(parts) != key
        or any(
            not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,254}", p) or p in {".", ".."}
            for p in parts
        )
    ):
        raise ValueError("unsafe storage key")
