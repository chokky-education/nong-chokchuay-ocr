# Kindergarten Duo Automation: 2-Person & Kindergarten Teacher Upgrade

## Problem Statement

Two users sharing the local OpenThai Paperless Vault & Expense Tracker—one of whom is a kindergarten teacher—need zero-friction separation between personal and kindergarten teacher documents, kindergarten-specific categories (`school`, `teaching`, `classroom`, `kids_snack`) and automatic Thai keyword tagging, automatic expense extraction enabled by default, and Gemini 2.5 Flash hybrid support without changing the localhost single-database architecture.

## Solution

1. **Subfolder Watch Folder Auto-Tagging**: Support `data/inbox/teacher/` (`ครูอนุบาล`) and `data/inbox/me/` (`ส่วนตัว`) subdirectories (plus custom subfolder names) inside the Watch Folder, automatically creating the default `teacher` and `me` subdirectories on startup and preserving existing tags when merging auto-extracted tags.
2. **Kindergarten Teacher Intelligence (Offline + LLM Hybrid)**: Expand `extractor.py` with kindergarten classroom/supply and kids' snack expense categories (`classroom`, `kids_snack`), document categories (`school`, `teaching`), offline Thai auto-tagging, and an updated Gemini prompt.
3. **Per-Tag Expense Filtering & Quick Tags UI**: Allow filtering Expense Records and UTF-8 BOM CSV exports by tag (e.g., `ครูอนุบาล` vs `ส่วนตัว`), add one-click Quick Tag pills on the document bar, and enable `AUTO_EXTRACT_ON_INGEST=true` by default in `AppConfig.from_env()` and `.env`.

## User Stories

1. As a kindergarten teacher, I want to drop photos or PDFs into `data/inbox/teacher/` so that every ingested Vault Document and Expense Record is automatically tagged `ครูอนุบาล` without manual tagging.
2. As the second user, I want to drop my documents into `data/inbox/me/` so that they are tagged `ส่วนตัว` and can be filtered separately in the Vault and Expense Dashboard.
3. As a kindergarten teacher, I want receipts for teaching supplies (crayons, modeling clay, stickers, storybooks) and children's snacks/school milk classified into `classroom` and `kids_snack` automatically, and school/parent letters classified into `school` or `teaching`.
4. As both users, I want `AUTO_EXTRACT_ON_INGEST=true` and `GEMINI_API_KEY` / `LLM_API_KEY` fallback active out of the box, plus one-click Quick Tags and per-tag CSV export in the Expense Dashboard.

## Implementation Decisions

- Reuse `documents.tags_json` and `json_each(d.tags_json)` for owner/context separation (`ครูอนุบาล`, `ส่วนตัว`) rather than breaking the single-user schema boundary in ADR 0001.
- Keep `AppConfig` dataclass default `auto_extract_on_ingest: bool = False` so isolated unit tests using `AppConfig.from_dir(...)` stay deterministic, while defaulting `AppConfig.from_env()` to `True`.
- Merge auto-extracted tags onto Watch Folder subfolder tags in `apply_intelligence_to_document` so channel/owner attribution is never overwritten.
