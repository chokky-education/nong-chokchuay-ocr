# 05: Optional Text-Only LLM Hybrid Extraction and Auto-Tag

**What to build:** Add `.env` configuration support and an optional text-only LLM REST adapter that enhances receipt extraction and suggests document Category/Tags only when an API key is provided, automatically falling back to offline regex if the request fails or no key is configured.

**Blocked by:** 04: Offline Thai Regex Expense Extractor and Dashboard

**Status:** done

- [x] LLM adapter sends strictly OCR text (never raw image bytes) when configured via `.env`
- [x] Extraction automatically falls back to offline regex on API errors, timeouts, or invalid JSON responses
- [x] Auto-extract and Extraction Method (`regex` / `llm` / `manual`) are visible in API responses and UI
