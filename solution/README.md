# Assignment A Solution: Interaction Ledger & Reporting Pipeline

Production-grade, idempotent, append-only interaction ledger and reporting pipeline for Agentic Universe outbound voice campaigns.

## Overview

The pipeline processes messy, multi-format lead uploads (CSV) and voice agent telephony event streams (JSONL), loading them incrementally into an append-only interaction ledger backed by SQLite (WAL mode). It guarantees:

1. **Exact Metrics & Contract Compliance:** Generates all 5 output reports (`batch_summary.csv`, `daily_overview.csv`, `lead_report.csv`, `rejections.csv`, `data_quality.json`) formatted exactly per specification.
2. **Deterministic Rejection Order:** Walks schema fields in strict definition order, classifying errors into `missing_required:<field_name>` or `invalid_phone`, tracking optional field warnings separately.
3. **Robust Duplicate Merging:** First valid row permanently anchors the lead's `batch_id`; subsequent arrivals merge on `(workflow_id, canonical_phone)` with last-write-wins updates for non-null fields only.
4. **Append-Only Event Ledger & Dynamic Orphan Resolution:** Events are never deleted or modified. Events arriving before their lead are retained as unlinked ledger records and automatically resolve once the lead arrives in a subsequent drop.
5. **Supervisor Override Precedence:** Effective disposition selects the latest valid `disposition.override` by occurred time; if none, the latest valid `disposition.set`. Unknown codes from outside the catalogue are counted in data quality and ignored.
6. **Zero-PII Storage at Rest:** Plaintext phone numbers and customer names are never stored unencrypted. Lookups and joins use deterministic HMAC-SHA256 blind indexing (`phone_hash`), while values at rest are protected via Authenticated Encryption (AEAD). Masked phone numbers (`+91******XXXX`) are stored for report generation.
7. **Complete Idempotency:** Ingestion tracks file checksums and relative paths. Re-running drops is a safe, instantaneous no-op.

---

## Directory Structure

```
solution/
├── __init__.py           # Package marker
├── cli.py                # Command-line interface (ingest, report)
├── config.py             # System configuration, paths, IST timezone, defaults
├── crypto.py             # PII protection: HMAC blind indexing, AEAD encryption, masking
├── database.py           # Database connection pool, DDL schemas, WAL mode PRAGMAs
├── ingest_events.py      # Event file parsing, deduplication, ledger storage
├── ingest_leads.py       # Lead file ingestion, validation, duplicate merging
├── pipeline.py           # Pipeline coordinator orchestrating multi-file drops
├── reports.py            # Aggregators and report generators for all 5 outputs
└── schema.py             # Schema registry, header normalizer, field parsers
```

---

## Quickstart

### 1. Requirements
Python 3.11+ (Standard Library only; optional `pytest` for running test suite).

### 2. Ingestion & Reporting Commands

Run via root `run.py` or module invocation `python -m solution.cli`:

```bash
# Ingest Day 1
python run.py ingest data/drop_1

# Ingest Day 2
python run.py ingest data/drop_2

# Generate Reports
python run.py report --out reports/
```

### 3. Run Tests

```bash
python -m pytest -v
```
