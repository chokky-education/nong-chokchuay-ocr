# 06: Solo Launcher Polish and Real OCR End-to-End Verification

**What to build:** Eliminate duplicate sample auto-creation on browser refresh, provide `.env.example`, update `start.sh` and `README.md` for the Local-first Solo SaaS workflow, and verify the full application including a real ONNX `LineOCR` smoke test on repository sample documents.

**Blocked by:** 03: Watch Folder Auto-Ingestion, 05: Optional Text-Only LLM Hybrid Extraction and Auto-Tag

**Status:** ready-for-agent

- [ ] Reloading the web UI loads the latest Vault Document (or clean empty state) without inserting duplicate samples
- [ ] `.env.example`, `start.sh`, and `README.md` accurately reflect Watch Folder, Vault, and Hybrid Expense workflows
- [ ] Full test suite including real ONNX `LineOCR` smoke test passes
