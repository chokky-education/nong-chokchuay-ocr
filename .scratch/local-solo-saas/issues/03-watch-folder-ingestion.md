# 03: Watch Folder Auto-Ingestion

**What to build:** Automatically detect images and PDFs dropped into the local Watch Folder, ingest them through the shared OCR pipeline with `watch_folder` channel attribution, remove ingested files from the inbox after persistence, quarantine failing files with error details, and expose status and manual scan controls in the API and UI.

**Blocked by:** 02: Vault Source Files, Categories, Tags, Edit Re-index, and Box Alignment

**Status:** done

- [x] Valid image and PDF files in the Watch Folder are ingested into the Vault and archived as Source Files
- [x] Unreadable or corrupt files are quarantined into the failed inbox directory with an accompanying error note
- [x] Background polling checks file-size stability before ingesting and can be triggered on demand via API and UI
