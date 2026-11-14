# 02: Student Profile and Reimbursement Extractor

**What to build:** Expand `extractor.py` and `ocr_service.py` to detect kindergarten student documents (`is_likely_student_document`, `extract_student_profile_regex`), infer `reimbursement_status` and `budget_source` on expenses, update `_LLM_SYSTEM_PROMPT` to extract `is_student` + `student`, and persist linked `Student Profile` rows during document ingestion.

**Blocked by:** 01: Database Schema, Reimbursement Columns, and Students Table

**Status:** done

- [x] Offline regex extracts Thai student full name, nickname, 13-digit citizen ID, B.E. birth date, classroom, parent name/phone, food allergies, and medical notes
- [x] Expense extractor infers `pending_reimbursement` vs `personal` and `budget_source` (`classroom`, `kids_snack`, `subsidy`, `school_project`, `personal`)
- [x] `apply_intelligence_to_document` and `ingest_document` persist and return `student` cleanly without breaking existing tests
