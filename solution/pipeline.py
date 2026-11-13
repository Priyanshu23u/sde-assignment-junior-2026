"""
Unified pipeline coordinator orchestrating incremental ingestion and reporting.
"""
from __future__ import annotations

import logging
from pathlib import Path

from solution.config import DATABASE_PATH
from solution.database import get_connection, init_db
from solution.ingest_events import ingest_event_file
from solution.ingest_leads import ingest_lead_file
from solution.reports import generate_reports
from solution.schema import SchemaRegistry

logger = logging.getLogger("solution.pipeline")


class Pipeline:
    """End-to-end pipeline runner."""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path or DATABASE_PATH)
        self.schema_registry = SchemaRegistry()
        init_db(self.db_path)

    def ingest_drop(self, drop_path: Path | str) -> dict[str, int]:
        """
        Ingests a single drop directory containing leads/ and events/.
        Processes files in strictly deterministic order:
        - Drops in ingestion order
        - Files in alphabetical filename order
        - Rows in file order
        """
        drop_dir = Path(drop_path)
        if not drop_dir.exists():
            raise FileNotFoundError(f"Drop directory not found: {drop_dir}")

        conn = get_connection(self.db_path)
        total_leads_processed = 0
        total_events_processed = 0

        try:
            # 1. Ingest Leads
            leads_base = drop_dir / "leads"
            if leads_base.exists():
                # Walk workflows in sorted order
                for wf_dir in sorted(leads_base.iterdir()):
                    if wf_dir.is_dir():
                        wf_id = wf_dir.name
                        # Files in filename order per spec
                        for csv_file in sorted(wf_dir.glob("*.csv")):
                            # Canonical relative path for idempotency tracking
                            rel_path = f"{drop_dir.name}/leads/{wf_id}/{csv_file.name}"
                            stats = ingest_lead_file(
                                conn=conn,
                                file_path=csv_file,
                                workflow_id=wf_id,
                                schema_registry=self.schema_registry,
                                drop_relative_path=rel_path,
                            )
                            total_leads_processed += stats.get("non_blank_rows", 0)

            # 2. Ingest Events
            events_base = drop_dir / "events"
            if events_base.exists():
                for jsonl_file in sorted(events_base.glob("*.jsonl")):
                    rel_path = f"{drop_dir.name}/events/{jsonl_file.name}"
                    stats = ingest_event_file(
                        conn=conn,
                        file_path=jsonl_file,
                        schema_registry=self.schema_registry,
                        drop_relative_path=rel_path,
                    )
                    total_events_processed += stats.get("non_blank_rows", 0)

        finally:
            conn.close()

        return {
            "leads_rows": total_leads_processed,
            "events_lines": total_events_processed,
        }

    def generate_reports(self, output_dir: Path | str) -> None:
        """Generates all 5 output reports into output_dir."""
        out_path = Path(output_dir)
        conn = get_connection(self.db_path)
        try:
            generate_reports(
                conn=conn,
                output_dir=out_path,
                schema_registry=self.schema_registry,
            )
        finally:
            conn.close()

