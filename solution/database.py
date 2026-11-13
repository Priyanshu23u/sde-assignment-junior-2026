"""
Database connection, schema management, and transactional helpers.
Backed by SQLite with Write-Ahead Logging (WAL) and memory caching.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from solution.config import DATABASE_PATH


DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS ingested_files (
        file_path TEXT PRIMARY KEY,
        file_hash TEXT NOT NULL,
        file_type TEXT NOT NULL,
        workflow_id TEXT,
        batch_id TEXT,
        non_blank_rows INTEGER DEFAULT 0,
        rejected_rows INTEGER DEFAULT 0,
        merged_duplicates INTEGER DEFAULT 0,
        field_warnings INTEGER DEFAULT 0,
        malformed_events INTEGER DEFAULT 0,
        duplicate_events INTEGER DEFAULT 0,
        ingested_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS rejections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        workflow_id TEXT NOT NULL,
        batch_id TEXT NOT NULL,
        row_number INTEGER NOT NULL,
        reason TEXT NOT NULL,
        file_path TEXT NOT NULL,
        UNIQUE(file_path, row_number)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS leads (
        lead_id INTEGER PRIMARY KEY AUTOINCREMENT,
        workflow_id TEXT NOT NULL,
        phone_hash TEXT NOT NULL,
        batch_id TEXT NOT NULL,
        external_ref TEXT NOT NULL,
        phone_masked TEXT NOT NULL,
        phone_encrypted TEXT NOT NULL,
        name_encrypted TEXT NOT NULL,
        raw_fields_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(workflow_id, phone_hash)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        event_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        workflow_id TEXT NOT NULL,
        call_id TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        occurred_at_ist TEXT NOT NULL,
        date_ist TEXT NOT NULL,
        phone_hash TEXT,
        phone_encrypted TEXT,
        payload_json TEXT NOT NULL,
        disposition_code TEXT,
        is_known_disposition INTEGER DEFAULT 1,
        file_path TEXT,
        ingested_at TEXT NOT NULL
    );
    """,
    # Optimized Indexes
    "CREATE INDEX IF NOT EXISTS idx_leads_wf_hash ON leads(workflow_id, phone_hash);",
    "CREATE INDEX IF NOT EXISTS idx_leads_wf_ref ON leads(workflow_id, external_ref);",
    "CREATE INDEX IF NOT EXISTS idx_leads_wf_batch ON leads(workflow_id, batch_id);",
    "CREATE INDEX IF NOT EXISTS idx_events_call_id ON events(call_id);",
    "CREATE INDEX IF NOT EXISTS idx_events_wf_hash ON events(workflow_id, phone_hash);",
    "CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);",
    "CREATE INDEX IF NOT EXISTS idx_events_date_ist ON events(workflow_id, date_ist);",
    "CREATE INDEX IF NOT EXISTS idx_rejections_sort ON rejections(workflow_id, batch_id, row_number);",
]


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """
    Creates an optimized SQLite connection with WAL journal mode,
    foreign keys, and 64MB cache.
    """
    path = Path(db_path or DATABASE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(
        str(path),
        timeout=60.0,
        isolation_level=None,  # Autocommit mode; we manage explicit transactions via contextmanager
    )
    conn.row_factory = sqlite3.Row
    
    # Performance and concurrency PRAGMAs
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA cache_size = -64000;")
    
    return conn


def init_db(db_path: Path | str | None = None) -> None:
    """Initialises all tables and indexes if they do not already exist."""
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE;")
        for stmt in DDL_STATEMENTS:
            conn.execute(stmt)
        conn.execute("COMMIT;")
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Context manager for ACID transactions."""
    conn.execute("BEGIN IMMEDIATE;")
    try:
        yield conn
        conn.execute("COMMIT;")
    except Exception:
        conn.execute("ROLLBACK;")
        raise

