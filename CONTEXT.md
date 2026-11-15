# OpenThai Solo Vault & น้องโชคช่วย (Nong ChokChuay Kindergarten Assistant)

A local-first and LINE OA-enabled document vault, classroom expense reimbursement tracker, and kindergarten student profile assistant powered by CPU-only Thai OCR (`OpenThai 2.0`) and SQLite FTS5 search.

> ดูคำอธิบายภาษาไทยและตารางอ้างอิงในโค้ดเพิ่มเติมได้ที่ [GLOSSARY.md](./GLOSSARY.md) และกติกาหลักของ Agent ที่ [AGENTS.md](./AGENTS.md)

## Language

**Vault Document**:
An archived image or PDF stored in SQLite along with its OCR text, line bounding boxes, review status, and classification metadata.
_Avoid_: Record, Entry, Item

**Source File**:
The original unmodified image or PDF file saved inside `data/uploads/` (and durably stored under private R2 `vault/uploads/<source_storage_key>` with SHA-256 verification when running in Cloudflare mode per ADR 0003), linked to a Vault Document and lazily restored on authorized download.
_Avoid_: Attachment, Raw Blob, Upload

**Vault Manifest & Snapshot Revision**:
The authoritative `vault/manifest.json` pointer and versioned SQLite backup (`vault/snapshots/rev-*.db`) in the private R2 bucket (`nong-chokchuay-vault`), updated via conditional ETag (`If-Match` / `If-None-Match: *`) as the single durable commit point.
_Avoid_: Checkpoint File, Temp Backup

**OCR Line**:
A single detected text region returned by `LineOCR`, containing recognized text, confidence score, reading-order row, page number, and 4-point box coordinates.
_Avoid_: Segment, Token, Snippet

**Ingestion Channel**:
The origin through which a Source File enters the system (`web_upload`, `watch_folder`, `line_oa`, or `api`).
_Avoid_: Source Type, Input Mode

**Watch Folder**:
The local `data/inbox/` directory monitored for new files to ingest automatically into the Vault.
_Avoid_: Drop Folder, Hot Folder

**Expense Record**:
Structured financial data (`merchant`, `expense_date`, `subtotal`, `vat_amount`, `total_amount`, `currency`, `expense_category`, `reimbursement_status`, `budget_source`) linked one-to-one with a Vault Document.
_Avoid_: Transaction, Ledger Row, Bill

**Extraction Method**:
The mechanism that produced an Expense Record or Student Profile (`regex`, `llm`, or `manual`).
_Avoid_: Parser Type, Mode

**Student Profile**:
Structured kindergarten student information (`full_name`, `nickname`, `citizen_id`, `birth_date`, `classroom`, `parent_name`, `parent_phone`, `allergies`, `medical_notes`) linked optionally to a Vault Document.
_Avoid_: Kid Row, Child Entry

**Reimbursement Status**:
Whether an Expense Record represents teacher out-of-pocket spending awaiting school claim (`pending_reimbursement`), already reimbursed (`reimbursed`), or personal spending (`personal`).
_Avoid_: Claim Flag, Paid Bool

**Review Queue**:
Vault Documents with `review_status = 'pending_review'` awaiting one-click teacher verification before moving to `'reviewed'`.
_Avoid_: Moderation List, Drafts

**LINE OA Channel**:
The LINE Official Account webhook and Flex Card / LIFF interaction layer (`ingestion_channel = 'line_oa'`) allowing teachers to capture receipts, student documents, and school circulars straight from mobile chat.
_Avoid_: Bot Pipe, Chat Feed
