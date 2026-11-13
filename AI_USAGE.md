# AI Usage Disclosure

AI tools are allowed. We want to know **how** you used them, because using them well is a
skill. Honest answers are never held against you. Discovering undisclosed use is.

## Tools used
Google Antigravity (Gemini), ChatGPT.

## What I used them for
- Accelerated scaffolding of test suites and data structure models.
- Generated initial boilerplate for CLI argument parsing and CSV writing.
- Formulated KaTeX/KaTeX-compatible mathematical representations and interview preparation questions.
- Explored edge-case test vectors for Indian mobile phone numbers (10, 11, and 12-digit variants) and multi-format day-first date strings.

## What I wrote or decided myself
- **Core Architecture & Data Modeling:** Designed the append-only interaction ledger, SQLite WAL configuration, and composite indexing strategy.
- **Searchable Encryption (Blind Indexing) Strategy:** Formulated the deterministic HMAC-SHA256 blind indexing pattern (`phone_hash`) alongside AEAD Encrypt-then-MAC storage to guarantee zero plaintext PII at rest while retaining exact-match join capability between events and leads.
- **Dynamic Orphan Event Resolution:** Designed the ledger query engine that keeps unlinked events in an append-only state until their corresponding lead arrives in a subsequent drop, accurately recalculating `data_quality.json` to reflect final state.
- **Supervisor Override Resolution:** Engineered the precedence logic where valid supervisor overrides take precedence over agent disposition classifications by occurred timestamp, ensuring unknown disposition codes from outside the catalogue are quarantined and ignored in metrics.
- **Rejection & Merging Pipeline:** Implemented strict schema-order walking for rejection classification (`missing_required:<field>` vs `invalid_phone`), warning tracking on non-rejected rows, permanent `batch_id` attribution to the first valid row, and non-null overwriting on duplicate merges.

## Where the AI was wrong or unhelpful
- In the initial draft for date parsing, the AI suggested using Python's `dateutil.parser.parse`, which defaults to American month-first (`MM/DD/YYYY`) for ambiguous dates like `01/02/2026`. The assignment specification strictly requires day-first parsing (`DD/MM/YYYY`) and explicit formats. I discarded the AI suggestion and built an explicit format matcher enforcing day-first semantics and rejecting impossible dates (e.g. `31/02/2026`).
- The AI initially suggested updating or deleting earlier interaction events when a supervisor override occurred. This violates the core domain invariant of an append-only interaction ledger. I corrected this by preserving all raw events immutably in the ledger and computing effective dispositions at query/aggregation time.

## How I verified AI-generated code
- Executed unit tests (`pytest -v`) across 30 distinct test cases covering boundary conditions, crypto roundtrips, zero plaintext PII storage, malformed lines, out-of-order deliveries, schema defaults, CLI flag positions, and schema orders.
- Verified byte-for-byte idempotency on repeated and split drops.
- Tested the pipeline against synthetic data generated with unseen random seeds (e.g., `--seed 99`) and verified mathematical invariants across all 5 generated report files.
- Measured runtime and resource consumption on the large-scale dataset (~140k leads, ~600k events).
