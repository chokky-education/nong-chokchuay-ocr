# 🌤️ น้องโชคช่วย (Nong ChokChuay) — ผู้ช่วยจัดการเอกสาร & บิลเบิกจ่ายคุณครูอนุบาล

> **ระบบผู้ช่วยคุณครูอนุบาล (LINE OA + เว็บ/LIFF + คลังเอกสารส่วนตัวไม่มีวันหมดอายุ)**  
> พัฒนาต่อยอดจากโมเดล **OpenThai 2.0 OCR Text (ONNX)** โดย **iApp Technology** เพื่อลดภาระงานเอกสารของคุณครู: จดบิลสำรองจ่ายรอเบิกคืน, เก็บทะเบียนเด็กอนุบาลพร้อมป้ายเตือนแพ้อาหาร, และค้นหาเอกสารโรงเรียนผ่าน LINE OA โดยใช้ Reply API ฟรีไม่จำกัด

---

## 📌 สารบัญ

- [1. ภาพรวมระบบ (System Overview)](#1-ภาพรวมระบบ-system-overview)
- [2. โครงสร้างโปรเจกต์ (Project Structure)](#2-โครงสร้างโปรเจกต์-project-structure)
- [3. วิธีเริ่มใช้งานแบบรวดเร็ว (Quick Start)](#3-วิธีเริ่มใช้งานแบบรวดเร็ว-quick-start)
- [4. สถาปัตยกรรมฐานข้อมูลและการค้นหา (Database & Full-Text Search)](#4-สถาปัตยกรรมฐานข้อมูลและการค้นหา-database--full-text-search)
- [5. รายละเอียด REST API (API Reference)](#5-รายละเอียด-rest-api-api-reference)
- [6. พิมพ์เขียวสำหรับการต่อยอดเป็น Digital Product (Product Blueprints)](#6-พิมพ์เขียวสำหรับการต่อยอดเป็น-digital-product-product-blueprints)
- [7. การนำไปขึ้นเซิร์ฟเวอร์และการ Deploy ด้วย Docker & Cloudflare Workers Paid](#7-การนำไปขึ้นเซิร์ฟเวอร์และการ-deploy-ด้วย-docker--cloudflare-workers-paid)
- [8. ประสิทธิภาพและข้อกำหนดทรัพยากร (Performance & Benchmarks)](#8-ประสิทธิภาพและข้อกำหนดทรัพยากร-performance--benchmarks)
- [9. ลิขสิทธิ์และการอ้างอิง (License & Attributions)](#9-ลิขสิทธิ์และการอ้างอิง-license--attributions)

---

## 1. ภาพรวมระบบ (System Overview)

ระบบนี้รวมเอาโมเดล **OpenThai 2.0 OCR Text (ONNX)** ขนาดจิ๋วเพียง **4.2 MB** เข้ากับ Web Server สมรรถนะสูง (FastAPI), SQLite Trigram FTS5 Full-Text Search และ **LINE OA Webhook + Interactive Simulator** สำหรับคุณครูอนุบาล:

```
[ มือถือครู (LINE OA) / เว็บโต๊ะทำงาน ] ---> [ FastAPI + OpenThai 2.0 ONNX (CPU) ]
   (ถ่ายรูปบิล, สูติบัตรเด็ก, PDF)             - PP-OCRv6-tiny + Thai CTC (~0.4s/หน้า)
                                                               │
                        ┌──────────────────────────────────────┼────────────────────────────────────┐
                        ▼                                      ▼                                    ▼
         [ 💸 สมุดเบิกจ่ายห้องเรียน ]             [ 👶 แฟ้มเด็กอนุบาล & แพ้อาหาร ]         [ 📂 ตู้เซฟเอกสารโรงเรียน ]
        - แยกเงินสำรองจ่าย / รอเบิกคืน          - ดึงชื่อ เลข 13 หลัก วันเกิด (พ.ศ->ค.ศ.)  - ถาดรอตรวจ (1-Click Review)
        - ส่งออก Excel CSV ภาษาไทยไม่เพี้ยน      - ป้ายเตือนแพ้อาหาร & เบอร์ผู้ปกครอง      - ค้นหา FTS5 ไม่มีวันหมดอายุ
```

### จุดเด่นสำหรับคุณครูอนุบาล & LINE OA SaaS
- **ส่งงานผ่าน LINE OA ตอบกลับฟรี (`replyToken`):** รองรับ Webhook (`/api/line/webhook`) ตรวจสอบลายเซ็น `X-Line-Signature`, ตอบกลับเป็น Flex Card กด "✅ ยืนยันตรวจแล้ว" หรือ "💸 เบิกเงินคืนแล้ว" และค้นหาในแชทด้วยคำสั่ง `"ยอดรอเบิก"`, `"แพ้อาหาร"`, หรือคำค้นเอกสาร พร้อมตัวจำลองแชท LINE บนหน้าเว็บ (`/api/line/simulate`) (LINE ระบุให้ใช้ `replyToken` เพียงครั้งเดียว **ภายใน 1 นาที** และควรตอบกลับให้เร็วที่สุด)
- **แฟ้มทะเบียนเด็ก & แจ้งเตือนแพ้อาหาร:** สกัดชื่อ-นามสกุล, ชื่อเล่น, เลขบัตร 13 หลัก, วันเกิด (แปลง พ.ศ.→ค.ศ.), ห้องเรียน, เบอร์ผู้ปกครอง, ประวัติแพ้อาหาร และโรคประจำตัวจากใบสมัคร/สูติบัตรอัตโนมัติ (แบบ Offline Regex หรือ Hybrid Text LLM)
- **สมุดเบิกจ่ายเงินสำรองจ่าย:** แยกรายจ่าย `pending_reimbursement` (รอเบิกคืนโรงเรียน), `reimbursed` (เบิกคืนแล้ว), และ `personal` (ส่วนตัว) พร้อม Dashboard KPI และ Export CSV (UTF-8 BOM)
- **ความปลอดภัย (Privacy-first):** ประมวลผลภาพบนเซิร์ฟเวอร์ตัวเอง 100% เก็บไฟล์ต้นฉบับใน `data/uploads/` และต่อให้เปิด LLM เสริม ก็ส่งเฉพาะข้อความ Text ที่อ่านได้เท่านั้น
- **Watch Folder (`data/inbox/`):** โยนไฟล์ภาพหรือ PDF ลงโฟลเดอร์ `data/inbox/` เพื่อให้ระบบดูดไปสแกนและจัดเก็บอัตโนมัติ (ไฟล์เสียถูกแยกเข้า `data/inbox_failed/` พร้อม `.error.txt`)

วิธีเตรียมเครื่องและรันชุดทดสอบอยู่ใน [คู่มือทำงานกับ Agent](docs/agent-workflow.md)

---

## 2. โครงสร้างโปรเจกต์ (Project Structure)

```
openthai-ocr-app/
├── app.py                      # FastAPI Backend & Vault/Watcher/Expense REST APIs
├── config.py                   # AppConfig + โหลดค่า .env (Paths, Watch Folder, LLM settings)
├── database.py                 # SQLite ORM, Schema Migration, Trigram FTS5 และตาราง expenses
├── ocr_engine.py               # OpenThai 2.0 ONNX OCR Inference Engine + Geometry alignment
├── ocr_service.py              # Ingestion Pipeline กลาง (Source File Storage + OCR + Auto-extract)
├── storage_service.py          # R2 Durable Storage Coordinator (Startup Restore Gate, Lock, Rollback, Conditional ETag)
├── watcher.py                  # Background Polling Watch Folder (data/inbox/ -> Vault / inbox_failed)
├── extractor.py                # Offline Thai Regex Receipt Extractor + Optional Text-only LLM Adapter
├── AGENTS.md                   # คู่มือรันและทดสอบสำหรับ Agent & Developer
├── CONTEXT.md                  # Domain Glossary สำหรับ Personal Paperless Vault & Expense Tracker
├── .env.example                # ตัวอย่าง Environment Variables (Watch Folder, Optional Text LLM)
├── start.sh                    # Shell script สำหรับเปิดเซิร์ฟเวอร์ Local (127.0.0.1:8765)
├── Dockerfile                  # สำหรับ Build เป็น Docker Container
├── requirements.txt            # รายการ Dependencies ทั้งหมด
├── README.md                   # คู่มือทางเทคนิคและ REST API
│
├── model/                      # โมเดล OpenThai 2.0 OCR Text (Apache-2.0)
│   ├── det.onnx                # Text Line Detector (PaddlePaddle PP-OCRv6-tiny ~1.8 MB)
│   ├── rec.onnx                # Thai CTC Recognizer (~2.4 MB)
│   ├── rec_dict.txt            # พจนานุกรม 528 ตัวอักษรไทย-อังกฤษ
│   ├── config.json             # ค่า Threshold และพารามิเตอร์การจัดเรียงข้อความ
│   └── runtime/
│       ├── line_engine.py      # Runtime engine จัดการ Deskew, Rotation, Multi-column layout
│       └── thai_norm.py        # Normalizer สระ/วรรณยุกต์ไทย
│
├── static/                     # Frontend Web UI (Vanilla JS + CSS สะอาดตา ไม่มี Build step)
│   ├── index.html              # โครงสร้างหน้าเว็บพร้อม Modal คลังเอกสาร & Dashboard รายจ่าย
│   ├── styles.css              # Apple/Linear Design System สำหรับ Desktop Web
│   └── app.js                  # Logic Drag-and-drop, Bounding Box, Inline Edit, Expense Dashboard
│
├── docs/                       # เอกสาร Architecture Decisions (ADR) และ Workflow
├── samples/                    # เอกสารตัวอย่างสำหรับทดสอบ
│   ├── sample_official_document.png  # บันทึกข้อความราชการ & ตาราง
│   └── sample_budget_table.png       # รายงานงบประมาณรายจ่าย
│
├── data/                       # โฟลเดอร์เก็บข้อมูล (Persistent Local Data)
│   ├── documents.db            # ฐานข้อมูล SQLite (documents, documents_fts, expenses)
│   ├── uploads/                # ต้นฉบับเอกสารที่บันทึกเก็บไว้ดาวน์โหลด
│   ├── inbox/                  # Watch Folder สำหรับโยนไฟล์เข้าแล้วประมวลผลอัตโนมัติ
│   └── inbox_failed/           # Quarantine สำหรับไฟล์ที่เสียหายพร้อมไฟล์ .error.txt
│
├── tests/                      # Automated unittest suite (ใช้ Tempdir ไม่กระทบ data/ จริง)
└── .venv/                      # Python 3.11 Virtual Environment
```

---

## 3. วิธีเริ่มใช้งานแบบรวดเร็ว (Quick Start)

ถ้าเพิ่ง clone ให้เตรียม Python และ dependency ตาม [วิธีรันในเครื่อง](docs/agent-workflow.md#รันและตรวจในเครื่อง) ก่อน

### วิธีที่ 1: รันผ่านสคริปต์บน macOS

```bash
./start.sh
```
สคริปต์จะเริ่มเซิร์ฟเวอร์ที่ `http://127.0.0.1:8765` และเปิดหน้าเว็บให้โดยอัตโนมัติ

### วิธีที่ 2: รันผ่านคำสั่ง Python โดยตรง

```bash
./.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8765 --reload
```

---

## 4. สถาปัตยกรรมฐานข้อมูลและการค้นหา (Database & Full-Text Search)

ระบบใช้ **SQLite 3** ร่วมกับ **FTS5 Trigram Tokenizer** ทำให้ **ค้นหาคำย่อย (Substring) ในภาษาไทยที่ไม่มีการเว้นวรรค** ได้แม่นยำ พร้อมทำ Schema Migration แบบ Idempotent เพื่อรักษาข้อมูลเดิม:

### ตารางหลัก (`documents`)
| Column | Type | คำอธิบาย |
| :--- | :--- | :--- |
| `id` | TEXT (UUID) | Primary Key ประจำเอกสาร |
| `filename` | TEXT | ชื่อไฟล์เอกสารต้นฉบับ |
| `file_type` | TEXT | ชนิดไฟล์ (`image` หรือ `pdf`) |
| `file_size` | INTEGER | ขนาดไฟล์ (Bytes) |
| `total_pages`| INTEGER | จำนวนหน้าเอกสาร |
| `text_content`| TEXT | ข้อความเต็ม (แก้ไขย้อนหลังและ re-index FTS ได้) |
| `lines_json` | TEXT | JSON บรรทัด (ข้อความ, ความมั่นใจ, พิกัด `box` 4 มุม, `page`) |
| `avg_confidence`| REAL | คะแนนความมั่นใจเฉลี่ย (0.00 - 1.00) |
| `duration_ms`| REAL | เวลาที่ใช้ประมวลผล (มิลลิวินาที) |
| `image_preview`| TEXT | รูปภาพหน้าแรกแบบ Base64 (สำหรับแสดงผล Bounding Boxes) |
| `source_path` | TEXT | Path ของไฟล์ต้นฉบับที่เก็บใน `data/uploads/` |
| `category` | TEXT | หมวดหมู่เอกสาร (เช่น `general`, `receipt`, `tax`, `official`) |
| `tags_json` | TEXT | รายการแท็กแบบ JSON Array |
| `ingestion_channel`| TEXT | ช่องทางนำเข้า (`web_upload` หรือ `watch_folder`) |
| `doc_date` | TEXT | วันที่อ้างอิงเอกสาร (`YYYY-MM-DD`) |
| `image_width` | INTEGER | ความกว้างจริงของภาพที่ใช้อ้างอิงพิกัด Bounding Box (px) |
| `image_height` | INTEGER | ความสูงจริงของภาพที่ใช้อ้างอิงพิกัด Bounding Box (px) |
| `created_at` | TEXT | วันที่และเวลาที่นำเข้าเอกสาร |
| `updated_at` | TEXT | วันที่และเวลาที่แก้ไขล่าสุด |

### ตารางรายจ่าย (`expenses`)
| Column | Type | คำอธิบาย |
| :--- | :--- | :--- |
| `id` | TEXT (UUID) | Primary Key ของรายการรายจ่าย |
| `document_id` | TEXT UNIQUE | Foreign Key อ้างอิง `documents(id) ON DELETE CASCADE` |
| `merchant` | TEXT | ชื่อร้านค้า / ผู้ให้บริการ |
| `expense_date` | TEXT | วันที่รายจ่าย (`YYYY-MM-DD`, แปลง พ.ศ. -> ค.ศ. อัตโนมัติ) |
| `subtotal` | REAL | ยอดก่อนภาษีมูลค่าเพิ่ม |
| `vat_amount` | REAL | ภาษีมูลค่าเพิ่ม (VAT) |
| `total_amount` | REAL | ยอดสุทธิรวม |
| `currency` | TEXT | สกุลเงิน (ค่าเริ่มต้น `THB`) |
| `expense_category` | TEXT | หมวดหมู่รายจ่าย (`food`, `transport`, `utilities`, `office`, `other`) |
| `extraction_method` | TEXT | วิธีที่สกัด (`regex`, `llm`, หรือ `manual`) |
| `raw_json` | TEXT | Payload JSON ดิบจากการสกัด |

### ตารางค้นหาเสมือน (`documents_fts`)
```sql
CREATE VIRTUAL TABLE documents_fts USING fts5(
    id UNINDEXED,
    filename,
    category,
    tags_text,
    text_content,
    tokenize="trigram"
);
```
*ตัวอย่างการค้นหา:* เมื่อผู้ใช้ค้นหา `"งบประมาณ"` ระบบจะทำการ Query:
```sql
SELECT d.* FROM documents_fts f
JOIN documents d ON d.id = f.id
WHERE documents_fts MATCH '"งบประมาณ"'
ORDER BY d.created_at DESC;
```

---

## 5. รายละเอียด REST API (API Reference)

สามารถเรียกใช้งาน API เหล่านี้โดยตรงจากแอปพลิเคชันหรือสคริปต์อัตโนมัติในเครื่องได้:

### 1) อัปโหลดและประมวลผล OCR + Auto-Extract
- **Endpoint:** `POST /api/ocr`
- **Content-Type:** `multipart/form-data`
- **Parameters:**
  - `file` (File, required): รูปภาพหรือไฟล์ PDF
  - `page` (int, default: 1): หน้าที่ต้องการอ่าน (กรณีเป็น PDF)
  - `process_all_pages` (bool, default: false): อ่านทุกหน้าพร้อมกันหรือไม่
  - `save_to_db` (bool, default: true): บันทึกลง SQLite พร้อมเก็บต้นฉบับใน `data/uploads/`
  - `category` (string, default: `"general"`): หมวดหมู่เอกสาร
  - `tags` (string, optional): แท็กคั่นด้วยจุลภาค
  - `doc_date` (string, optional): วันที่อ้างอิงเอกสาร (`YYYY-MM-DD`)
  - `auto_extract` (bool, optional): เปิด/ปิดการสกัดรายจ่ายและ auto-tag หลัง OCR (ค่าเริ่มต้นตาม `.env` `AUTO_EXTRACT_ON_INGEST`)
- **Response Example:**
```json
{
  "success": true,
  "doc_id": "b24aebe5-47e1-4a11-a6e0-5ab41dde35a3",
  "filename": "receipt.png",
  "is_pdf": false,
  "total_pages": 1,
  "image_width": 1024,
  "image_height": 1440,
  "text": "ข้อความภาษาไทยที่อ่านได้...",
  "lines": [
    {
      "text": "บันทึกข้อความ",
      "confidence": 0.985,
      "row": 0,
      "box": [[120, 50], [450, 50], [450, 90], [120, 90]],
      "page": 1
    }
  ],
  "stats": {
    "duration_ms": 380.5,
    "total_lines": 42,
    "avg_confidence": 0.968
  }
}
```

### 2) Vault: ค้นหา แก้ไข และดาวน์โหลดต้นฉบับเอกสาร
- `GET /api/documents` — ค้นหาและกรองเอกสารด้วย `query` (FTS5), `category`, `tag`, `limit`, `offset`
- `GET /api/documents/{doc_id}` — ดึงข้อมูลเอกสารฉบับเต็ม, พิกัด Bounding Boxes, Metadata และข้อมูลรายจ่ายที่ผูกอยู่
- `GET /api/documents/{doc_id}/file` — ดาวน์โหลดไฟล์ต้นฉบับที่เก็บไว้ใน `data/uploads/`
- `PATCH /api/documents/{doc_id}` — แก้ไข `text`, `category`, `tags`, `doc_date` พร้อม Re-index FTS5 อัตโนมัติ
- `DELETE /api/documents/{doc_id}` — ลบเอกสาร (และลบข้อมูล `expenses` / `documents_fts` รวมถึงไฟล์ต้นฉบับที่ผูกอยู่)

### 3) Watch Folder (`data/inbox/`)
- `GET /api/watcher/status` — ตรวจสอบสถานะ Background Watcher, จำนวนไฟล์รอ (`pending_count`) และไฟล์ล้มเหลว (`failed_count`)
- `POST /api/watcher/scan` — สั่งสแกนโฟลเดอร์ `data/inbox/` ทันที (ไฟล์ที่เสียหายจะถูกย้ายไปยัง `data/inbox_failed/` พร้อมไฟล์ `.error.txt`)

### 4) Hybrid Expense Extraction & Dashboard
- `POST /api/documents/{doc_id}/extract` — สกัดข้อมูลรายจ่ายจากเอกสาร (`extraction_method`: `auto`, `regex`, หรือ `llm` โดยส่งเฉพาะข้อความจาก OCR ไม่ส่งภาพ และ Fallback กลับเป็น Regex อัตโนมัติหาก LLM ล้มเหลว)
- `GET /api/expenses` — ดึงรายการรายจ่ายและยอดรวม KPI (`count`, `subtotal`, `vat_amount`, `total_amount`) รองรับตัวกรอง `month=YYYY-MM` และ `expense_category`
- `PUT /api/expenses/{doc_id}` — แก้ไข/บันทึกรายการรายจ่ายแบบ Manual
- `DELETE /api/expenses/{doc_id}` — ลบเฉพาะรายการรายจ่ายออกจากตารางสรุป (คงเอกสารไว้ในคลัง)
- `GET /api/expenses/export.csv` — ส่งออกรายการรายจ่ายเป็น CSV (UTF-8 with BOM เปิดใน Excel ภาษาไทยไม่เพี้ยน)

### 5) ระบบและสำรองข้อมูล
- `GET /api/system` — ข้อมูลโมเดล, สถิติฐานข้อมูล, สถานะ `llm_configured` และ `auto_extract_on_ingest`
- `GET /api/database/download` — ดาวน์โหลดไฟล์ `opentha_documents.db` สำหรับ Backup

---

## 6. พิมพ์เขียวสำหรับการต่อยอดเป็น Digital Product (Product Blueprints)

ฐานโค้ดนี้ถูกออกแบบให้เป็น **Headless & Decoupled Engine** คุณสามารถหยิบไปต่อยอดเป็นผลิตภัณฑ์ดิจิทัลได้หลากหลายรูปแบบ:

### 💡 ผลิตภัณฑ์ที่ 1: Document RAG & Chatbot สำหรับองค์กร (Enterprise AI Knowledge Base)
- **แนวคิด:** แปลงเอกสารระเบียบราชการ, คู่มือพนักงาน, หรือนโยบายบริษัทเป็น Vector Database
- **วิธีต่อยอด:**
  1. นำ `text_content` หรือแบ่งตาม `lines_json` มาทำ Semantic Chunking
  2. สร้าง Vector Embeddings (เช่น ใช้โมเดล `BAAI/bge-m3` หรือ `WangchanBERTa`)
  3. บันทึกลง SQLite Vector extension (sqlite-vec) หรือ ChromaDB / pgvector
  4. ทำ Chatbot ให้พนักงานถาม-ตอบเอกสารภายในได้โดยคำตอบอ้างอิงเลขหน้าและกรอบข้อความจริง

### 💡 ผลิตภัณฑ์ที่ 2: ระบบตรวจจับและดึงข้อมูลใบเสร็จ/ใบกำกับภาษี (Smart Accounting Parser Micro-SaaS)
- **แนวคิด:** SaaS สำหรับสำนักงานบัญชีที่ต้องคีย์ใบเสร็จและใบกำกับภาษีวันละหลายร้อยใบ
- **วิธีต่อยอด:**
  1. ใช้พิกัด `box` และข้อความที่ได้จากโมเดลส่งต่อให้ Small LLM (เช่น Typhoon หรือ Gemini Flash)
  2. สกัดฟิลด์สำคัญออกมาเป็น Structured JSON เช่น: เลขประจำตัวผู้เสียภาษี 13 หลัก, ยอดรวมก่อน VAT, VAT 7%, ยอดสุทธิ
  3. มีปุ่มกด Export เป็น Excel หรือยิงเข้าโปรแกรมบัญชี (FlowAccount, PeakEngine, Xero)

### 💡 ผลิตภัณฑ์ที่ 3: แอปพลิเคชัน Desktop ทำงานออฟไลน์ 100% (Offline Desktop Utility)
- **แนวคิด:** ซอฟต์แวร์ขายแบบ One-time purchase หรือรายปี สำหรับนักกฎหมาย, หน่วยงานรัฐ, หรือบุคคลทั่วไปที่ไม่ไว้ใจการส่งไฟล์ขึ้นคลาวด์
- **วิธีต่อยอด:**
  1. ห่อหุ้มโปรเจกต์นี้ด้วย **Tauri** หรือ **Electron**
  2. แพ็ก Python runtime + ONNX files รวมเป็นไฟล์ติดตั้ง `.dmg` (Mac) หรือ `.msi` (Windows)
  3. ผู้ใช้ติดตั้งและดับเบิลคลิกใช้งานได้ทันทีโดยไม่ต้องมีความรู้เรื่อง Terminal หรือ Python

### 💡 ผลิตภัณฑ์ที่ 4: REST API Gateway สำหรับสแกนเอกสารใน Workflow ขององค์กร
- **แนวคิด:** ทำเป็น OCR Microservice ภายในเครือข่าย Intranet ของบริษัท
- **วิธีต่อยอด:**
  1. เพิ่มระบบ API Key Authentication (`X-API-KEY`) และ Rate Limiting
  2. ต่อเชื่อมกับ Webhook แจ้งเตือนเมื่อประมวลผลไฟล์ขนาดใหญ่เสร็จสิ้น
  3. ผูกเข้ากับระบบ ERP หรือ Document Management System (DMS) เดิมขององค์กร

---

## 7. การนำไปขึ้นเซิร์ฟเวอร์และการ Deploy ด้วย Docker & Cloudflare Workers Paid

โปรเจกต์มี `Dockerfile` และ `wrangler.toml` พร้อมใช้งาน รองรับทั้ง Docker ในเครื่อง/VPS และ **Cloudflare Workers Paid (ค่าพื้นฐาน $5/เดือน พร้อมโควตาในตัว + R2 free tier 10 GB-month, 1M Class A writes, 10M Class B reads; ไม่ใช่เพดานค่าใช้จ่ายสูงสุดหากใช้งานเกินโควตา)** + Cloudflare Containers (`sleepAfter = "3m"`, `max_instances = 1`) + Private R2 Bucket (`VAULT_BUCKET` / `nong-chokchuay-vault`, ดูรายละเอียดใน `docs/adr/0003-cloudflare-r2-sqlite-persistence.md`):

```bash
# 1. Build & Run แบบ Docker
docker build -t nong-chokchuay-ocr .
docker run -d \
  -p 8765:8765 \
  --name nong-chokchuay \
  -v $(pwd)/data:/app/data \
  --restart unless-stopped \
  nong-chokchuay-ocr

# 2. สร้าง private R2 bucket (ครั้งแรกครั้งเดียว, ห้ามเปิด Public Access)
npx wrangler r2 bucket create nong-chokchuay-vault

# 3. ตั้งค่า Secrets และ Deploy ขึ้น Cloudflare (Bootstrap ครั้งแรกชั่วคราว)
npx wrangler secret put LINE_CHANNEL_SECRET
npx wrangler secret put LINE_CHANNEL_ACCESS_TOKEN
npx wrangler deploy --var R2_BOOTSTRAP_IF_EMPTY:true
# เปิด /api/health หรือหน้าเว็บ 1 ครั้งเพื่อสร้าง snapshot แรก แล้วล็อกกลับ:
npx wrangler deploy --var R2_BOOTSTRAP_IF_EMPTY:false
```

> **การตั้งค่า LINE Developers Webhook URL:** นำ URL ที่ได้ (เช่น `https://nong-chokchuay-ocr.entrace-space.workers.dev/api/line/webhook`) ไปวางใน LINE Official Account Messaging API และเปิด Use Webhook (LINE ระบุให้ใช้ `replyToken` เพียงครั้งเดียว **ภายใน 1 นาที** และควรตอบกลับให้เร็วที่สุด)

---

## 8. ประสิทธิภาพและข้อกำหนดทรัพยากร (Performance & Benchmarks)

| รายการ | สเปก / ผลการทดสอบ |
| :--- | :--- |
| **ขนาดโมเดลรวม** | **4.2 MB** (`det.onnx` 1.8 MB + `rec.onnx` 2.4 MB) |
| **หน่วยความจำที่ใช้ (RAM)** | ~400 - 500 MB ตลอดการทำงาน |
| **หน่วยประมวลผล (Hardware)** | **CPU ล้วน (ไม่จำเป็นต้องมี GPU)** |
| **ความเร็วต่อหน้า A4** | **~0.39 - 0.50 วินาที** (บนชิป Apple Silicon หรือ Intel Xeon) |
| **ปริมาณงานสูงสุด (Throughput)** | สูงถึง **~950 หน้าต่อนาที** บนซีพียู 8 Cores (เมื่อรัน Multi-workers) |
| **ความแม่นยำภาษาไทย** | เอกสารงบประมาณราชการ **99.5%**, ประกาศราชกิจจานุเบกษา **96.3%** |

---

## 9. ลิขสิทธิ์และการอ้างอิง (License & Attributions)

- **OpenThai 2.0 OCR Models:** พัฒนาและฝึกฝนโดยทีม **OpenThai & iApp Technology** เผยแพร่ภายใต้ลิขสิทธิ์ **Apache-2.0** (อนุญาตให้นำไปใช้ในเชิงพาณิชย์และต่อยอดพัฒนาต่อได้อย่างอิสระ)
  - โมเดลต้นฉบับบน Hugging Face: [iapp/OpenThai2.0-OCR-Text](https://huggingface.co/iapp/OpenThai2.0-OCR-Text)
  - โมเดล Markdown: [iapp/OpenThai2.0-OCR-Markdown](https://huggingface.co/iapp/OpenThai2.0-OCR-Markdown)
- **Base Architecture:** PaddlePaddle PP-OCRv6-tiny (Apache-2.0)
- **OCR Application & Full-Text Vault:** Open-source ภายใต้ลิขสิทธิ์ **MIT License**
