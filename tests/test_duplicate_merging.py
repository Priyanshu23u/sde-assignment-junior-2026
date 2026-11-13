"""
Unit tests for duplicate lead merging, last-write-wins non-null updates, and batch ID persistence.
"""
import json
from pathlib import Path
import pytest
from solution.database import get_connection, init_db
from solution.ingest_leads import ingest_lead_file
from solution.schema import SchemaRegistry


@pytest.fixture
def test_db(tmp_path: Path):
    db_file = tmp_path / "test_leads.db"
    init_db(db_file)
    conn = get_connection(db_file)
    yield conn, tmp_path
    conn.close()


def test_duplicate_lead_merging(test_db):
    conn, tmp_path = test_db
    reg = SchemaRegistry()

    # Batch 1 creates the lead
    csv1 = tmp_path / "batch_001.csv"
    csv1.write_text(
        "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        "Aarav Sharma,+919876543210,MOT/2026/100001,2026-11-20,5000,hi\n",
        encoding="utf-8",
    )
    s1 = ingest_lead_file(conn, csv1, "motor_renewal", reg, "drop_1/leads/motor_renewal/batch_001.csv")
    assert s1["non_blank_rows"] == 1
    assert s1["rejected_rows"] == 0
    assert s1["merged_duplicates"] == 0

    lead = conn.execute("SELECT * FROM leads WHERE workflow_id = 'motor_renewal';").fetchone()
    assert lead["batch_id"] == "batch_001"
    assert lead["external_ref"] == "MOT/2026/100001"
    fields = json.loads(lead["raw_fields_json"])
    assert fields["premium_amount"] == 5000.0
    assert fields["preferred_language"] == "hi"

    # Batch 2 has a duplicate with updated amount and language = null
    csv2 = tmp_path / "batch_002.csv"
    csv2.write_text(
        "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        "Aarav Sharma,9876543210,MOT/2026/100001,2026-11-25,7500,\n",
        encoding="utf-8",
    )
    s2 = ingest_lead_file(conn, csv2, "motor_renewal", reg, "drop_1/leads/motor_renewal/batch_002.csv")
    assert s2["merged_duplicates"] == 1

    # Invariants check
    lead2 = conn.execute("SELECT * FROM leads WHERE workflow_id = 'motor_renewal';").fetchone()
    # 1. Permanent batch_id from first valid row
    assert lead2["batch_id"] == "batch_001"
    fields2 = json.loads(lead2["raw_fields_json"])
    # 2. Overwrites only non-null fields
    assert fields2["premium_amount"] == 7500.0
    assert fields2["policy_expiry"] == "2026-11-25"
    # 3. Empty/null field in duplicate does not overwrite existing value
    assert fields2["preferred_language"] == "hi"


def test_schema_field_defaults_new_and_duplicate_leads(test_db):
    conn, tmp_path = test_db
    reg = SchemaRegistry()

    # Lead 1: New lead created with null preferred_language -> receives default "en"
    csv1 = tmp_path / "defaults_001.csv"
    csv1.write_text(
        "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        "Priya Patel,+919123456780,MOT/2026/200001,2026-12-01,6000,\n",
        encoding="utf-8",
    )
    s1 = ingest_lead_file(conn, csv1, "motor_renewal", reg, "drop_1/leads/motor_renewal/defaults_001.csv")
    assert s1["non_blank_rows"] == 1
    assert s1["merged_duplicates"] == 0

    lead1 = conn.execute(
        "SELECT * FROM leads WHERE workflow_id = 'motor_renewal' AND external_ref = 'MOT/2026/200001';"
    ).fetchone()
    assert lead1["batch_id"] == "defaults_001"
    f1 = json.loads(lead1["raw_fields_json"])
    # Schema default "en" must be applied to null field
    assert f1["preferred_language"] == "en"

    # Duplicate 1: Later batch provides non-null preferred_language "ta" -> overwrites default "en"
    csv2 = tmp_path / "defaults_002.csv"
    csv2.write_text(
        "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        "Priya Patel,9123456780,MOT/2026/200001,2026-12-01,6000,ta\n",
        encoding="utf-8",
    )
    s2 = ingest_lead_file(conn, csv2, "motor_renewal", reg, "drop_1/leads/motor_renewal/defaults_002.csv")
    assert s2["merged_duplicates"] == 1

    lead1_dup = conn.execute(
        "SELECT * FROM leads WHERE workflow_id = 'motor_renewal' AND external_ref = 'MOT/2026/200001';"
    ).fetchone()
    # Batch ID must remain defaults_001 permanently
    assert lead1_dup["batch_id"] == "defaults_001"
    f1_dup = json.loads(lead1_dup["raw_fields_json"])
    # New non-null value "ta" overwrites previous default "en"
    assert f1_dup["preferred_language"] == "ta"

    # Duplicate 2: Another batch arrives with null preferred_language -> "ta" is preserved (not overwritten by default)
    csv3 = tmp_path / "defaults_003.csv"
    csv3.write_text(
        "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        "Priya Patel,9123456780,MOT/2026/200001,,6500,\n",
        encoding="utf-8",
    )
    s3 = ingest_lead_file(conn, csv3, "motor_renewal", reg, "drop_2/leads/motor_renewal/defaults_003.csv")
    assert s3["merged_duplicates"] == 1

    lead1_dup2 = conn.execute(
        "SELECT * FROM leads WHERE workflow_id = 'motor_renewal' AND external_ref = 'MOT/2026/200001';"
    ).fetchone()
    assert lead1_dup2["batch_id"] == "defaults_001"
    f1_dup2 = json.loads(lead1_dup2["raw_fields_json"])
    assert f1_dup2["preferred_language"] == "ta"
    assert f1_dup2["premium_amount"] == 6500.0
    assert f1_dup2["policy_expiry"] == "2026-12-01"

