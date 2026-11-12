# 🇹🇭 OpenThai 2.0 OCR Studio & Document Vault

> **ระบบแปลงภาพและเอกสาร PDF ภาษาไทยเป็นข้อความอัตโนมัติ (AI OCR) พร้อมคลังฐานข้อมูล Full-Text Search ในเครื่อง (On-Premise / 100% Offline & Private)**  
> พัฒนาต่อยอดจากโมเดล Open-weights **OpenThai 2.0 OCR** โดย **iApp Technology** ออกแบบสถาปัตยกรรมแบบ Modular เพื่อให้พร้อมนำไปพัฒนาต่อยอดเป็น **Digital Products, Micro-SaaS หรือระบบ RAG สำหรับองค์กร** ได้ทันที

---

## 📌 สารบัญ

- [1. ภาพรวมระบบ (System Overview)](#1-ภาพรวมระบบ-system-overview)
- [2. โครงสร้างโปรเจกต์ (Project Structure)](#2-โครงสร้างโปรเจกต์-project-structure)
- [3. วิธีเริ่มใช้งานแบบรวดเร็ว (Quick Start)](#3-วิธีเริ่มใช้งานแบบรวดเร็ว-quick-start)
- [4. สถาปัตยกรรมฐานข้อมูลและการค้นหา (Database & Full-Text Search)](#4-สถาปัตยกรรมฐานข้อมูลและการค้นหา-database--full-text-search)
- [5. รายละเอียด REST API (API Reference)](#5-รายละเอียด-rest-api-api-reference)
- [6. พิมพ์เขียวสำหรับการต่อยอดเป็น Digital Product (Product Blueprints)](#6-พิมพ์เขียวสำหรับการต่อยอดเป็น-digital-product-product-blueprints)
- [7. การนำไปขึ้นเซิร์ฟเวอร์และการ Deploy ด้วย Docker](#7-การนำไปขึ้นเซิร์ฟเวอร์และการ-deploy-ด้วย-docker)
- [8. ประสิทธิภาพและข้อกำหนดทรัพยากร (Performance & Benchmarks)](#8-ประสิทธิภาพและข้อกำหนดทรัพยากร-performance--benchmarks)
- [9. ลิขสิทธิ์และการอ้างอิง (License & Attributions)](#9-ลิขสิทธิ์และการอ้างอิง-license--attributions)

---

## 1. ภาพรวมระบบ (System Overview)

ระบบนี้รวมเอาโมเดล **OpenThai 2.0 OCR Text (ONNX)** ขนาดจิ๋วเพียง **4.2 MB** เข้ากับ Web Server สมรรถนะสูง (FastAPI) และ SQLite ฐานข้อมูลในตัวที่มีความสามารถ Trigram FTS5 Full-Text Search:

```
[ เอกสารต้นฉบับ ] ---> [ pypdfium2 / Pillow ] ---> [ OpenThai 2.0 OCR (ONNX) ]
 (PDF / รูปภาพ)           (แปลงภาพ 300 DPI)           - PP-OCRv6-tiny Detector (1.8MB)
                                                      - Thai CTC Recognizer (2.4MB)
                                                                 │
                                                                 ▼
                                                  [ ข้อความ + Bounding Boxes ]
                                                                 │
                                        ┌────────────────────────┴────────────────────────┐
                                        ▼                                                 ▼
                          [ Interactive Web UI ]                             [ SQLite Database ]
                         - ชี้เมาส์ดู Bounding Boxes                         - ตาราง documents (Metadata + JSON)
                         - คัดลอก / Export (.txt, .json, .md)                - FTS5 Trigram Full-Text Search
                         - แสดงผลความแม่นยำรายบรรทัด                          - ค้นหาคำภาษาไทยได้ทันที
```

### จุดเด่นเชิงเทคนิค
- **ความปลอดภัยระดับสูงสุด (Zero Data Leak):** ประมวลผลบน CPU เครื่องตัวเอง ไม่ส่งข้อมูลออกอินเทอร์เน็ต เหมาะกับเอกสารลับทางราชการ, บัญชีการเงิน, โรงพยาบาล หรือสัญญาธุรกิจ
- **ไม่ต้องพึ่งพา GPU:** โมเดลทำงานผ่าน `onnxruntime` รันบน CPU ปกติ (Apple Silicon M-Series หรือ Intel/AMD x86_64) ได้อย่างลื่นไหล
- **ความเร็ว:** เฉลี่ย ~0.3 - 0.5 วินาทีต่อหน้าเอกสาร A4
- **รองรับ Multi-Page PDF:** แปลงไฟล์ PDF ทั้งหน้าหรือ Batch ทุกหน้าในคลิกเดียว

---

## 2. โครงสร้างโปรเจกต์ (Project Structure)

```
openthai-ocr-app/
├── app.py                      # FastAPI Backend (Endpoints สำหรับ OCR, ค้นหา, จัดการเอกสาร)
├── database.py                 # SQLite ORM & Full-Text Search (Trigram FTS5)
├── start.sh                    # Shell script สำหรับเปิดแอปและเบราว์เซอร์อัตโนมัติ
├── Dockerfile                  # สำหรับ Build เป็น Docker Container ขึ้น Cloud/VPS
├── requirements.txt            # รายการ Dependencies ทั้งหมด
├── README.md                   # คู่มือทางเทคนิคและแนวทางการต่อยอด
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
│   ├── index.html              # โครงสร้างหน้าเว็บพร้อมระบบ Modal และ Canvas
│   ├── styles.css              # Apple/Linear Design System สำหรับ Desktop Web
│   └── app.js                  # Logic จัดการ Drag-and-drop, Bounding box canvas, API calls
│
├── samples/                    # เอกสารตัวอย่างสำหรับทดสอบ
│   ├── sample_official_document.png  # บันทึกข้อความราชการ & ตาราง
│   └── sample_budget_table.png       # รายงานงบประมาณรายจ่าย
│
├── data/                       # โฟลเดอร์เก็บข้อมูล (Persistent Data)
│   ├── documents.db            # ฐานข้อมูล SQLite (เก็บข้อความ, พิกัด Bounding Box, FTS)
│   └── uploads/                # เก็บไฟล์เอกสารที่อัปโหลด
│
└── .venv/                      # Python 3.11 Virtual Environment
```

---

## 3. วิธีเริ่มใช้งานแบบรวดเร็ว (Quick Start)

### วิธีที่ 1: รันผ่านสคริปต์อัตโนมัติ (macOS / Linux)

```bash
cd /Users/chalisa/openthai-ocr-app
./start.sh
```
สคริปต์จะเริ่มเซิร์ฟเวอร์ที่ `http://127.0.0.1:8765` และเปิดหน้าเว็บให้โดยอัตโนมัติ

### วิธีที่ 2: รันผ่านคำสั่ง Python โดยตรง

```bash
cd /Users/chalisa/openthai-ocr-app
source .venv/bin/activate
uvicorn app:app --host 127.0.0.1 --port 8765 --reload
```

---

## 4. สถาปัตยกรรมฐานข้อมูลและการค้นหา (Database & Full-Text Search)

ระบบใช้ **SQLite 3** ร่วมกับ Extension **FTS5 Trigram Tokenizer** ซึ่งมีความพิเศษคือ **สามารถค้นหาคำย่อย (Substring) ในภาษาไทยที่ไม่มีการเว้นวรรคได้อย่างแม่นยำและรวดเร็ว**:

### ตารางหลัก (`documents`)
| Column | Type | คำอธิบาย |
| :--- | :--- | :--- |
| `id` | TEXT (UUID) | Primary Key ประจำเอกสาร |
| `filename` | TEXT | ชื่อไฟล์เอกสารต้นฉบับ |
| `file_type` | TEXT | ชนิดไฟล์ (`image` หรือ `pdf`) |
| `file_size` | INTEGER | ขนาดไฟล์ (Bytes) |
| `total_pages`| INTEGER | จำนวนหน้าเอกสาร |
| `text_content`| TEXT | ข้อความเต็มที่อ่านได้ทั้งหมด เรียงตาม Reading order |
| `lines_json` | TEXT | ข้อมูล JSON ของแต่ละบรรทัด (ข้อความ, ความมั่นใจ, พิกัด `box` 4 มุม) |
| `avg_confidence`| REAL | คะแนนความมั่นใจเฉลี่ย (0.00 - 1.00) |
| `duration_ms`| REAL | เวลาที่ใช้ประมวลผล (มิลลิวินาที) |
| `image_preview`| TEXT | รูปภาพหน้าแรกแบบ Base64 (สำหรับแสดงผล Bounding Boxes) |
| `created_at` | TEXT | วันที่และเวลาที่สแกนเอกสาร |

### ตารางค้นหาเสมือน (`documents_fts`)
```sql
CREATE VIRTUAL TABLE documents_fts USING fts5(
    id UNINDEXED,
    filename,
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

คุณสามารถนำ API เหล่านี้ไปเชื่อมต่อกับ Frontend อื่นๆ เช่น React, Vue, Next.js, หรือ Mobile App (Flutter / Swift / Kotlin) ได้ทันที:

### 1) อัปโหลดและประมวลผล OCR
- **Endpoint:** `POST /api/ocr`
- **Content-Type:** `multipart/form-data`
- **Parameters:**
  - `file` (File, required): รูปภาพหรือไฟล์ PDF
  - `page` (int, default: 1): หน้าที่ต้องการอ่าน (กรณีเป็น PDF)
  - `process_all_pages` (bool, default: false): อ่านทุกหน้าพร้อมกันหรือไม่
  - `save_to_db` (bool, default: true): บันทึกลง SQLite ให้อัตโนมัติหรือไม่
- **Response Example:**
```json
{
  "success": true,
  "doc_id": "b24aebe5-47e1-4a11-a6e0-5ab41dde35a3",
  "filename": "document.pdf",
  "is_pdf": true,
  "total_pages": 3,
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

### 2) ค้นหาและดึงรายการเอกสารในคลัง
- **Endpoint:** `GET /api/documents`
- **Query Parameters:**
  - `query` (string, optional): คำค้นภาษาไทยหรืออังกฤษ
  - `limit` (int, default: 50)
  - `offset` (int, default: 0)
- **Response:** คืนค่า Array ของเอกสาร พร้อม Text Snippet และสถิติรวมของคลัง

### 3) ดึงข้อมูลเอกสารรายฉบับ
- **Endpoint:** `GET /api/documents/{doc_id}`
- **Response:** คืนค่าข้อมูลฉบับเต็ม ทั้งข้อความ พิกัด Bounding Boxes และภาพตัวอย่าง

### 4) ลบเอกสาร
- **Endpoint:** `DELETE /api/documents/{doc_id}`

### 5) สำรองข้อมูลฐานข้อมูล
- **Endpoint:** `GET /api/database/download`
- **Response:** ดาวน์โหลดไฟล์ `opentha_documents.db` สำหรับย้ายเครื่องหรือ Backup

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

## 7. การนำไปขึ้นเซิร์ฟเวอร์และการ Deploy ด้วย Docker

โปรเจกต์มี `Dockerfile` พร้อมใช้งาน คุณสามารถ Build และนำไปรันบน Server เช่น Ubuntu VPS, AWS EC2, GCP Compute Engine หรือ Cloud Container Platform ได้ทันที:

```bash
# 1. Build Docker image
docker build -t openthai-ocr-app .

# 2. Run Container (Map พอร์ต 8765 และ Mount โฟลเดอร์ data เพื่อเก็บฐานข้อมูลถาวร)
docker run -d \
  -p 8765:8765 \
  --name openthai-ocr \
  -v $(pwd)/data:/app/data \
  --restart unless-stopped \
  openthai-ocr-app
```

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
