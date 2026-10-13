"""Registry setup must preserve credentials and only select a usable image."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def image_script(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts" / "build-analysis.py"
    spec = importlib.util.spec_from_file_location("analysis_image", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    image = f"hari31416/agentic-rag-analyst-runtime:py312-v1@sha256:{'a' * 64}"
    (tmp_path / ".env.example").write_text(f"SANDBOX_IMAGE={image}\n")
    (tmp_path / ".env").write_text("PRIVATE_KEY=keep-me\nSANDBOX_IMAGE=old\n")
    return module, image


def test_pull_uses_published_digest_and_preserves_env(image_script, monkeypatch):
    module, image = image_script
    calls = []
    monkeypatch.setattr(sys, "argv", ["build-analysis.py"])
    monkeypatch.setattr(module, "run", lambda *args: calls.append(args))
    module.main()
    assert calls == [("msb", "pull", image)]
    assert (
        module.ROOT / ".env"
    ).read_text() == f"PRIVATE_KEY=keep-me\nSANDBOX_IMAGE={image}\n"


def test_failed_pull_keeps_current_image(image_script, monkeypatch):
    module, _ = image_script
    monkeypatch.setattr(sys, "argv", ["build-analysis.py"])

    def fail(*args):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(module, "run", fail)
    with pytest.raises(RuntimeError, match="registry unavailable"):
        module.main()
    assert (module.ROOT / ".env").read_text().endswith("SANDBOX_IMAGE=old\n")


def test_publish_pins_registry_digest_for_both_platforms(image_script, monkeypatch):
    module, _ = image_script
    calls = []
    digest = f"sha256:{'b' * 64}"
    tag = "hari31416/agentic-rag-analyst-runtime:py312-v2"
    monkeypatch.setattr(sys, "argv", ["build-analysis.py", "--publish", "--tag", tag])

    def run(*args):
        calls.append(args)
        if args[0] == "docker":
            metadata = Path(args[args.index("--metadata-file") + 1])
            metadata.write_text(json.dumps({"containerimage.digest": digest}))

    monkeypatch.setattr(module, "run", run)
    module.main()
    assert calls[0][calls[0].index("--platform") + 1] == "linux/amd64,linux/arm64"
    assert calls[0][-2:] == ("--push", "infra")
    assert calls[1] == ("msb", "pull", f"{tag}@{digest}")
    assert (
        f"SANDBOX_IMAGE={tag}@{digest}\n" in (module.ROOT / ".env.example").read_text()
    )
