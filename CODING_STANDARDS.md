# CODING_STANDARDS.md — มาตรฐานโค้ดและการรีวิว Diff

เอกสารนี้กำหนดมาตรฐานการเขียนโค้ดและการตรวจทานการเปลี่ยนแปลง (Diff Review) สำหรับโปรเจกต์ **"น้องโชคช่วย" (Nong ChokChuay) / OpenThai 2.0 OCR App**

---

## 1. โครงสร้างโค้ดและการรักษาความเรียบง่าย

- **ใช้ Python 3.11+ และไลบรารีมาตรฐานเป็นหลัก:** เพิ่ม dependency ใหม่ใน `requirements.txt` เฉพาะเมื่อจำเป็นจริง และต้องไม่ทำให้ Docker image หรือเวลาเริ่มทำงานของ Cloudflare Container บวมเกินงบประมาณ
- **แยกบทบาทแต่ละโมดูลให้ชัด:**
  - `app.py` รับผิดชอบ HTTP routing, การแปลงค่า input/output และการจับ error ระดับ API
  - `ocr_service.py` เป็นตัวกลางเดียวสำหรับตรรกะการนำเข้าเอกสาร (`web_upload`, `watch_folder`, `line_oa`, `api`) ห้ามเขียนตรรกะนำเข้าซ้ำแยกตามช่องทาง
  - `database.py` รับผิดชอบคำสั่ง SQL, FTS5 และการแปลงแถวฐานข้อมูลเป็น dict
  - `extractor.py` รับผิดชอบการสกัดข้อมูลจากข้อความ OCR เท่านั้น
- **ใช้คำศัพท์ตรงตาม `GLOSSARY.md`:** ชื่อตัวแปร ฟิลด์ใน JSON คอลัมน์ในฐานข้อมูล และข้อความในเอกสารต้องสอดคล้องกับคำศัพท์ที่กำหนดไว้

---

## 2. ฐานข้อมูล SQLite และ Schema Migration

- **ห้ามทำลายข้อมูลเดิม:** การเพิ่มตารางหรือคอลัมน์ใหม่ใน `database.py` ต้องทำผ่าน migration ที่รันซ้ำได้อย่างปลอดภัย (idempotent) โดยตรวจด้วย `PRAGMA table_info(...)` ก่อนสั่ง `ALTER TABLE`
- **ป้องกัน SQL Injection เสมอ:** ใช้ parameterized query (`?`) ทุกจุดที่รับค่าจากผู้ใช้หรือจาก OCR ห้ามต่อสตริง SQL โดยตรง
- **รักษาความสอดคล้องของ FTS5:** เมื่อเพิ่ม แก้ไข หรือลบข้อมูลในตาราง `documents` ต้องอัปเดตดัชนีค้นหา `documents_fts` ให้ตรงกันเสมอ
- **การส่งออกไฟล์ CSV ภาษาไทย:** ต้องใส่ UTF-8 BOM (`\ufeff`) เสมอเพื่อให้เปิดใน Microsoft Excel บน Windows/macOS แล้วภาษาไทยไม่เพี้ยน

---

## 3. ข้อมูลจาก AI, ความเป็นส่วนตัว และระดับความน่าเชื่อถือ

- **ห้ามส่งไฟล์ภาพหรือ PDF ออกนอกเซิร์ฟเวอร์:** การเรียก LLM ภายนอก (`extractor.py`) ต้องส่งเฉพาะข้อความ (`full_text` / `lines`) ที่ผ่านการอ่านจากโมเดล ONNX ในเครื่องแล้วเท่านั้น
- **ต้องมี Fallback เสมอ:** หากไม่ได้ตั้งค่า `LLM_API_KEY` หรือการเรียก LLM ล้มเหลว/หมดเวลา ระบบต้องถอยกลับมาใช้ตัวสกัด Offline Thai Regex (`extraction_method = 'regex'`) โดยไม่ทำให้การนำเข้าเอกสารล้มเหลว
- **ระบุที่มาของข้อมูลให้ครบ:**
  - บันทึก `ingestion_channel`, `extraction_method` (`regex`, `llm`, `manual`), `confidence`, และ `review_status` (`pending_review`, `reviewed`) อย่างชัดเจน
  - เมื่อผู้ใช้แก้ไขหรือยืนยันข้อมูลด้วยตนเอง ต้องปรับ `extraction_method = 'manual'` หรือ `review_status = 'reviewed'` พร้อมอัปเดตเวลา `updated_at`

---

## 4. ค่าลับและความปลอดภัย (Secrets & Security)

- **ห้ามเก็บค่าลับในโค้ดหรือ Git:** อ่านค่าลับ (`LINE_CHANNEL_SECRET`, `LINE_CHANNEL_ACCESS_TOKEN`, `LLM_API_KEY`, `GEMINI_API_KEY`) ผ่าน `config.py` จาก Environment Variables หรือ `.env` เท่านั้น
- **ห้ามเปิดเผยค่าลับใน API หรือ Log:** Endpoint สำหรับแสดงสถานะระบบ (เช่น `/api/health`, `/api/stats`, `/api/line/config`) ให้ส่งออกเฉพาะค่า boolean ว่าตั้งค่าแล้วหรือไม่ (เช่น `secret_configured: true/false`) ห้ามส่งค่าจริงออกไปยังฝั่งเบราว์เซอร์
- **ตรวจสอบลายเซ็น LINE Webhook:** `/api/line/webhook` ต้องตรวจ `X-Line-Signature` ด้วย HMAC-SHA256 เสมอเมื่อตั้งค่า `LINE_CHANNEL_SECRET`
- **ความปลอดภัยฝั่ง Frontend (`static/`):** เมื่อนำข้อความจาก OCR หรือข้อมูลผู้ใช้ไปแสดงผลใน DOM ต้องใช้ `textContent` หรือฟังก์ชัน escape HTML เสมอเพื่อป้องกัน XSS

---

## 5. การเขียนและรันชุดทดสอบ (Testing Standards)

- **แยกพื้นที่ทดสอบออกจากข้อมูลจริง 100%:** ชุดทดสอบใน `tests/` ต้องใช้ `tempfile.TemporaryDirectory()` สำหรับ `AppConfig.from_dir(...)` เสมอ ห้ามอ่านหรือเขียนทับ `data/documents.db` หรือ `data/uploads/` ของจริง
- **ไม่พึ่งพาเครือข่ายภายนอกใน Unit Test:** การทดสอบ LLM adapter หรือ LINE Messaging API ต้องจำลอง (mock/stub) การเรียก HTTP ภายนอก เพื่อให้รันผ่านได้ทั้งในเครื่องและบน GitHub Actions CI โดยไม่ต้องใช้ API key จริง
- **ทุกการแก้ไขตรรกะต้องมีผลทดสอบรองรับ:** ก่อนเปิดหรืออัปเดต PR ต้องรันทั้งคำสั่งตรวจ syntax (`py_compile`) และชุดทดสอบทั้งหมด (`./.venv/bin/python -m unittest discover -s tests -v`) ให้ผ่านครบทุกข้อ

---

## 6. รายการตรวจทาน Diff ก่อนส่งงาน (Diff Review Checklist)

1. มีไฟล์ค่าลับ (`.env`), ฐานข้อมูล (`*.db`), หรือไฟล์ชั่วคราวหลุดเข้า `git status` หรือไม่?
2. โค้ดที่แก้กระทบช่องทางนำเข้าอื่น (`web_upload`, `watch_folder`, `line_oa`, `api`) หรือไม่ และได้ตรวจครบทุกช่องทางแล้วหรือยัง?
3. มีการเพิ่ม ย้าย หรือเปลี่ยนหน้าที่ของไฟล์ที่ต้องอัปเดตใน `INDEX.md` และ `docs/STATUS.md` หรือไม่?
4. การเปลี่ยนแปลงนี้ขัดแย้งกับ `docs/adr/0001` หรือ `docs/adr/0002` หรือไม่? (หากขัดแย้ง ต้องหยุดและถามเจ้าของโปรเจกต์ก่อน)
