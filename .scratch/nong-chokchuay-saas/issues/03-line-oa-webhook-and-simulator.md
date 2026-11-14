# 03: LINE OA Webhook, Flex Replies, and Web Simulator

**What to build:** Add `LINE_CHANNEL_SECRET`, `LINE_CHANNEL_ACCESS_TOKEN`, and `PUBLIC_BASE_URL` to `config.py` / `.env.example`, create `line_service.py` for HMAC-SHA256 verification, Nong Chok Flex Card replies, and chat commands (`ยอดรอเบิก`, `แพ้อาหาร`, search), and expose `/api/line/webhook`, `/api/line/simulate`, `/api/documents/{doc_id}/review`, `/api/students*`, and `/api/samples/kindergarten/{preset}` in `app.py`.

**Blocked by:** 01: Database Schema, Reimbursement Columns, and Students Table, 02: Student Profile and Reimbursement Extractor

**Status:** done

- [x] `POST /api/line/webhook` verifies `X-Line-Signature` when configured and processes text, image, file, and postback events
- [x] `POST /api/line/simulate` lets teachers test photo/text/postback flows from the browser using the same pipeline
- [x] `/api/students`, `/api/students/export.csv`, and `/api/documents/{doc_id}/review` pass unit and integration tests
