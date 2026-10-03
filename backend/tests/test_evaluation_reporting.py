import csv
import json

import pytest

from evaluation.reporting import write_reports
from evaluation.review import apply_review, export_review


def report():
    return {
        "schema_version": 1,
        "experiment_id": "exp-1",
        "identity": {"model": "local"},
        "cases": [
            {"case_id": "en1", "language": "en", "rubric": {"answer": "<script>"}},
            {"case_id": "hi1", "language": "hi-IN", "rubric": {}},
        ],
        "trials": [
            {
                "case_id": "en1",
                "repetition": 0,
                "status": "failed",
                "run_id": "r1",
                "elapsed_seconds": 2,
                "tokens": 10,
                "model_calls": 2,
                "answer_text": "=1+1",
                "metrics": [
                    {
                        "code": "numeric",
                        "status": "fail",
                        "details": "<script>alert(1)</script>",
                    }
                ],
                "artifact_ids": ["a/1"],
            },
            {
                "case_id": "hi1",
                "repetition": 0,
                "status": "passed",
                "elapsed_seconds": 4,
                "tokens": 20,
                "model_calls": 1,
                "answer_text": "उत्तर",
                "metrics": [],
                "artifact_ids": [],
            },
        ],
    }


def test_report_writes_escaped_html_safe_csv_and_language_cohorts(tmp_path):
    paths = write_reports(report(), tmp_path)
    rendered = paths["html"].read_text()
    assert "&lt;script&gt;" in rendered
    assert "<script>" not in rendered
    assert "/api/runs/r1/audit" in rendered
    data = json.loads(paths["json"].read_text())
    assert data["summary"]["cohorts"]["Hindi"]["count"] == 1
    assert data["summary"]["cohorts"]["English"]["failures"] == 1
    with paths["csv"].open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["answer_text"] == "'=1+1"


def test_review_import_ties_to_exact_trial_and_preserves_metrics():
    original = report()
    template = export_review(original)
    row = template["reviews"][0]
    row.update(
        reviewers=["reviewer-a"],
        provenance="synthetic fixture review",
        rating="incorrect",
        comment="Check arithmetic",
    )
    updated = apply_review(original, template)
    reviewed = updated["trials"][0]
    assert reviewed["review_status"] == "reviewed"
    assert reviewed["human_review"]["rating"] == "incorrect"
    assert reviewed["metrics"] == original["trials"][0]["metrics"]


def test_review_rejects_other_trial_answer_and_duplicate_rows():
    template = export_review(report())
    template["reviews"][0].update(
        reviewers=["r"], provenance="fixture", rating="ok", comment=""
    )
    wrong = report()
    wrong["experiment_id"] = "another-exp"
    with pytest.raises(ValueError, match="experiment"):
        apply_review(wrong, template)
    altered = report()
    altered["trials"][0]["answer_text"] = "changed"
    with pytest.raises(ValueError, match="hash"):
        apply_review(altered, template)
    duplicate = export_review(report())
    duplicate["reviews"] = [duplicate["reviews"][0], duplicate["reviews"][0]]
    with pytest.raises(ValueError, match="duplicate"):
        apply_review(report(), duplicate)
