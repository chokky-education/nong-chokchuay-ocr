# 02: Kindergarten Categories, Offline Auto-Tagging, and Hybrid Prompt

**What to build:** Extend `extractor.py` with kindergarten-specific document categories (`school`, `teaching`) and expense categories (`classroom`, `kids_snack`), offline Thai keyword inference for document category and tags, and updated LLM JSON extraction instructions for Gemini 2.5 Flash.

**Blocked by:** None

**Status:** done

- [x] Offline `extract_expense_regex` classifies kindergarten classroom supplies (`classroom`) and children's snacks/milk (`kids_snack`) accurately without regressing existing categories
- [x] `extract_document_intelligence` infers `school`/`teaching`/`official`/`receipt` document categories and Thai auto-tags (`ครูอนุบาล`, `สื่อการสอน`, `ขนมเด็ก`, `แจ้งผู้ปกครอง`, `เบิกโรงเรียน`, `ใบเสร็จ`, `สลิปโอนเงิน`) completely offline
- [x] `_LLM_SYSTEM_PROMPT` and validation sets allow `school`, `teaching`, `classroom`, and `kids_snack`
