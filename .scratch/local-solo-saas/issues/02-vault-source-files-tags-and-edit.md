# 02: Vault Source Files, Categories, Tags, Edit Re-index, and Box Alignment

**What to build:** Upgrade the Vault so every uploaded document persists its unmodified Source File on disk, records true image dimensions, supports Category and Tags, lets the user download Source Files, edit OCR text/metadata with FTS5 re-indexing, filter the Vault, and view accurately aligned bounding boxes on reopen.

**Blocked by:** 01: Matt Pocock Setup and Test Seam

**Status:** ready-for-agent

- [ ] Existing Vault Document rows survive idempotent startup migration without data loss
- [ ] Uploaded Source Files are saved on disk and downloadable via API and UI
- [ ] Editing OCR text, Category, or Tags updates SQLite and makes new terms immediately searchable via FTS5
- [ ] Reopening a saved Vault Document renders bounding boxes using stored original dimensions
- [ ] Deleting a Vault Document cleans up its DB record, FTS entry, and Source File
