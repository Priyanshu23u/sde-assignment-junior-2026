"""
End-to-end integration tests for CLI, incremental pipeline, and idempotency.
"""
from pathlib import Path
import pytest
from solution.cli import main
from solution.pipeline import Pipeline


def test_pipeline_end_to_end_and_idempotency(tmp_path: Path):
    db_file = tmp_path / "e2e.db"
    rep_dir = tmp_path / "reports_e2e"

    pipeline = Pipeline(db_path=db_file)

    # Ingest drop 1
    res1 = pipeline.ingest_drop("data/drop_1")
    assert res1["leads_rows"] > 0
    assert res1["events_lines"] > 0

    # Ingest drop 2
    res2 = pipeline.ingest_drop("data/drop_2")
    assert res2["leads_rows"] > 0
    assert res2["events_lines"] > 0

    # Re-run drop 1 (should be skipped due to idempotency)
    res1_dup = pipeline.ingest_drop("data/drop_1")
    assert res1_dup["leads_rows"] == 0
    assert res1_dup["events_lines"] == 0

    # Generate reports
    pipeline.generate_reports(rep_dir)
    assert (rep_dir / "batch_summary.csv").exists()
    assert (rep_dir / "daily_overview.csv").exists()
    assert (rep_dir / "lead_report.csv").exists()
    assert (rep_dir / "rejections.csv").exists()
    assert (rep_dir / "data_quality.json").exists()


def test_cli_execution(tmp_path: Path):
    db_file = tmp_path / "cli.db"
    rep_dir = tmp_path / "reports_cli"

    # Test CLI ingest
    rc_ingest1 = main(["ingest", "data/drop_1", "--db", str(db_file)])
    assert rc_ingest1 == 0

    rc_ingest2 = main(["ingest", "data/drop_2", "--db", str(db_file)])
    assert rc_ingest2 == 0

    # Test CLI report
    rc_report = main(["report", "--out", str(rep_dir), "--db", str(db_file)])
    assert rc_report == 0

    assert (rep_dir / "data_quality.json").exists()


def test_cli_db_flag_positions_and_database_creation(tmp_path: Path):
    from solution.cli import build_parser
    import sqlite3

    parser = build_parser()

    # 1. Verify parser attribute behavior
    a_before = parser.parse_args(["--db", "before.db", "ingest", "data/drop_1"])
    assert getattr(a_before, "db", None) == "before.db"

    a_after = parser.parse_args(["ingest", "data/drop_1", "--db", "after.db"])
    assert getattr(a_after, "db", None) == "after.db"

    a_none = parser.parse_args(["ingest", "data/drop_1"])
    assert getattr(a_none, "db", None) is None

    # 2. End-to-end execution with --db placed BEFORE subcommand
    db_before = tmp_path / "cli_before.db"
    rep_before = tmp_path / "reports_before"
    assert not db_before.exists()

    rc1 = main(["--db", str(db_before), "ingest", "data/drop_1"])
    assert rc1 == 0
    assert db_before.exists(), "Target database file must be created when --db is before subcommand"

    conn1 = sqlite3.connect(db_before)
    leads_count1 = conn1.execute("SELECT COUNT(*) FROM leads;").fetchone()[0]
    conn1.close()
    assert leads_count1 > 0, "Target database must contain ingested leads"

    rc_rep1 = main(["--db", str(db_before), "report", "--out", str(rep_before)])
    assert rc_rep1 == 0
    assert (rep_before / "batch_summary.csv").exists()

    # 3. End-to-end execution with --db placed AFTER subcommand
    db_after = tmp_path / "cli_after.db"
    rep_after = tmp_path / "reports_after"
    assert not db_after.exists()

    rc2 = main(["ingest", "data/drop_1", "--db", str(db_after)])
    assert rc2 == 0
    assert db_after.exists(), "Target database file must be created when --db is after subcommand"

    conn2 = sqlite3.connect(db_after)
    leads_count2 = conn2.execute("SELECT COUNT(*) FROM leads;").fetchone()[0]
    conn2.close()
    assert leads_count2 == leads_count1, "Both flag positions must ingest identical lead rows"

    rc_rep2 = main(["report", "--out", str(rep_after), "--db", str(db_after)])
    assert rc_rep2 == 0
    assert (rep_after / "batch_summary.csv").exists()

