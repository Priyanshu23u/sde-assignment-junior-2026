"""
Tests verifying exact compliance with the Output Contract specified in README.md:
- Exact header strings
- Delimiters and line endings
- Sorting criteria
- Type and boolean formatting
- No PII in rejections.csv
"""
import csv
import json
from pathlib import Path
import pytest
from solution.database import init_db
from solution.pipeline import Pipeline


@pytest.fixture(scope="module")
def generated_reports(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("contract_test")
    db_file = tmp / "contract.db"
    rep_dir = tmp / "reports"

    pipeline = Pipeline(db_path=db_file)
    pipeline.ingest_drop("data/drop_1")
    pipeline.ingest_drop("data/drop_2")
    pipeline.generate_reports(rep_dir)
    return rep_dir


def test_batch_summary_contract(generated_reports: Path):
    path = generated_reports / "batch_summary.csv"
    assert path.exists()

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        expected_header = [
            "workflow_id",
            "batch_id",
            "total_leads",
            "yet_to_reach_out",
            "in_progress_telephony_failed",
            "in_progress_dialed",
            "completed",
        ]
        assert header == expected_header

        rows = list(reader)
        assert len(rows) > 0

        # Check sorting: sorted by workflow_id, batch_id
        keys = [(r[0], r[1]) for r in rows]
        assert keys == sorted(keys)

        # Invariant: sum of buckets == total_leads
        for r in rows:
            total = int(r[2])
            bucket_sum = int(r[3]) + int(r[4]) + int(r[5]) + int(r[6])
            assert total == bucket_sum


def test_daily_overview_contract(generated_reports: Path):
    path = generated_reports / "daily_overview.csv"
    assert path.exists()

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        expected_header = [
            "workflow_id",
            "date",
            "total_calls",
            "connected_calls",
            "connectivity_rate",
            "success_leads",
        ]
        assert header == expected_header

        rows = list(reader)
        assert len(rows) > 0

        # Check sorting: sorted by workflow_id, date
        keys = [(r[0], r[1]) for r in rows]
        assert keys == sorted(keys)

        # Check connectivity_rate format: exactly 4 decimals
        for r in rows:
            rate_str = r[4]
            assert "." in rate_str
            decimals = rate_str.split(".")[1]
            assert len(decimals) == 4
            tot = int(r[2])
            conn = int(r[3])
            assert tot > 0
            assert conn <= tot
            assert float(rate_str) == pytest.approx(conn / tot, abs=1e-4)


def test_lead_report_contract(generated_reports: Path):
    path = generated_reports / "lead_report.csv"
    assert path.exists()

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        expected_header = [
            "workflow_id",
            "external_ref",
            "batch_id",
            "phone_masked",
            "bucket",
            "total_calls",
            "connected_calls",
            "last_call_at",
            "current_disposition",
            "ever_success",
        ]
        assert header == expected_header

        rows = list(reader)
        assert len(rows) > 0

        # Check sorting: sorted by workflow_id, external_ref
        keys = [(r[0], r[1]) for r in rows]
        assert keys == sorted(keys)

        # Check phone masking & boolean formatting
        for r in rows:
            masked = r[3]
            assert masked.startswith("+91******")
            assert len(masked) == 13
            ever_success = r[9]
            assert ever_success in ("true", "false")
            bucket = r[4]
            assert bucket in (
                "yet_to_reach_out",
                "in_progress_telephony_failed",
                "in_progress_dialed",
                "completed",
            )


def test_rejections_contract(generated_reports: Path):
    path = generated_reports / "rejections.csv"
    assert path.exists()

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        expected_header = ["workflow_id", "batch_id", "row_number", "reason"]
        assert header == expected_header

        rows = list(reader)
        assert len(rows) > 0

        # Check sorting: sorted by workflow_id, batch_id, row_number
        keys = [(r[0], r[1], int(r[2])) for r in rows]
        assert keys == sorted(keys)

        # Check that NO PII is contained in rejections.csv
        for r in rows:
            # reason must match one of the allowed shapes
            reason = r[3]
            assert reason == "invalid_phone" or reason.startswith("missing_required:")


def test_data_quality_contract(generated_reports: Path):
    path = generated_reports / "data_quality.json"
    assert path.exists()

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    required_keys = [
        "lead_rows_total",
        "lead_rows_rejected",
        "rejections_by_reason",
        "duplicate_lead_rows_merged",
        "field_warnings",
        "events_total",
        "malformed_events",
        "duplicate_events_dropped",
        "orphan_events",
        "unknown_disposition_events",
    ]
    for k in required_keys:
        assert k in data, f"Missing key {k} in data_quality.json"

    assert isinstance(data["rejections_by_reason"], dict)
    assert sum(data["rejections_by_reason"].values()) == data["lead_rows_rejected"]

