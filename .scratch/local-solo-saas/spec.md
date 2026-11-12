# Local-first Solo SaaS: OpenThai Paperless Vault & Expense Tracker

## Problem Statement

A solo user running the local OCR studio currently gets a stateless OCR demo: uploaded images and PDFs are discarded instead of archived, OCR typos cannot be corrected or re-indexed for Thai full-text search, documents lack categories or tags, saved documents misalign bounding boxes on reopen, every page reload re-runs OCR on a sample file and inserts duplicates, every file requires manual browser upload, and scanned receipts/slips cannot be summarized into monthly expense totals or exported for tax filing.

## Solution

Transform the local app into a cohesive personal Paperless Vault and Expense Tracker running on a single computer:

1. Every uploaded or Watch Folder document flows through one unified ingestion pipeline that preserves the Source File on disk, records true image dimensions, stores Category and Tags, and indexes all text in SQLite FTS5.
2. Users can download original Source Files, correct OCR text and metadata with automatic FTS5 re-indexing, and filter the Vault by Category or Tag.
3. Users can drop images or PDFs into a local Watch Folder for hands-free background ingestion, with bad files isolated rather than retried forever.
4. Users can extract structured Expense Records from receipts and slips offline via Thai Regex heuristics—or optionally via a text-only LLM API configured in environment variables—and view/edit monthly summaries or export UTF-8 BOM CSV files.

## User Stories

1. As a solo user, I want every uploaded image or PDF saved on local disk as a Source File, so that I can download the original document from the Vault anytime.
2. As a solo user, I want to assign a Category and Tags to a Vault Document, so that I can organize receipts, contracts, and official documents cleanly.
3. As a solo user, I want to edit misread Thai OCR text and re-index it into FTS5, so that future searches always locate the document accurately.
4. As a solo user, I want saved Vault Documents to render OCR Line bounding boxes using their true original dimensions, so that hovering or clicking lines aligns accurately on reopen.
5. As a solo user, I want to drop PDFs or receipt photos into a local Watch Folder, so that documents are OCR'd and archived automatically without opening the browser.
6. As a solo user, I want corrupt or unreadable Watch Folder files moved to a failed quarantine folder with an error note, so that one bad file never blocks or loops the watcher.
7. As a solo user, I want to extract merchant, date (converting Thai Buddhist Era years to Gregorian ISO dates), subtotal, VAT, and total from receipts 100% offline via regex, so that sensitive financial records work without internet or API keys.
8. As a solo user, I want an optional text-only LLM adapter enabled only when an API key is configured, so that only recognized text (never raw images) is sent for enhanced extraction and auto-tagging with automatic regex fallback.
9. As a solo user, I want a monthly Expense Dashboard with inline editing and UTF-8 BOM CSV export, so that I can review monthly spending and open reports cleanly in Excel.
10. As a solo user, I want refreshing the browser to load cleanly without re-inserting duplicate sample documents, so that my Vault stays uncluttered.

## Implementation Decisions

- **Unified Ingestion Pipeline**: Browser uploads and Watch Folder ingestion share one pipeline so Source File persistence, dimension capture, FTS5 indexing, and optional auto-extraction behave identically across every Ingestion Channel.
- **Idempotent Database Migration**: Preserve existing Vault Document rows on startup via additive column checks (`ALTER TABLE` only when absent) and rebuild the FTS5 virtual table safely when schema columns expand.
- **Single-Database Relational Ownership**: Store Expense Records in an `expenses` table keyed 1:1 to `documents` with `ON DELETE CASCADE`; deleting a Vault Document also removes its Source File from disk.
- **Polling Watch Folder with Stability Guard**: Poll the Watch Folder on a configurable interval and verify file size stability across polls in background mode, while exposing an immediate manual scan endpoint for deterministic testing and on-demand triggers.
- **Offline-First Hybrid Extractor**: Implement Thai Regex extraction as the default zero-cost baseline and treat LLM extraction as an optional text-only enhancement with automatic fallback to regex on network or parsing failure.
- **Zero-Dependency Config Loading**: Parse `.env` with Python standard library helpers at startup so the app requires no additional runtime package installations.

## Testing Decisions

- **Primary Seams**:
  - HTTP API + Temp SQLite/Filesystem Seam: Exercise FastAPI endpoints end-to-end against temporary SQLite databases and temporary `uploads`/`inbox` folders with a deterministic stub OCR engine for fast unit tests.
  - Watch Folder Scan Seam: Place real valid and corrupt files into a temporary Watch Folder and invoke a scan pass to verify ingestion, Source File archival, FTS5 searchability, and failed-file quarantine.
  - Extractor & CSV Seam: Test Thai receipt regex parsing (including BE-to-CE date conversion and VAT math), test text-only LLM adapter payload & error fallback via mocked HTTP transport, and verify CSV export output.
  - Real ONNX Engine Smoke Seam: Run one real `LineOCR` pass over a repository sample image to verify end-to-end integration with the real ONNX runtime.
- **Rules**: Assert only external observable behavior (HTTP responses, FTS search results, files on disk, CSV contents), never internal private helpers.

## Out of Scope

- Cloud/VPS deployment, public reverse proxies, Cloudflare Tunnel, or Tailscale setup
- Multi-user authentication, single-user login screens, or API key gating (localhost single-user trust boundary)
- Telegram or LINE Messaging Bot ingestion
- Searchable PDF synthesis with embedded invisible Thai text layers
