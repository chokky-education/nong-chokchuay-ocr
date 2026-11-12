# OpenThai Solo Vault

A local-first personal document vault and expense tracker powered by CPU-only Thai OCR and SQLite FTS5 search.

## Language

**Vault Document**:
An archived image or PDF stored in SQLite along with its OCR text, line bounding boxes, and classification metadata.
_Avoid_: Record, Entry, Item

**Source File**:
The original unmodified image or PDF file saved on disk inside `data/uploads/` and linked to a Vault Document.
_Avoid_: Attachment, Raw Blob, Upload

**OCR Line**:
A single detected text region returned by `LineOCR`, containing recognized text, confidence score, reading-order row, page number, and 4-point box coordinates.
_Avoid_: Segment, Token, Snippet

**Ingestion Channel**:
The origin through which a Source File enters the system (`web_upload`, `watch_folder`, or `api`).
_Avoid_: Source Type, Input Mode

**Watch Folder**:
The local `data/inbox/` directory monitored for new files to ingest automatically into the Vault.
_Avoid_: Drop Folder, Hot Folder

**Expense Record**:
Structured financial data (`merchant`, `expense_date`, `subtotal`, `vat_amount`, `total_amount`, `currency`, `expense_category`) linked one-to-one with a Vault Document.
_Avoid_: Transaction, Ledger Row, Bill

**Extraction Method**:
The mechanism that produced an Expense Record (`regex`, `llm`, or `manual`).
_Avoid_: Parser Type, Mode
