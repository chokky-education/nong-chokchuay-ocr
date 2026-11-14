# 01: Database Schema, Reimbursement Columns, and Students Table

**What to build:** Extend `database.py` with idempotent migrations for `documents.review_status`, `documents.line_user_id`, `expenses.reimbursement_status`, `expenses.budget_source`, and a new `students` table with full CRUD and summary calculations (`pending_reimbursement_total`, `reimbursed_total`, `personal_total`, `pending_review_count`, `total_students`).

**Blocked by:** None

**Status:** done

- [x] Idempotent migrations preserve legacy `documents` and `expenses` rows cleanly
- [x] `upsert_expense` and `list_expenses` support `reimbursement_status` and `budget_source` with summary totals
- [x] `students` table supports `upsert_student`, `list_students`, `get_student_by_id`, `get_student_by_document_id`, and `delete_student`
