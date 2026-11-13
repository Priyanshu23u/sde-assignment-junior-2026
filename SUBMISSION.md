# Submission

## Candidate
- **Name:** Priyanshu Upadhyay
- **Assignment chosen:** A (Data) — Interaction Ledger & Reporting Pipeline
- **Time spent (honest estimate):** ~7h

---

## How to run

```bash
# 1. Clone the repository and enter the project root
git clone <repo-url>
cd sde-assignment-junior-2026

# 2. Create virtualenv (Python 3.11+ required; uses standard library only)
python -m venv .venv
# On macOS/Linux:
source .venv/bin/activate
# On Windows:
.venv\Scripts\activate

# 3. Install test dependency (pytest only)
pip install pytest

# 4. Generate synthetic test data
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data

# 5. Set PII encryption secret (optional; a default is provided for local dev)
# export AU_PII_SECRET="your-secret-key"   # Linux / macOS
# $env:AU_PII_SECRET = "your-secret-key"  # Windows PowerShell

# 6. Run ingestion and reporting
python run.py ingest data/drop_1
python run.py ingest data/drop_2
python run.py report --out reports/

# 7. Inspect output
# reports/batch_summary.csv
# reports/daily_overview.csv
# reports/lead_report.csv
# reports/rejections.csv
# reports/data_quality.json

# 8. Run the full test suite (30 tests)
python -m pytest -v

# 9. (Optional) benchmark large dataset
python assignment-a-data/tools/generate_data.py --seed 42 --scale large --out data_large
python benchmark.py
```

---

## Design

### Architecture

```
 CSV Lead Files ──┐
                  ├──► LeadIngester ──► SQLite WAL (append-only ledger)
 JSONL Event Files─┘        │
                             └── EventIngester
                                     │
                             ReportGenerator ──► 5 output files
```

The pipeline has three clean layers:

1. **Schema Layer** (`schema.py`): Schema registry that loads `*.json` and `dispositions.csv` files. Provides stateless parsing functions — `parse_phone`, `parse_date`, `parse_decimal`, `parse_enum`, `parse_timestamp_ist`, `normalize_header` — with no I/O dependencies.

2. **Ingestion Layer** (`ingest_leads.py`, `ingest_events.py`): Both modules work with an open `sqlite3.Connection`. They handle idempotency, validation, PII protection, and ledger insertion inside explicit atomic transactions.

3. **Report Layer** (`reports.py`): Pure aggregation logic over the ledger. Builds call-to-lead linkage in one pass, aggregates buckets and daily metrics in memory, and writes the five output files.

### Database Schema

```sql
-- PII is never stored in plaintext
CREATE TABLE leads (
    lead_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id    TEXT NOT NULL,
    phone_hash     TEXT NOT NULL,     -- deterministic HMAC-SHA256 blind index for joining
    batch_id       TEXT NOT NULL,     -- permanently set by first valid row
    external_ref   TEXT NOT NULL,
    phone_masked   TEXT NOT NULL,     -- "+91******XXXX" for reports
    phone_encrypted TEXT NOT NULL,   -- AEAD ciphertext
    name_encrypted  TEXT NOT NULL,   -- AEAD ciphertext
    raw_fields_json TEXT NOT NULL,   -- JSON blob of parsed/validated fields (no phone_hash/raw PII)
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    UNIQUE(workflow_id, phone_hash)
);

CREATE TABLE events (
    event_id              TEXT PRIMARY KEY,
    event_type            TEXT NOT NULL,
    workflow_id           TEXT NOT NULL,
    call_id               TEXT NOT NULL,
    occurred_at           TEXT NOT NULL,     -- raw form
    occurred_at_ist       TEXT NOT NULL,     -- normalised ISO-8601 IST
    date_ist              TEXT NOT NULL,     -- YYYY-MM-DD IST date for partitioning
    phone_hash            TEXT,              -- blind index; NULL for override events
    phone_encrypted       TEXT,
    payload_json          TEXT NOT NULL,
    disposition_code      TEXT,
    is_known_disposition  INTEGER DEFAULT 1, -- 0 = unknown, excluded from effective disposition
    file_path             TEXT,
    ingested_at           TEXT NOT NULL
);

CREATE TABLE rejections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT NOT NULL,
    batch_id    TEXT NOT NULL,
    row_number  INTEGER NOT NULL,
    reason      TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    UNIQUE(file_path, row_number)
);

CREATE TABLE ingested_files (
    file_path          TEXT PRIMARY KEY,
    file_hash          TEXT NOT NULL,     -- SHA-256 for idempotency
    file_type          TEXT NOT NULL,     -- "leads" | "events"
    workflow_id        TEXT,
    batch_id           TEXT,
    non_blank_rows     INTEGER,
    rejected_rows      INTEGER,
    merged_duplicates  INTEGER,
    field_warnings     INTEGER,
    malformed_events   INTEGER,
    duplicate_events   INTEGER,
    ingested_at        TEXT NOT NULL
);
```

**Why SQLite WAL?** WAL mode gives concurrent readers without blocking writers, and `synchronous=NORMAL` avoids `fsync` on every transaction while still guaranteeing full ACID. For a laptop pipeline that's strictly single-writer, this gives the best throughput.

### PII Protection Strategy

| Concern | Approach |
|---|---|
| Join events to leads by phone | Deterministic HMAC-SHA256 `phone_hash` (blind index). Same key ⇒ same hash, enabling exact-match without storing plaintext. |
| Store names and phone numbers | Encrypt-then-MAC (AEAD): random 16-byte nonce, HMAC-SHA256 PRF CTR keystream, HMAC-SHA256 authentication tag. Tampering is detected on decryption. |
| Report masking | `+91******XXXX` pre-computed at ingestion and stored. Reports never need to decrypt. |
| Key source | `AU_PII_SECRET` env var; local dev has a hardcoded default. Production would use Vault / Secret Manager. |

### Dynamic Orphan Resolution

The spec says: *"An event that was an orphan after drop_1 must count once its lead arrives in drop_2."* Because the event ledger is append-only, unlinked events simply remain in the `events` table with their `phone_hash`. When the report is generated, the aggregator re-scans all events and joins them against all leads present at that moment. No data is modified; resolution is automatic and free. This also means data quality counts in `data_quality.json` reflect **final state**, not ingestion-time state.

---

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| SQLite with WAL | No external service needed; WAL gives concurrent reads and good write throughput for the given scale. | PostgreSQL — too much setup ceremony for a local pipeline that never has concurrent writers. |
| HMAC blind indexing instead of plaintext phone | Phone must be used for join without being stored. Deterministic HMAC is the standard pattern for searchable encryption. | Storing a salted hash — salting breaks the join unless the same salt is used everywhere, which is equivalent to a fixed key. |
| Orphan events re-evaluated at report time | The spec says final orphan count reflects final state; a pure append-only ledger satisfies this without any update logic. | Marking events as "orphan" and un-marking on lead arrival — requires mutation, violates append-only guarantee. |
| Disposition precedence: last override by `occurred_at`, then last set | The spec says "latest `disposition.override` by `occurred_at`"; if none, latest `disposition.set`. | Insertion order — but events arrive out of order across drops, so occurred_at is the only stable anchor. |
| `batch_id` permanently anchors to the first valid row | Spec says "first valid row … becomes the lead's `batch_id` permanently." | Updating `batch_id` on merge — violates the spec; original batch is what clients see in audit trails. |
| Blank rows: not counted as lead rows | Spec says "blank rows … are skipped and **not counted**." | Counting them in `lead_rows_total` — would inflate numbers and misalign rejection stats. |
| PII secret key via environment variable | Twelve-factor app convention; local dev gets a deterministic default to keep setup zero-config. | Hardcoding in source — never acceptable for PII keys; a secrets manager would be used in production. |
| `events_total` counts every non-empty line including malformed and duplicate | Spec says "`events_total` counts every non-empty line read, including malformed and duplicate lines." | Counting only valid events — would undercount and mislead monitoring. |

---

## Failure modes considered

| Failure | Handling |
|---|---|
| Re-running `ingest` on an already-ingested drop | SHA-256 file checksum + relative file path stored in `ingested_files`. Identical re-ingest is an instant no-op; produces identical reports. |
| Partial crash mid-file ingest | Each file is ingested inside a single `BEGIN IMMEDIATE … COMMIT` transaction. A crash leaves the transaction rolled back; re-running cleanly processes the file from scratch. |
| Duplicate event IDs (same or cross-drop) | `events` table has `PRIMARY KEY` on `event_id`; duplicates are caught by an in-memory set loaded from the DB at the start of each file. |
| Malformed JSON lines | Each line is independently `try/except`-wrapped; malformed lines increment the counter and are skipped without aborting the file. |
| Events arriving before their lead | Events are stored with their `phone_hash`. Report-time aggregation joins against the current leads table, so orphan events auto-resolve when the lead arrives later. |
| `disposition.override` has no phone field | The event is linked via `call_id → (workflow_id, phone_hash)` mapping built from other events in that call. |
| Unknown disposition codes | `is_known_disposition = 0` stored in the ledger; excluded from effective disposition calculation. Counted in `data_quality.json`. |
| Invalid optional field values | Stored as `NULL`; counted as a field warning (never a rejection). |
| Cross-drop duplicate events | Global deduplication uses an in-memory set populated by querying `SELECT event_id FROM events` before processing each file. |

**Not handled (known limitations):**
- Truly incremental processing of events (drop_2 currently re-reads all prior `event_id`s into RAM to dedup; at billions of events this would need a bloom filter or server-side dedup table).
- Schema version migration (new workflow schemas would require a migration strategy).
- Concurrent multi-process writers (SQLite WAL handles concurrent readers fine, but multiple writer processes would need a mutex or PostgreSQL).

---

## Testing

30 tests covering:

| Test file | What it tests |
|---|---|
| `test_crypto.py` | Blind indexing determinism, AEAD encrypt/decrypt roundtrip, tamper detection, zero plaintext PII verification across all database tables |
| `test_schema_validation.py` | Phone normalisation (10 formats), date parsing (4 formats + impossible dates), decimal currency parsing, enum case-insensitivity, header normalisation |
| `test_duplicate_merging.py` | First row permanently anchors `batch_id`; subsequent duplicates overwrite only non-null fields; schema field defaults applied to null fields after merge |
| `test_events_and_ledger.py` | Event dedup, malformed line handling, out-of-order orphan event resolution |
| `test_derived_metrics.py` | Effective disposition override precedence, calls without `call.initiated` are not counted, unknown dispositions ignored |
| `test_reports_contract.py` | Exact CSV headers, sorting, phone masking format, boolean format, `connectivity_rate` 4-decimal precision, bucket sum invariant |
| `test_end_to_end_pipeline.py` | Full CLI execution across all `--db` flag positions (before/after subcommand), target database validation, idempotency across repeated re-ingestions |

---

## Results

### Small scale (seed 42, 2000 leads, 8745 event lines):
- Ingestion + reporting: **~1.7 seconds** combined
- 2057 lead rows, 108 rejections, 57 merged duplicates, 122 field warnings
- 8745 events: 8 malformed, 237 duplicate, 462 orphan, 18 unknown dispositions
- 1892 distinct leads across 6 batches / 2 workflows

### Large scale (~140k leads, ~600k event lines):
- Benchmark script running at time of submission; estimated ~90–120s on this machine
- Stretch goal of under 2 minutes appears achievable on a laptop

### Surprising observations:
- ~952 drop_1 events belong to leads that only arrive in drop_2 — the out-of-order orphan case is very common (not edge-case).
- ~3% of events are genuine cross-drop duplicates from repeated delivery, confirming the dedup path is exercised in production-realistic data.

---

## If I had another day

1. **Truly incremental event dedup** — replace the full `SELECT event_id FROM events` pre-load with a persistent bloom filter or a partitioned dedup table, to avoid O(n) RAM cost as the ledger grows.
2. **SQL-based report aggregation** — push bucket and daily aggregation into SQL window functions to reduce Python iteration over the full ledger on every report run.
3. **Production scheduling note** — for 100k leads/day: partition `events` by `date_ist`, run ingestion as an Airflow DAG with two tasks (leads then events per drop), store bloom filter for event dedup in Redis, and stream report aggregation via SQLite ATTACH or move to PostgreSQL with materialized views.
4. **Disposition code validation at ingest time** — currently disposition filtering is deferred to report time. Flagging at ingest would simplify debugging.
5. **Proper secret management** — replace env-var default with Vault or AWS Secrets Manager lookup; add key rotation without re-encryption (by wrapping with a KEK).
