# 01: Subfolder Watch Folder Auto-Tagging and Environment Defaults

**What to build:** Enable `AUTO_EXTRACT_ON_INGEST=true` by default in `AppConfig.from_env()`, support `GEMINI_API_KEY` / `GOOGLE_API_KEY` fallbacks and safe `.env` overrides in `config.py`, auto-create `data/inbox/teacher/` and `data/inbox/me/` subdirectories, scan Watch Folder subfolders in `watcher.py` to apply owner tags (`ครูอนุบาล`, `ส่วนตัว`), and merge auto-extracted tags in `ocr_service.py`.

**Blocked by:** None

**Status:** done

- [x] `AppConfig.from_env()` defaults `auto_extract_on_ingest` to `True`, supports `GEMINI_API_KEY`/`GOOGLE_API_KEY` fallback, and `load_dotenv_file()` handles later non-empty overrides cleanly
- [x] `AppConfig.ensure_dirs()` creates `data/inbox/teacher/` and `data/inbox/me/`, and `InboxWatcher` ingests files from subfolders with auto-mapped tags while preserving the subfolders
- [x] `apply_intelligence_to_document()` merges suggested tags with pre-assigned tags without dropping Watch Folder owner tags
