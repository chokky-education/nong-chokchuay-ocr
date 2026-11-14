# 03: Quick Tags, Expense Tag Filter API, and Dashboard CSV Export UI

**What to build:** Add tag filtering and `tags` column output to `database.list_expenses` and `/api/expenses` + `/api/expenses/export.csv` in `app.py`, and update the web UI (`index.html`, `app.js`, `styles.css`) with kindergarten categories, one-click Quick Tag pills, and Expense Dashboard tag filtering.

**Blocked by:** 01: Subfolder Watch Folder Auto-Tagging and Environment Defaults, 02: Kindergarten Categories, Offline Auto-Tagging, and Hybrid Prompt

**Status:** done

- [x] `/api/expenses` and `/api/expenses/export.csv` accept `tag` filtering and return document tags
- [x] Web UI displays `school` / `teaching` Vault Document categories, `classroom` / `kids_snack` Expense Record categories, Quick Tag toggle buttons, and Expense Dashboard tag filter synced with CSV export
- [x] All unit and integration tests in `tests/` pass
