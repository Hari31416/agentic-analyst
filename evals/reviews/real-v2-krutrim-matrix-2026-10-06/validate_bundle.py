"""Validate exact-answer review binding, comparison totals and HTML payload."""

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))
from evaluation.review import apply_review


def main():
    data = json.loads((OUT / "comparison.json").read_text())
    matrix = json.loads((ROOT / "evals/runs/real-v2-matrix/matrix.json").read_text())
    by_model = {m["model"]: m for m in matrix["models"]}
    cases = json.loads((ROOT / "evals/cases/real-v2.json").read_text())["cases"]
    ids = {c["id"] for c in cases}
    assert len(data["models"]) == 4
    assert len(ids) == 50
    diagnostics = []
    for model in data["models"]:
        path = Path(by_model[model["model"]]["reports"]["json"])
        raw_path = ROOT / "evals/runs/real-v2-matrix" / path.parent.name / path.name
        raw = json.loads(raw_path.read_text())
        assert (
            hashlib.sha256(raw_path.read_bytes()).hexdigest()
            == model["source_report_sha256"]
        )
        review = json.loads((OUT / model["output_slug"] / "review.json").read_text())
        attached = apply_review(raw, review)
        assert len(review["reviews"]) == 50
        assert {r["case_id"] for r in review["reviews"]} == ids
        assert (
            json.loads(
                (OUT / model["output_slug"] / "reviewed-report.json").read_text()
            )
            == attached
        )
        assert {c["case_id"] for c in model["cases"]} == ids
        assert (
            Counter(c["review"]["overall_task"] for c in model["cases"])
            == model["review_counts"]
        )
        assert sum(model["review_counts"].values()) == 50
        assert set(model["review_counts"]) <= {"complete", "partial", "failed"}
        assert (
            Counter(t["status"] for t in raw["trials"]) == model["auto_status_counts"]
        )
        assert sum("real-v2-added" in c["tags"] for c in model["cases"]) == 20
        assert all(c["review"]["comment"].strip() for c in model["cases"])
        for trial, original in zip(attached["trials"], raw["trials"]):
            assert trial["status"] == original["status"]
            assert trial["metrics"] == original["metrics"]
        diagnostics.append(
            {
                "model": model["model"],
                "trials": 50,
                "review_counts": model["review_counts"],
                "auto_status_counts": model["auto_status_counts"],
            }
        )
    html = (OUT / "index.html").read_text()
    embedded = re.search(r"const DATA = (.*?);\nconst models", html, re.S)
    assert embedded and json.loads(embedded.group(1)) == data
    assert "__DATA__" not in html
    assert "Real-v1" not in html
    assert "30 Cases" not in html
    assert "Qwen3.5-9B Excluded" not in html
    assert 'data-filter="added"' in html
    result = {
        "schema_version": 1,
        "models": 4,
        "trials": 200,
        "exact_answer_hashes_verified": 200,
        "checks": "Review schema/hash binding, unchanged automatic results, source hashes, all 50 IDs per model, cohort counts, totals and embedded HTML payload",
        "results": diagnostics,
    }
    (OUT / "validation.json").write_text(json.dumps(result, indent=2) + "\n")
    print("Validated 4 models, 200 exact-answer reviews and matching HTML payload")


if __name__ == "__main__":
    main()
