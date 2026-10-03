"""Small deployment-owned multilingual glossary, used only for additive queries."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import cast


@lru_cache(maxsize=1)
def glossary() -> dict[str, list[str]]:
    # Kept outside agent logic: deployments can review/extend this asset.
    return cast(
        dict[str, list[str]],
        json.loads(Path(__file__).with_name("glossary.json").read_text()),
    )


def language_query_variants(query: str) -> tuple[list[str], dict[str, object]]:
    searchable = re.sub(
        r'```[\s\S]*?```|`[^`]*`|"[^"\n]*"|https?://\S+|[\w-]*[0-9०-९][\w-]*',
        " ",
        query,
    )
    additions: list[str] = []
    matched: list[str] = []
    for term, forms in glossary().items():
        if re.search(
            r"(?<![\w\u0900-\u097f-])" + re.escape(term) + r"(?![\w\u0900-\u097f-])",
            searchable,
        ):
            matched.append(term)
            additions.extend(
                form for form in forms if form.casefold() not in query.casefold()
            )
    # Numeral normalization supplies a search form, never changes source text or IDs.
    numeral_forms = [
        word.translate(str.maketrans("०१२३४५६७८९", "0123456789"))
        for word in re.findall(r"(?<![\w-])[०-९]+(?:[.,][०-९]+)*(?![\w-])", query)
    ]
    additions.extend(numeral_forms)
    extra = " ".join(dict.fromkeys(additions))
    variant = query + " " + extra if extra else query
    usable = variant != query and len(variant) <= 2000
    return ([variant] if usable else []), {
        "stage": "language_variants",
        "status": "ready" if usable else "skipped",
        "method": "reviewed-glossary-v1",
        "matched_terms": matched,
        "original_preserved": True,
        "model_calls": 0,
        "limitations": [
            "Glossary variants are search hints, not translations or resolved entity mappings.",
            "Unlisted Romanized Hindi and ambiguous words such as kal remain unresolved.",
        ],
    }
