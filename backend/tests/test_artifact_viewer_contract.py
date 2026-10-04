from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.artifacts import artifact_kind
from app.contracts import FinalAnswer
from app.db.models import Artifact, Run
from app.evidence.validation import validate_answer


@pytest.mark.parametrize(
    "name,media,expected",
    [
        ("plot.png", "image/png", "image"),
        ("figure.svg", "image/svg+xml", "image"),
        ("figure.webp", "image/webp", "image"),
        ("plot.html", "text/html; charset=UTF-8", "html"),
        ("plot.htm", "application/octet-stream", "html"),
        ("rows.csv", "text/csv; charset=utf-8", "table"),
        ("rows.xlsx", "application/octet-stream", "table"),
        ("rows.parquet", "application/octet-stream", "table"),
        ("chart.json", "application/json", "chart"),
    ],
)
def test_viewer_kind(name, media, expected):
    assert artifact_kind(Artifact(display_name=name, media_type=media)) == expected


@pytest.mark.parametrize(
    "syntax", ["[Result](artifact:{id})", "![Plot](artifact:{id})", "[artifact:{id}]"]
)
def test_artifact_mentions_require_declared_accessible_ids(syntax):
    identity = str(uuid4())
    run = Run(
        id=str(uuid4()), thread_id=str(uuid4()), selected_source_ids=[], config={}
    )
    artifact = Artifact(id=identity, run_id=run.id, durable=True)

    class Session:
        def get(self, model, key):
            return {(Artifact, identity): artifact, (Run, run.id): run}.get(
                (model, key)
            )

    session = Session()
    validate_answer(
        session,
        run,
        FinalAnswer(text=syntax.format(id=identity), artifact_ids=[identity]),
    )
    validate_answer(
        session,
        run,
        FinalAnswer(text=syntax.format(id=identity.upper()), artifact_ids=[identity]),
    )
    with pytest.raises(ValueError, match="inline"):
        validate_answer(session, run, FinalAnswer(text=syntax.format(id=identity)))
    other = SimpleNamespace(thread_id=str(uuid4()), selected_source_ids=[], config={})
    with pytest.raises(ValueError, match="another thread"):
        validate_answer(
            session,
            other,
            FinalAnswer(text=syntax.format(id=identity), artifact_ids=[identity]),
        )


@pytest.mark.parametrize("reference", ["not-an-id", "../../secret", str(uuid4())])
def test_unknown_embed_is_rejected(reference):
    run = Run(selected_source_ids=[], config={})
    with pytest.raises(ValueError, match="declared artifact"):
        validate_answer(None, run, FinalAnswer(text=f"![Plot](artifact:{reference})"))
