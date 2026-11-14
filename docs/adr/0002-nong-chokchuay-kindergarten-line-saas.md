# 2. Nong ChokChuay Kindergarten Teacher SaaS (LINE OA + Review Queue + Cloudflare Containers)

Status: accepted

Building on ADR 0001 (Single SQLite + Filesystem Source Files + Text-Only Hybrid Extraction), we extend the application into **"น้องโชคช่วย" (Nong ChokChuay)** for Thai kindergarten teachers while preserving single-database portability and backward compatibility:

1. **LINE OA Front-Door + Web/LIFF Workspace:** Expose `/api/line/webhook` (with `X-Line-Signature` HMAC-SHA256 verification and free Reply Flex Cards) and `/api/line/simulate` inside the same FastAPI app so teachers can forward bills, student documents, and school PDFs from LINE and review/export them on desktop/LIFF without running a separate Node/TypeScript backend.
2. **Teacher Reimbursement & Review Queue:** Add `review_status` (`pending_review` / `reviewed`) and `line_user_id` to `documents`, and `reimbursement_status` (`pending_reimbursement` / `reimbursed` / `personal`) + `budget_source` to `expenses` via idempotent `PRAGMA table_info` migrations.
3. **Kindergarten Student Profiles:** Add a dedicated `students` table linked optionally (`ON DELETE SET NULL`) to `documents` to extract and track child identities, parent contacts, and food allergy / medical alerts locally without sending raw student ID/birth-certificate images to external servers.
4. **Cloudflare $5/mo Container Deployment Target:** Configure `wrangler.toml` with Cloudflare Containers (`sleep_after = "3m"`, scale-to-zero) wrapping the existing Python/ONNX `Dockerfile` so the full OpenThai 2.0 stack fits within the Cloudflare Workers Paid ($5/month) resource budget.
